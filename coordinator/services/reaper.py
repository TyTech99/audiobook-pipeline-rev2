"""Job reaper: reclaims stale jobs and keeps Postgres and Redis in agreement.

Two scans, one invariant: a job that is mid-flight (``in_progress`` /
``converting`` in Postgres) is either freshly claimed in Redis or waiting in
a pending queue. Anything else gets reclaimed.

1. **Redis scan** (original): entries in the processing queues whose claim
   record is stale, missing, or malformed are requeued or dead-lettered.
   The DB row is the retry-count source of truth and is reset to
   ``submitted`` — previously this path only touched Redis, so the next
   ``mark_in_progress`` raised 409 and the job died anyway (#34's latent
   twin). Entries whose DB row is already terminal are purged instead of
   requeued: the worker finished without cleaning its queue entry, and
   requeueing finished work would re-run it.

2. **DB scan** (#34): rows ``in_progress``/``converting`` whose
   ``updated_at`` is older than the visibility timeout, that have no fresh
   claim record and no processing-queue entry. These orphans are invisible
   to the Redis scan (the worker died before writing any Redis state, or
   the Redis state was lost) and previously stayed stuck forever. Recovery
   mirrors ``jobs.fail_job``: rebuild the pending-queue message from the
   row, bump the retry count, requeue — or dead-letter past ``max_retries``.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import timedelta

from psycopg import Connection

from coordinator.clock import iso, utcnow
from coordinator.services.jobs import (
    CLAIMS_HASH,
    CONVERT_PROCESSING,
    CONVERT_QUEUE,
    FAILED_QUEUE,
    PREP_PROCESSING,
    PREP_QUEUE,
    _storage_root,
)

logger = logging.getLogger(__name__)

TERMINAL = ("prep_complete", "complete", "failed", "convert_failed", "cancelled")


def _decode(raw) -> str:
    return raw.decode() if isinstance(raw, bytes) else str(raw)


def _claim_ts(redis_client, job_id) -> float | None:
    """Claim timestamp, or None when the claim is absent or malformed."""
    raw = redis_client.hget(CLAIMS_HASH, job_id)
    if raw is None:
        return None
    try:
        return float(_decode(raw).split(":", 1)[0])
    except ValueError:
        return None


def _load_row(conn: Connection, job_id) -> dict | None:
    return conn.execute("SELECT * FROM jobs WHERE job_id = %s", (job_id,)).fetchone()


def _reset_row(conn: Connection, job_id, retry_count: int, error: str) -> None:
    """Return a mid-flight row to 'submitted' for another attempt.

    A row already 'submitted' (worker died between the queue move and
    mark_in_progress) keeps its status but still records the retry.
    """
    conn.execute(
        """
        UPDATE jobs
        SET status = CASE WHEN status IN ('in_progress', 'converting')
                          THEN 'submitted' ELSE status END,
            retry_count = %s, error = %s, updated_at = %s
        WHERE job_id = %s
        """,
        (retry_count, error, utcnow(), job_id),
    )


def _fail_row(conn: Connection, redis_client, row: dict, retry_count: int, error: str, is_convert: bool) -> None:
    new_status = "convert_failed" if is_convert else "failed"
    conn.execute(
        """
        UPDATE jobs
        SET status = %s, retry_count = %s, error = %s, updated_at = %s
        WHERE job_id = %s
        """,
        (new_status, retry_count, error, utcnow(), row["job_id"]),
    )
    redis_client.rpush(
        FAILED_QUEUE,
        json.dumps({"job_id": str(row["job_id"]), "error": error, "retry_count": retry_count}),
    )


def _message_from_row(row: dict) -> dict:
    """Rebuild the pending-queue message from a DB row (mirrors fail_job)."""
    job_id = row["job_id"]
    metadata = row["metadata"] or {}
    root = _storage_root()
    if row["type"] == "convert":
        return {
            "job_id": str(job_id),
            "type": "convert",
            "input_url": row["input_url"],
            "package_url": row["package_url"] or row["input_url"],
            "output_prefix": f"file://{root}/audio/{job_id}/",
            "voice_recipe": (row["convert"] or {}).get("voice_recipe")
            or metadata.get("voice_recipe")
            or {},
            "performance_style": (row["convert"] or {}).get("performance_style")
            or metadata.get("performance_style")
            or "neutral",
            "metadata": metadata,
            "created_at": iso(row["created_at"]),
        }
    message = {
        "job_id": str(job_id),
        "type": "prep",
        "input_format": row["input_format"],
        "input_url": row["input_url"],
        "output_prefix": f"file://{root}/packages/{job_id}/",
        "metadata": metadata,
        "created_at": iso(row["created_at"]),
    }
    if metadata.get("page_range"):
        message["page_range"] = metadata["page_range"]
    return message


def _resolve(conn, redis_client, *, row, message, error, max_retries: int, stats: dict) -> None:
    """Shared recovery: bump the DB retry count, then requeue or dead-letter."""
    job_id = row["job_id"]
    retry_count = (row["retry_count"] or 0) + 1
    is_convert = row["type"] == "convert" or row["status"] == "converting"
    if retry_count > max_retries:
        logger.warning("Job %s exceeded max retries (%d), dead-lettering", job_id, max_retries)
        _fail_row(conn, redis_client, row, retry_count, error, is_convert)
        stats["dead_lettered"] += 1
    else:
        message["retry_count"] = retry_count
        redis_client.rpush(CONVERT_QUEUE if is_convert else PREP_QUEUE, json.dumps(message))
        _reset_row(conn, job_id, retry_count, error)
        logger.info("Job %s requeued after %s (attempt %d)", job_id, error, retry_count)
        stats["requeued"] += 1


def _redis_scan(redis_client, stale_threshold: float, max_retries: int, stats: dict, conn: Connection | None) -> None:
    """Reclaim stale entries in the Redis processing queues."""
    for processing_queue in (PREP_PROCESSING, CONVERT_PROCESSING):
        jobs = redis_client.lrange(processing_queue, 0, -1)
        for raw in jobs:
            job = json.loads(raw)
            job_id = job.get("job_id")

            row = _load_row(conn, job_id) if conn is not None else None
            if conn is not None:
                if row is None:
                    # Queue entry with no DB row: junk from a wiped database.
                    logger.warning("Job %s in %s has no DB row, purging entry", job_id, processing_queue)
                    redis_client.lrem(processing_queue, 1, raw)
                    redis_client.hdel(CLAIMS_HASH, job_id)
                    stats["purged"] += 1
                    continue
                if row["status"] in TERMINAL:
                    # Finished elsewhere; the worker never cleaned its entry.
                    logger.info(
                        "Job %s already %s, purging stale %s entry",
                        job_id, row["status"], processing_queue,
                    )
                    redis_client.lrem(processing_queue, 1, raw)
                    redis_client.hdel(CLAIMS_HASH, job_id)
                    stats["purged"] += 1
                    continue

            claimed_at = redis_client.hget(CLAIMS_HASH, job_id)
            claimed_at_ts = None
            if claimed_at is None:
                # No claim record — treat as stale (legacy job or crash before hset)
                logger.warning("Job %s in %s has no claim record, requeueing", job_id, processing_queue)
                should_requeue = True
            else:
                claimed_str = _decode(claimed_at)
                try:
                    claimed_at_ts = float(claimed_str.split(":", 1)[0])
                except ValueError:
                    logger.warning("Job %s has malformed claim record %r, requeueing", job_id, claimed_str)
                    should_requeue = True
                else:
                    should_requeue = claimed_at_ts < stale_threshold

            if not should_requeue:
                continue

            if conn is None:
                # Compatibility path (no DB access): the original behavior.
                age = 0 if claimed_at_ts is None else int(time.time() - claimed_at_ts)
                logger.info(
                    "Job %s stale in %s (age %ds), requeueing", job_id, processing_queue, age,
                )
                redis_client.lrem(processing_queue, 1, raw)
                redis_client.hdel(CLAIMS_HASH, job_id)
                retry_count = job.get("retry_count", 0) + 1
                job["retry_count"] = retry_count
                if retry_count > max_retries:
                    logger.warning("Job %s exceeded max retries (%d), dead-lettering", job_id, max_retries)
                    redis_client.rpush(
                        FAILED_QUEUE,
                        json.dumps({
                            "job_id": job_id,
                            "error": "visibility_timeout_exceeded",
                            "retry_count": retry_count,
                        }),
                    )
                    stats["dead_lettered"] += 1
                else:
                    redis_client.rpush(PREP_QUEUE if processing_queue == PREP_PROCESSING else CONVERT_QUEUE, json.dumps(job))
                    stats["requeued"] += 1
                continue

            logger.info("Job %s stale in %s, requeueing", job_id, processing_queue)
            redis_client.lrem(processing_queue, 1, raw)
            redis_client.hdel(CLAIMS_HASH, job_id)
            _resolve(
                conn, redis_client,
                row=row, message=job,
                error="reclaimed_after_visibility_timeout",
                max_retries=max_retries, stats=stats,
            )


def _db_scan(conn, redis_client, stale_threshold: float, visibility_timeout: int, max_retries: int, stats: dict) -> None:
    """Recover in-flight DB rows that Redis knows nothing about (#34)."""
    cutoff = utcnow() - timedelta(seconds=visibility_timeout)
    rows = conn.execute(
        """
        SELECT * FROM jobs
        WHERE status IN ('in_progress', 'converting') AND updated_at < %s
        """,
        (cutoff,),
    ).fetchall()
    for row in rows:
        job_id = row["job_id"]

        claimed_ts = _claim_ts(redis_client, str(job_id))
        if claimed_ts is not None and claimed_ts >= stale_threshold:
            # Fresh claim: a live worker owns it (mid-long-operation). The
            # Redis scan polices it by claim age.
            continue

        processing = CONVERT_PROCESSING if row["status"] == "converting" else PREP_PROCESSING
        in_processing = any(
            json.loads(raw).get("job_id") == str(job_id)
            for raw in redis_client.lrange(processing, 0, -1)
        )
        if in_processing:
            # Present in Redis: the Redis scan owns it.
            continue

        logger.warning(
            "Job %s orphaned (status=%s, updated_at stale, no Redis presence), recovering",
            job_id, row["status"],
        )
        _resolve(
            conn, redis_client,
            row=row, message=_message_from_row(row),
            error="recovered_after_orphan_scan",
            max_retries=max_retries, stats=stats,
        )
        stats["db_orphans_recovered"] += 1


def run_reaper(redis_client, visibility_timeout: int, max_retries: int = 3, pool=None) -> dict:
    """Reclaim stale jobs.

    Args:
        redis_client: Redis client instance
        visibility_timeout: Seconds before a claimed job is considered stale
        max_retries: Maximum retry count before dead-lettering
        pool: Optional psycopg ConnectionPool. With it, the reaper also
            cross-references the DB (#34): recovers orphaned in-flight rows
            and keeps row status/retry_count in sync when requeueing from
            the Redis scan. Without it, the original Redis-only behavior
            is preserved.

    Returns:
        dict with counts: requeued, dead_lettered, purged, db_orphans_recovered
    """
    now = time.time()
    stale_threshold = now - visibility_timeout
    stats = {"requeued": 0, "dead_lettered": 0, "purged": 0, "db_orphans_recovered": 0}

    if pool is None:
        _redis_scan(redis_client, stale_threshold, max_retries, stats, conn=None)
        return stats

    with pool.connection() as conn:
        _redis_scan(redis_client, stale_threshold, max_retries, stats, conn=conn)
        _db_scan(conn, redis_client, stale_threshold, visibility_timeout, max_retries, stats)
        conn.commit()

    return stats
