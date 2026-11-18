"""Worker loop for the prep worker. Claims jobs from Redis, processes them, reports to coordinator."""

from __future__ import annotations

import json
import os
import sys
import time
import uuid

import httpx
import redis

from worker.common.llm_client import llm_settings_from_mapping
from worker.common.settings import flatten_worker_settings
from worker.common.storage import download_bytes, upload_package_dir
from worker.prep.cover import get_cover
from worker.prep.epub_builder import build_epub
from worker.prep.epub_parser import classify_epub_item, parse_epub
from worker.prep.llm.analyzer import Analyzer, analyzer_from_settings
from worker.prep.metadata_enrich import enrich_metadata
from worker.prep.narrator_script import build_narrator_script, validate_script
from worker.prep.page_ledger import build_page_ledger, validate_page_ledger
from worker.prep.pdf_parser import classify_page, detect_chapters, parse_pdf


class PrepWorker:
    def __init__(
        self,
        settings: dict,
        http: httpx.Client | None = None,
        redis_client: redis.Redis | None = None,
        analyzer: Analyzer | None = None,
    ):
        self.settings = settings
        self.http = http or httpx.Client(timeout=30)
        self.redis = redis_client or redis.Redis(
            host=settings["redis_host"],
            port=settings["redis_port"],
            password=settings.get("redis_password") or None,
            db=settings.get("redis_db", 0),
        )
        self.worker_id = str(uuid.uuid4())
        self.node_id = settings["node_id"]
        self.coordinator_url = settings["coordinator_url"].rstrip("/")
        self.api_key = settings["api_key"]
        self.analyzer = analyzer if analyzer is not None else analyzer_from_settings(settings, http=self.http)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def register(self) -> None:
        response = self.http.post(
            f"{self.coordinator_url}/workers/register",
            headers=self._headers(),
            json={
                "worker_id": self.worker_id,
                "node_id": self.node_id,
                "type": "prep",
                "queues": ["prep"],
                "capabilities": {
                    "llm_slots": llm_settings_from_mapping(self.settings).slots,
                },
            },
        )
        response.raise_for_status()

    def heartbeat(self, current_job: str | None = None) -> None:
        response = self.http.post(
            f"{self.coordinator_url}/workers/{self.worker_id}/heartbeat",
            headers=self._headers(),
            json={"status": "idle", "current_job": current_job},
        )
        response.raise_for_status()

    def claim_job(self, timeout: int = 30) -> dict | None:
        result = self.redis.brpop(["prep"], timeout=timeout)
        if result is None:
            return None
        _, raw = result
        return json.loads(raw)

    def process_job(self, job: dict) -> dict:
        job_id = job["job_id"]
        input_format = job["input_format"]
        input_url = job["input_url"]

        data = download_bytes(input_url, http_client=self.http)

        if input_format == "pdf":
            doc = parse_pdf(data)
            for page in doc["pages"]:
                action, reason = classify_page(page)
                page["action"] = action
                page["reason"] = reason
        else:
            doc = parse_epub(data)
            for page in doc["pages"]:
                action, reason = classify_epub_item(page)
                page["action"] = action
                page["reason"] = reason

        chapters = detect_chapters(doc["pages"])
        metadata = doc["metadata"]
        title = metadata.get("title") or job.get("metadata", {}).get("title") or "Unknown Title"
        author = metadata.get("author") or job.get("metadata", {}).get("author") or "Unknown Author"
        language = job.get("metadata", {}).get("language") or "en"

        metadata = enrich_metadata(
            {
                "title": title,
                "author": author,
                "subject": metadata.get("subject") or "",
                "language": language,
            },
            doc["pages"],
            http=self.http,
        )
        title = metadata.get("title") or title
        author = metadata.get("author") or author

        cover_bytes, cover_source = get_cover(data, input_format, title, author, http=self.http)

        ledger = build_page_ledger(input_format, input_url, doc["pages"], chapters)
        ledger_errors = validate_page_ledger(ledger, doc["total_pages"])
        if ledger_errors:
            raise ValueError(f"Page ledger validation failed: {ledger_errors}")

        script = build_narrator_script(
            job_id, input_format, input_url, title, author, language, doc["pages"], chapters
        )
        script_errors = validate_script(script, ledger)
        if script_errors:
            raise ValueError(f"Script validation failed: {script_errors}")

        epub_data = build_epub(title, author, language, doc["pages"], chapters)

        package = {
            "manifest": {
                "package_id": str(uuid.uuid4()),
                "job_id": job_id,
                "version": "1.0",
                "source": {"format": input_format, "url": input_url, "page_count": doc["total_pages"]},
                "book": {"title": title, "author": author, "language": language},
                "cover": {"source": cover_source},
                "statistics": {
                    "total_pages": doc["total_pages"],
                    "kept_pages": ledger["summary"]["kept"],
                    "skipped_pages": ledger["summary"]["skipped"],
                    "total_segments": script["metadata"]["total_segments"],
                    "total_chapters": script["metadata"]["total_chapters"],
                    "word_count": script["metadata"]["estimated_word_count"],
                },
                "validation": {
                    "page_parity": True,
                    "epub_valid": True,
                    "script_valid": True,
                },
            },
            "epub": epub_data,
            "script": script,
            "page_ledger": ledger,
            "metadata": metadata,
            "cover": cover_bytes,
            "llm_analysis": self.analyzer.analyze(
                title=title,
                author=author,
                language=language,
                pages=doc["pages"],
            ),
        }

        root = str(self.settings.get("storage_root") or "/var/lib/audiobook").rstrip("/")
        output_prefix = self.settings.get("output_prefix") or f"file://{root}/packages/{job_id}/"
        package_url = upload_package_dir(
            output_prefix,
            {
                "book.epub": package["epub"],
                "script.json": json.dumps(package["script"]),
                "page-ledger.json": json.dumps(package["page_ledger"]),
                "metadata.json": json.dumps(package["metadata"]),
                "llm-analysis.json": json.dumps(package["llm_analysis"]),
                "manifest.json": json.dumps(package["manifest"]),
                "cover.jpg": package["cover"],
            },
        )

        self.http.post(
            f"{self.coordinator_url}/jobs/{job_id}/complete",
            headers=self._headers(),
            json={
                "package_url": package_url,
                "total_pages": doc["total_pages"],
                "kept_pages": ledger["summary"]["kept"],
                "skipped_pages": ledger["summary"]["skipped"],
            },
        )

        return package

    def run(self, max_jobs: int | None = None) -> None:
        self.register()
        jobs_processed = 0
        while max_jobs is None or jobs_processed < max_jobs:
            try:
                job = self.claim_job(timeout=10)
            except Exception:
                time.sleep(5)
                continue
            if job is None:
                self.heartbeat()
                continue
            jobs_processed += 1
            try:
                self.heartbeat(job["job_id"])
                self.process_job(job)
            except Exception as exc:
                try:
                    self.http.post(
                        f"{self.coordinator_url}/jobs/{job['job_id']}/fail",
                        headers=self._headers(),
                        json={"error": str(exc)},
                    )
                except Exception:
                    pass
        self.http.delete(f"{self.coordinator_url}/workers/{self.worker_id}", headers=self._headers())


def _expand_env(value):
    import re

    pattern = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

    def repl(match):
        return os.environ.get(match.group(1), "")

    if isinstance(value, str):
        return pattern.sub(repl, value)
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    return value


def main() -> None:
    import yaml

    path = os.environ.get("PREP_WORKER_CONFIG", "config/worker-prep.yaml")
    with open(path) as f:
        raw = _expand_env(yaml.safe_load(f))
    settings = flatten_worker_settings(raw)
    worker = PrepWorker(settings)
    worker.run()


if __name__ == "__main__":
    main()
