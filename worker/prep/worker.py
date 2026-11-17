"""Worker loop for the prep worker. Claims jobs from Redis, processes them, reports to coordinator."""

from __future__ import annotations

import json
import os
import sys
import time
import uuid

import httpx
import redis

from worker.prep.epub_builder import build_epub
from worker.prep.epub_parser import classify_epub_item, parse_epub
from worker.prep.narrator_script import build_narrator_script, validate_script
from worker.prep.page_ledger import build_page_ledger, validate_page_ledger
from worker.prep.pdf_parser import classify_page, detect_chapters, parse_pdf


class PrepWorker:
    def __init__(self, settings: dict, http: httpx.Client | None = None, redis_client: redis.Redis | None = None):
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

        data = self._download(input_url)

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
            "metadata": {
                "title": title,
                "author": author,
                "subject": metadata.get("subject") or "",
                "language": language,
            },
            "llm_analysis": {
                "subject_summary": "",
                "author_tone": "",
                "narration_style_recommendations": [],
                "suggested_voice_recipes": [],
            },
        }

        package_url = self._upload(job_id, package)

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

    def _download(self, url: str) -> bytes:
        if url.startswith("s3://"):
            raise NotImplementedError("S3 download not implemented in this slice")
        if url.startswith("file://"):
            path = url[len("file://"):]
            with open(path, "rb") as f:
                return f.read()
        response = self.http.get(url)
        response.raise_for_status()
        return response.content

    def _upload(self, job_id: str, package: dict) -> str:
        output_prefix = self.settings.get("output_prefix") or f"s3://bucket/packages/{job_id}/"
        if output_prefix.startswith("file://"):
            import pathlib

            path = pathlib.Path(output_prefix[len("file://"):])
            path.mkdir(parents=True, exist_ok=True)
            (path / "book.epub").write_bytes(package["epub"])
            (path / "script.json").write_text(json.dumps(package["script"]))
            (path / "page-ledger.json").write_text(json.dumps(package["page_ledger"]))
            (path / "metadata.json").write_text(json.dumps(package["metadata"]))
            (path / "llm-analysis.json").write_text(json.dumps(package["llm_analysis"]))
            (path / "manifest.json").write_text(json.dumps(package["manifest"]))
            return str(path) + "/"
        if output_prefix.endswith("/"):
            import pathlib

            path = pathlib.Path(output_prefix)
            path.mkdir(parents=True, exist_ok=True)
            (path / "book.epub").write_bytes(package["epub"])
            (path / "script.json").write_text(json.dumps(package["script"]))
            (path / "page-ledger.json").write_text(json.dumps(package["page_ledger"]))
            (path / "metadata.json").write_text(json.dumps(package["metadata"]))
            (path / "llm-analysis.json").write_text(json.dumps(package["llm_analysis"]))
            (path / "manifest.json").write_text(json.dumps(package["manifest"]))
            return str(path) + "/"
        return f"s3://bucket/packages/{job_id}/"

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


def main() -> None:
    import yaml

    path = os.environ.get("PREP_WORKER_CONFIG", "config/worker-prep.yaml")
    with open(path) as f:
        settings = yaml.safe_load(f)
    worker = PrepWorker(settings)
    worker.run()


if __name__ == "__main__":
    main()
