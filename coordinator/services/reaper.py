"""Job reaper: scans processing queues for stale jobs and requeues them.

Implements the visibility timeout pattern from Redis job queue best practices.
Jobs that have been in the processing queue longer than the visibility timeout
are moved back to the pending queue (with retry count increment). Jobs that
exceed max retries are moved to the failed queue.
"""

from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger(__name__)

PREP_PENDING = "prep:pending"
PREP_PROCESSING = "prep:processing"
CONVERT_PENDING = "convert:pending"
CONVERT_PROCESSING = "convert:processing"
FAILED_QUEUE = "failed"
CLAIMS_HASH = "job:claims"


def run_reaper(redis_client, visibility_timeout: int, max_retries: int = 3) -> dict:
    """Scan processing queues for stale jobs and requeue them.

    Args:
        redis_client: Redis client instance
        visibility_timeout: Seconds before a claimed job is considered stale
        max_retries: Maximum retry count before dead-lettering

    Returns:
        dict with counts: {requeued: int, dead_lettered: int}
    """
    now = time.time()
    stale_threshold = now - visibility_timeout
    stats = {"requeued": 0, "dead_lettered": 0}

    for processing_queue, pending_queue in [
        (PREP_PROCESSING, PREP_PENDING),
        (CONVERT_PROCESSING, CONVERT_PENDING),
    ]:
        jobs = redis_client.lrange(processing_queue, 0, -1)
        for raw in jobs:
            job = json.loads(raw)
            job_id = job.get("job_id")

            # Get claim time from hash (format: "timestamp:token" or legacy "timestamp")
            claimed_at = redis_client.hget(CLAIMS_HASH, job_id)
            if claimed_at is None:
                # No claim record — treat as stale (legacy job or crashed before hset)
                logger.warning(
                    "Job %s in %s has no claim record, requeueing",
                    job_id, processing_queue,
                )
                should_requeue = True
            else:
                claimed_str = claimed_at.decode() if isinstance(claimed_at, bytes) else str(claimed_at)
                # Parse timestamp (first part before colon, if present)
                claimed_ts_str = claimed_str.split(":", 1)[0]
                try:
                    claimed_at_ts = float(claimed_ts_str)
                except ValueError:
                    logger.warning("Job %s has malformed claim record %r, requeueing", job_id, claimed_str)
                    should_requeue = True
                else:
                    should_requeue = claimed_at_ts < stale_threshold

            if should_requeue:
                claimed_str = claimed_at.decode() if isinstance(claimed_at, bytes) else str(claimed_at) if claimed_at else None
                claimed_ts_str = claimed_str.split(":", 1)[0] if claimed_str else None
                claimed_ts = float(claimed_ts_str) if claimed_ts_str else now
                age = int(now - claimed_ts)
                logger.info(
                    "Job %s stale in %s (age %ds > timeout %ds), requeueing",
                    job_id, processing_queue, age, visibility_timeout,
                )
                redis_client.lrem(processing_queue, 1, raw)
                redis_client.hdel(CLAIMS_HASH, job_id)

                retry_count = job.get("retry_count", 0) + 1
                job["retry_count"] = retry_count

                if retry_count > max_retries:
                    logger.warning(
                        "Job %s exceeded max retries (%d), dead-lettering",
                        job_id, max_retries,
                    )
                    redis_client.rpush(FAILED_QUEUE, json.dumps({
                        "job_id": job_id,
                        "error": "visibility_timeout_exceeded",
                        "retry_count": retry_count,
                    }))
                    stats["dead_lettered"] += 1
                else:
                    redis_client.rpush(pending_queue, json.dumps(job))
                    stats["requeued"] += 1

    return stats
