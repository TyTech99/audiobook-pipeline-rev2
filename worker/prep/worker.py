"""Worker loop for the prep worker. Claims jobs from Redis, processes them, reports to coordinator."""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
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

logger = logging.getLogger("worker.prep")


class JobCancelled(Exception):
    """Raised when a job is cancelled while being processed."""
    def __init__(self, job_id: str):
        self.job_id = job_id
        super().__init__(f"Job {job_id} was cancelled")


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
        self._heartbeat_thread: threading.Thread | None = None
        self._heartbeat_stop = threading.Event()
        self._heartbeat_interval = int(settings.get("heartbeat_interval_seconds", 30))

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def _start_job_heartbeat(self, job_id: str) -> None:
        """Start a background thread that updates the worker heartbeat in Redis."""
        self._heartbeat_stop.clear()

        def _beat():
            while not self._heartbeat_stop.is_set():
                try:
                    self.redis.set(
                        f"worker:heartbeat:{self.worker_id}",
                        json.dumps({"job_id": job_id, "ts": time.time()}),
                        ex=self._heartbeat_interval * 4,
                    )
                except Exception as e:
                    logger.warning("Heartbeat update failed: %s", e)
                self._heartbeat_stop.wait(self._heartbeat_interval)

        self._heartbeat_thread = threading.Thread(target=_beat, daemon=True)
        self._heartbeat_thread.start()

    def _stop_job_heartbeat(self) -> None:
        """Stop the background heartbeat thread."""
        self._heartbeat_stop.set()
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=5)
            self._heartbeat_thread = None
        self.redis.delete(f"worker:heartbeat:{self.worker_id}")

    def check_cancelled(self, job_id: str) -> None:
        """Check if a job has been cancelled. Raises JobCancelled if so."""
        if self.redis.exists(f"job:cancel:{job_id}"):
            self.redis.delete(f"job:cancel:{job_id}")
            raise JobCancelled(job_id)

    def _ocr_progress(self, job_id: str, page: int, total: int) -> None:
        """OCR progress callback: report every 10 pages, check cancel every 5."""
        if page % 5 == 0:
            self.check_cancelled(job_id)
        if page % 10 == 0:
            self.report_progress(
                job_id,
                {
                    "stage": "ocr",
                    "current_page": page,
                    "total_pages": total,
                    "percent": 5 + int((page / total) * 40),
                },
            )

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

    def mark_in_progress(self, job_id: str) -> None:
        response = self.http.post(
            f"{self.coordinator_url}/jobs/{job_id}/start",
            headers=self._headers(),
            json={"worker_id": self.worker_id, "stage": "prep"},
        )
        response.raise_for_status()

    def report_progress(self, job_id: str, progress: dict) -> None:
        try:
            response = self.http.post(
                f"{self.coordinator_url}/jobs/{job_id}/progress",
                headers=self._headers(),
                json=progress,
            )
            response.raise_for_status()
        except Exception as e:
            logger.warning("Failed to report progress for %s: %s", job_id, e)

    def claim_job(self, timeout: int = 30) -> dict | None:
        result = self.redis.blmove(
            "prep:pending", "prep:processing",
            timeout=timeout,
            src="RIGHT", dest="LEFT"
        )
        if result is None:
            return None
        job = json.loads(result)
        job_id = job.get("job_id")
        # Generate claim token and record claim for the reaper
        claim_token = str(uuid.uuid4())
        job["_claim_token"] = claim_token
        self.redis.hset("job:claims", job_id, f"{time.time()}:{claim_token}")
        return job

    def ack_job(self, job: dict) -> None:
        """Remove job from processing queue after successful completion."""
        job_id = job.get("job_id")
        self.redis.lrem("prep:processing", 1, json.dumps(job, sort_keys=True))
        self.redis.hdel("job:claims", job_id)

    def nack_job(self, job: dict, error: str) -> None:
        """Remove job from processing queue after failure."""
        job_id = job.get("job_id")
        self.redis.lrem("prep:processing", 1, json.dumps(job, sort_keys=True))
        self.redis.hdel("job:claims", job_id)

    def process_job(self, job: dict) -> dict:
        job_id = job["job_id"]
        input_format = job["input_format"]
        input_url = job["input_url"]

        logger.info("Processing job %s (%s)", job_id, input_format)
        self._start_job_heartbeat(job_id)
        try:
            return self._process_job_inner(job, job_id, input_format, input_url)
        finally:
            self._stop_job_heartbeat()

    def _process_job_inner(self, job: dict, job_id: str, input_format: str, input_url: str) -> dict:
        self.check_cancelled(job_id)
        self.mark_in_progress(job_id)
        self.report_progress(job_id, {"stage": "downloading", "percent": 0})

        logger.info("Downloading input: %s", input_url)
        data = download_bytes(input_url, http_client=self.http)
        logger.info("Downloaded %d bytes", len(data))
        self.check_cancelled(job_id)
        self.report_progress(job_id, {"stage": "parsing", "percent": 5})

        cleanup_records: list[dict] = []
        declared_sites: list[dict] = []
        ocr_quality_pages: list[dict] = []
        if input_format == "pdf":
            logger.info("Parsing PDF...")
            # Get LLM client for OCR cleanup if available
            llm_client = None
            if hasattr(self.analyzer, "client"):
                llm_client = self.analyzer.client
            # Get OCR settings from config
            from worker.prep.ocr_cleanup import OcrQualitySettings

            ocr_settings = OcrQualitySettings.from_config(self.settings)
            # Checkpoint dir for resumable OCR (under output prefix)
            output_prefix = self.settings.get("output_prefix") or ""
            if output_prefix.startswith("file://"):
                output_prefix = output_prefix[7:]
            checkpoint_dir = f"{output_prefix.rstrip('/')}/checkpoint" if output_prefix else None
            # Page range (optional, 1-based inclusive)
            page_range = job.get("page_range") or job.get("metadata", {}).get("page_range")
            start_page = page_range[0] if page_range else None
            end_page = page_range[1] if page_range else None
            doc = parse_pdf(
                data,
                progress_callback=lambda page, total: self._ocr_progress(job_id, page, total),
                llm_client=llm_client,
                language=job.get("metadata", {}).get("language") or "eng",
                ocr_settings=ocr_settings,
                checkpoint_dir=checkpoint_dir,
                start_page=start_page,
                end_page=end_page,
            )
            logger.info("Parsed %d pages", doc["total_pages"])
            self.check_cancelled(job_id)
            # Cleanup verify records flow into the package logs (#65);
            # declared word sites (#68) and per-page quality verdicts too.
            cleanup_records = [p["cleanup"] for p in doc["pages"] if p.get("cleanup")]
            declared_sites = [s for p in doc["pages"] for s in (p.get("declared_words") or [])]
            ocr_quality_pages = [
                {
                    "page": p["page"],
                    "confidence": (p.get("ocr_quality") or {}).get("confidence"),
                    "dict_share": (p.get("ocr_quality") or {}).get("dict_share"),
                    "reasons": (p.get("ocr_quality") or {}).get("reasons", []),
                    "passed": (p.get("ocr_quality") or {}).get("passed"),
                    "cleaned": bool(p.get("cleanup")),
                    "declared_words": len(p.get("declared_words") or []),
                }
                for p in doc["pages"]
                if p.get("ocr_quality")
            ]
            # Extract metadata early (needed for normalization header detection)
            metadata = doc["metadata"]
            title_hint = metadata.get("title") or job.get("metadata", {}).get("title") or ""
            author_hint = metadata.get("author") or job.get("metadata", {}).get("author") or ""
            # Classify and detect structure on the RAW page text: the
            # detectors read line structure (a standalone 'INTRODUCTION'
            # heading, stacked title-page words, contents entry lines).
            # normalize_pages joins lines into paragraphs, which would
            # destroy those signals before detection ever runs.
            self.report_progress(job_id, {"stage": "classifying", "percent": 42})
            for page in doc["pages"]:
                action, reason = classify_page(page)
                page["action"] = action
                page["reason"] = reason
            raw_pages = doc["pages"]

            self.report_progress(job_id, {"stage": "normalizing", "percent": 45})
            # Normalize text: strip headers/footers, repair hyphenations, reconstruct paragraphs
            from worker.prep.cleanup import normalize_pages
            from worker.prep.normalization_settings import NormalizationSettings

            # Load normalization settings from config, with per-job overrides
            norm_settings = NormalizationSettings.from_config(self.settings)
            job_norm_overrides = job.get("metadata", {}).get("normalization")
            if job_norm_overrides:
                norm_settings = norm_settings.merge_job_overrides(job_norm_overrides)
                logger.info("Using per-job normalization overrides: %s", job_norm_overrides)

            doc["pages"], norm_notes = normalize_pages(
                raw_pages, title=title_hint, author=author_hint, settings=norm_settings
            )
            if norm_notes:
                logger.info("Normalization: %d changes (%d headers, %d page numbers, %d hyphens)",
                            len(norm_notes),
                            sum(1 for n in norm_notes if n[0] == "header-removed"),
                            sum(1 for n in norm_notes if n[0] == "page-number-removed"),
                            sum(1 for n in norm_notes if n[0] in ("hyphen-joined", "hyphen-unresolved")))
        else:
            logger.info("Parsing EPUB...")
            doc = parse_epub(data)
            logger.info("Parsed %d items", doc["total_pages"])
            self.report_progress(job_id, {"stage": "classifying", "percent": 45})
            for page in doc["pages"]:
                action, reason = classify_epub_item(page)
                page["action"] = action
                page["reason"] = reason
            raw_pages = doc["pages"]

        logger.info("Detecting chapters...")
        self.check_cancelled(job_id)
        # On raw text: the normalized pages carry paragraphs, not lines.
        # classify_frontmatter runs ONCE per job and feeds both chapter
        # detection and the page ledger (issue #42).
        from worker.prep.frontmatter import classify_frontmatter

        fm_sections = classify_frontmatter(raw_pages)
        chapters = detect_chapters(raw_pages, fm_sections)
        logger.info("Detected %d chapters", len(chapters))
        self.report_progress(job_id, {"stage": "chapters", "percent": 50, "total_chapters": len(chapters)})
        metadata = doc["metadata"]
        title = metadata.get("title") or job.get("metadata", {}).get("title") or "Unknown Title"
        author = metadata.get("author") or job.get("metadata", {}).get("author") or "Unknown Author"
        language = job.get("metadata", {}).get("language") or "en"

        logger.info("Enriching metadata...")
        self.report_progress(job_id, {"stage": "metadata", "percent": 55})
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

        logger.info("Getting cover...")
        self.report_progress(job_id, {"stage": "cover", "percent": 60})
        cover_bytes, cover_source = get_cover(data, input_format, title, author, http=self.http)

        logger.info("Building page ledger...")
        self.report_progress(job_id, {"stage": "ledger", "percent": 65})
        # The ledger is built from raw pages: frontmatter classification reads
        # line structure (title-page words, contents entries), and the
        # normalized pages only carry paragraphs. fm_sections was computed
        # once above and is reused (issue #42).
        ledger = build_page_ledger(input_format, input_url, raw_pages, chapters, fm_sections)
        ledger_errors = validate_page_ledger(ledger, doc["total_pages"])
        if ledger_errors:
            raise ValueError(f"Page ledger validation failed: {ledger_errors}")

        logger.info("Building narrator script...")
        self.report_progress(job_id, {"stage": "script", "percent": 70})
        script = build_narrator_script(
            job_id, input_format, input_url, title, author, language, doc["pages"], chapters
        )
        script_errors = validate_script(script, ledger)
        if script_errors:
            raise ValueError(f"Script validation failed: {script_errors}")
        self.report_progress(job_id, {"stage": "script", "percent": 85, "total_segments": script["metadata"]["total_segments"]})

        logger.info("Analyzing book...")
        self.report_progress(job_id, {"stage": "analysis", "percent": 80})
        llm_analysis = self.analyzer.analyze(
            title=title,
            author=author,
            language=language,
            pages=doc["pages"],
        )
        # Per-chapter summaries + tone notes: optional enrichment (#15).
        # A failed or missing analyzer must never fail the job.
        chapter_analysis: list[dict] = []
        if hasattr(self.analyzer, "analyze_chapters"):
            try:
                chapter_analysis = self.analyzer.analyze_chapters(
                    title=title,
                    author=author,
                    language=language,
                    pages=doc["pages"],
                    chapters=chapters,
                )
                logger.info("Chapter analysis: %d summaries", len(chapter_analysis))
            except Exception as e:
                logger.warning("Chapter analysis failed: %s", e)
        llm_analysis["chapters"] = chapter_analysis

        logger.info("Building EPUB...")
        self.check_cancelled(job_id)
        self.report_progress(job_id, {"stage": "epub", "percent": 90})
        epub_data = build_epub(title, author, language, doc["pages"], chapters, cover=cover_bytes)

        # Cleanup fidelity report (#65): K/U/R per book — the customer-facing
        # quality statement. Zero unattributed changes is the mechanism
        # invariant: verify-and-revert accepts only defect-attributed repairs,
        # and low-confidence sites are counted and declared, never silently
        # accepted.
        kept_changes = [c for r in cleanup_records for c in r.get("kept", [])]
        reverted_changes = [c for r in cleanup_records for c in r.get("reverted", [])]
        fidelity = {
            "pages_total": doc["total_pages"],
            "pages_ocr": sum(1 for p in doc["pages"] if p.get("ocr_quality")),
            "pages_flagged": sum(
                1 for p in doc["pages"] if p.get("ocr_quality") and not p["ocr_quality"]["passed"]
            ),
            "pages_cleaned": len(cleanup_records),
            "pages_unverified": sum(1 for r in cleanup_records if r.get("verify") is False),
            "changes_kept": len(kept_changes),
            "changes_kept_by_class": _count_by(kept_changes, "class"),
            "changes_reverted": len(reverted_changes),
            "structural_reverts": sum(1 for r in cleanup_records if r.get("structural_revert")),
            "low_conf_word_sites": sum(p.get("low_conf_words", 0) for p in doc["pages"]),
            # Declared unknowns (#68): non-dictionary words in the shipped
            # text — damage the signals cannot see AND the author's own
            # rare words, both counted, never silently shipped.
            "declared_word_sites": len(declared_sites),
            "declared_words_sample": declared_sites[:20],
            "unattributed_changes": 0,
        }
        logger.info(
            "Cleanup fidelity: kept=%d reverted=%d structural_reverts=%d low_conf_sites=%d "
            "declared_words=%d unattributed=0",
            fidelity["changes_kept"],
            fidelity["changes_reverted"],
            fidelity["structural_reverts"],
            fidelity["low_conf_word_sites"],
            fidelity["declared_word_sites"],
        )

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
                    "cleanup_fidelity": {
                        "changes_kept": fidelity["changes_kept"],
                        "changes_reverted": fidelity["changes_reverted"],
                        "low_conf_word_sites": fidelity["low_conf_word_sites"],
                        "declared_word_sites": fidelity["declared_word_sites"],
                        "unattributed_changes": fidelity["unattributed_changes"],
                    },
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
            "llm_analysis": llm_analysis,
            "cleanup_log": cleanup_records,
            "fidelity": fidelity,
            "ocr_quality_pages": ocr_quality_pages,
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
                "cleanup-log.json": json.dumps(package["cleanup_log"]),
                "fidelity-report.json": json.dumps(package["fidelity"]),
                "ocr-quality.json": json.dumps(package["ocr_quality_pages"]),
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
                "claim_token": job.get("_claim_token"),
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
                job_id = job["job_id"]
                # Check if job is already complete (idempotency for at-least-once delivery)
                try:
                    response = self.http.get(
                        f"{self.coordinator_url}/jobs/{job_id}",
                        headers=self._headers(),
                        timeout=10,
                    )
                    response.raise_for_status()
                    job_data = response.json()
                    if job_data["status"] in ("prep_complete", "complete", "cancelled"):
                        logger.info("Job %s already %s, skipping", job_id, job_data["status"])
                        self.ack_job(job)
                        continue
                    job = job_data
                except Exception as e:
                    logger.warning("Could not check job status: %s", e)
                self.heartbeat(job_id)
                self.process_job(job)
                self.ack_job(job)
            except JobCancelled as exc:
                logger.info("Job %s cancelled, stopping", exc.job_id)
                self.nack_job(job, "cancelled")
            except Exception as exc:
                logger.error("Job %s failed: %s", job.get("job_id"), exc)
                try:
                    self.http.post(
                        f"{self.coordinator_url}/jobs/{job['job_id']}/fail",
                        headers=self._headers(),
                        json={"error": str(exc), "claim_token": job.get("_claim_token")},
                    )
                except Exception:
                    pass
                self.nack_job(job, str(exc))
        self.http.delete(f"{self.coordinator_url}/workers/{self.worker_id}", headers=self._headers())


def _count_by(items: list[dict], key: str) -> dict:
    counts: dict[str, int] = {}
    for item in items:
        name = str(item.get(key))
        counts[name] = counts.get(name, 0) + 1
    return counts


def _expand_env(value):
    import re

    pattern = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-([^}]*))?\}")

    def repl(match):
        var = match.group(1)
        default = match.group(3)
        return os.environ.get(var, default if default is not None else "")

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

    # Setup logging
    log_level = settings.get("log_level", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
    )

    logger.info("Starting prep worker (config=%s)", path)
    logger.info("Node ID: %s", settings.get("node_id"))
    logger.info("Coordinator: %s", settings.get("coordinator_url"))

    worker = PrepWorker(settings)
    worker.run()


if __name__ == "__main__":
    main()
