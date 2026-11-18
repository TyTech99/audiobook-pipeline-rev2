# Fix Worker Crash + Queue Architecture Improvements

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Fix the immediate worker crash (field name mismatch + stale code) and implement a reliable queue pattern with visibility timeout and job reclamation so jobs are never lost when workers crash.

**Architecture:** Two-phase approach:
- **Phase A (immediate):** Fix the field name mismatch and deploy latest code so jobs can process.
- **Phase B (reliability):** Replace `BRPOP` with `BLMOVE` + visibility timeout + reaper pattern per Redis best practices. Jobs move atomically from `pending` to `processing`, a reaper reclaims stuck jobs, and workers must be idempotent.

**Tech Stack:** Python, FastAPI, Pydantic, httpx, Redis, Docker

---

## Best Practices Research Summary

From Redis official docs and industry patterns (Celery, Sidekiq, BullMQ, Azure Architecture):

### Current Implementation Gaps

| Best Practice | Our Implementation | Gap |
|---|---|---|
| Atomic handoff (BLMOVE) | BRPOP (removes from queue) | Job exists only in worker memory after claim |
| Visibility timeout | None | No way to detect stuck jobs |
| Worker heartbeats | Worker heartbeats to coordinator (not per-job) | Can't detect if a specific job's worker died |
| Job reclamation (reaper) | None | Crashed worker = lost job |
| At-least-once delivery | At-most-once (BRPOP) | Jobs lost on crash |
| Idempotent workers | Not enforced | Would break if we add re-delivery |
| Retry with backoff | Retry to same queue (no backoff) | Poison jobs crash workers in a loop |
| Dead-letter queue | `failed` queue exists | Never automatically retried or inspected |
| Single source of truth | Postgres + Redis (diverge) | State inconsistency (job `in_progress` in PG, in `failed` in Redis) |

### Recommended Pattern (Redis Job Queue)

```
pending (list) ──BLMOVE──▶ processing (list) ──ACK (LREM)──▶ done
     ▲                          │
     │                          ▼ (timeout)
     └──────── reaper ◀─────────┘
                  │
                  ▼ (max retries)
              dead-letter (list)
```

1. **Enqueue:** `RPUSH pending {job_id, type}`
2. **Claim:** `BLMOVE pending processing RIGHT LEFT` (atomic — job is always in Redis)
3. **Process:** Worker does work, updates job state in Postgres
4. **Ack:** `LREM processing 1 {job}` (job is done)
5. **Reaper:** Periodically scans `processing` for jobs older than visibility timeout, moves them back to `pending` (with retry count increment)
6. **Dead-letter:** After max retries, job moves to `dead-letter` queue

### Visibility Timeout Tuning

- Must exceed the longest real job time, with margin
- Our longest job: ~30 min OCR for a 300-page scanned PDF
- Recommended: 45 minutes (1.5x the longest job)
- Workers should update a heartbeat during long jobs (we already have progress reporting for this)

### Idempotency Requirements

With at-least-once delivery, workers may process the same job twice. Our prep worker must be idempotent:
- **Download:** Overwrites local file — idempotent ✓
- **Parse:** Re-parses from scratch — idempotent ✓
- **LLM analysis:** May produce slightly different results — acceptable (not a correctness issue)
- **EPUB build:** Overwrites output — idempotent ✓
- **Package upload:** Overwrites S3 object — idempotent ✓
- **Job state transition:** `submitted → in_progress → prep_complete` — need to handle re-entry (if job is already `prep_complete`, skip)

---

## Phase A: Immediate Fix (Get Jobs Processing)

### Task A1: Fix field name in worker progress reporting

**Objective:** Change all `report_progress()` calls to use field names that match the coordinator's `JobProgressIn` schema.

**Files:**
- Modify: `worker/prep/worker.py`

**Step 1: Replace field names**

```bash
cd /home/ty/projects/audiobook-pipeline-rev2
sed -i 's/"phase": /"stage": /g' worker/prep/worker.py
sed -i 's/"page": /"current_page": /g' worker/prep/worker.py
sed -i 's/"chapters": /"total_chapters": /g' worker/prep/worker.py
sed -i 's/"segments": /"total_segments": /g' worker/prep/worker.py
```

**Step 2: Verify**

```bash
grep -c '"stage":' worker/prep/worker.py   # Expected: 12
grep -c '"phase":' worker/prep/worker.py   # Expected: 0
grep -c '"current_page":' worker/prep/worker.py  # Expected: 1+
grep -c '"total_chapters":' worker/prep/worker.py  # Expected: 1+
grep -c '"total_segments":' worker/prep/worker.py  # Expected: 1+
```

**Step 3: Commit**

```bash
git add worker/prep/worker.py
git commit -m "fix(worker): align progress reporting fields with coordinator schema

- phase → stage
- page → current_page
- chapters → total_chapters
- segments → total_segments

The coordinator's JobProgressIn schema requires these field names.
The mismatch caused 400 validation errors on every progress report."
```

---

### Task A2: Add supervisor logging for worker lifecycle

**Objective:** Log when the supervisor starts and stops worker containers.

**Files:**
- Modify: `supervisor/docker_manager.py`

**Step 1: Add logging to `start_worker()`**

```python
logger.info("Starting worker: type=%s image=%s", worker_type, image)
```

**Step 2: Add logging to worker stop/reap logic**

```python
logger.info("Stopping worker: %s", container_name)
```

**Step 3: Run supervisor tests**

```bash
pytest supervisor/tests/ -v
```

**Step 4: Commit**

```bash
git add supervisor/docker_manager.py
git commit -m "feat(supervisor): log worker container start/stop events"
```

---

### Task A3: Deploy latest code and reset job state

**Objective:** Deploy the latest worker code and reset the stuck job.

**Step 1: Rsync latest code**

```bash
cd /home/ty/projects/audiobook-pipeline-rev2
sudo rsync -avz worker/prep/ /opt/audiobook/supervisor/worker/prep/ --exclude '__pycache__' --exclude '*.pyc'
sudo rsync -avz supervisor/ /opt/audiobook/supervisor/ --exclude '__pycache__' --exclude '*.pyc' --exclude 'tests'
```

**Step 2: Restart supervisor**

```bash
sudo docker rm -f audiobook-worker-prep 2>/dev/null
sudo systemctl restart audiobook-supervisor.service
```

**Step 3: Reset job state**

```bash
# Remove from failed queue
sudo docker exec coordinator-redis-1 redis-cli -a $(sudo grep '^REDIS_PASSWORD=' /etc/audiobook/coordinator.env | cut -d= -f2) \
  LREM failed 0 "$(sudo docker exec coordinator-redis-1 redis-cli -a $(sudo grep '^REDIS_PASSWORD=' /etc/audiobook/coordinator.env | cut -d= -f2) LRANGE failed 0 -1 | grep f498a3a5)"

# Reset job status
sudo docker exec coordinator-postgres-1 psql -U audiobook -d audiobook \
  -c "UPDATE jobs SET status = 'submitted', error = NULL, retry_count = 0, prep = '{}' WHERE job_id = 'f498a3a5-4e0a-49a3-bbee-c9fc1d31884e';"

# Re-queue
sudo docker exec coordinator-redis-1 redis-cli -a $(sudo grep '^REDIS_PASSWORD=' /etc/audiobook/coordinator.env | cut -d= -f2) \
  RPUSH prep '{"job_id": "f498a3a5-4e0a-49a3-bbee-c9fc1d31884e", "type": "prep"}'
```

**Step 4: Verify worker starts and processes**

```bash
sleep 10
docker ps --filter "name=audiobook-worker" --format "{{.Names}} {{.Status}}"
docker logs -f audiobook-worker-prep
```

Expected: Worker processes the job without crashing.

---

## Phase B: Reliable Queue Architecture

### Task B1: Implement BLMOVE-based job claiming

**Objective:** Replace `BRPOP` with `BLMOVE` so jobs move atomically from `pending` to `processing`.

**Files:**
- Modify: `worker/prep/worker.py` (claim_job method)
- Modify: `worker/tts/worker.py` (claim_job method)
- Modify: `coordinator/services/jobs.py` (enqueue uses `pending` queue name)

**Step 1: Update queue names in coordinator**

```python
# coordinator/services/jobs.py
PREP_QUEUE = "prep:pending"
CONVERT_QUEUE = "convert:pending"
PREP_PROCESSING = "prep:processing"
CONVERT_PROCESSING = "convert:processing"
FAILED_QUEUE = "failed"
```

**Step 2: Update worker claim_job to use BLMOVE**

```python
# worker/prep/worker.py
def claim_job(self, timeout: int = 30) -> dict | None:
    result = self.redis.blmove(
        "prep:pending", "prep:processing",
        timeout=timeout,
        src="RIGHT", dest="LEFT"
    )
    if result is None:
        return None
    return json.loads(result)
```

**Step 3: Add ack_job method**

```python
def ack_job(self, job: dict) -> None:
    self.redis.lrem("prep:processing", 1, json.dumps(job, sort_keys=True))
```

**Step 4: Update process_job to ack on completion**

```python
def run(self):
    while True:
        job = self.claim_job()
        if job is None:
            continue
        try:
            self.process_job(job)
            self.ack_job(job)  # Remove from processing on success
        except Exception as e:
            logger.error("Job %s failed: %s", job.get("job_id"), e)
            self.nack_job(job, str(e))  # Return to pending or dead-letter
```

**Step 5: Add nack_job method**

```python
def nack_job(self, job: dict, error: str) -> None:
    job_id = job.get("job_id")
    # Report failure to coordinator (handles retry logic)
    try:
        self.http.post(
            f"{self.coordinator_url}/jobs/{job_id}/fail",
            headers=self._headers(),
            json={"error": error},
        )
    except Exception as e:
        logger.warning("Failed to report job failure: %s", e)
    # Remove from processing (coordinator will re-enqueue if retrying)
    self.redis.lrem("prep:processing", 1, json.dumps(job, sort_keys=True))
```

**Step 6: Run tests**

```bash
pytest worker/prep/tests/ -v
pytest coordinator/tests/ -v
```

**Step 7: Commit**

```bash
git add worker/prep/worker.py worker/tts/worker.py coordinator/services/jobs.py
git commit -m "feat(queue): use BLMOVE for atomic job claiming

Jobs now move atomically from pending to processing, so a crashed
worker's job remains in Redis and can be reclaimed by the reaper.
This replaces BRPOP which removed jobs from the queue entirely."
```

---

### Task B2: Implement job reaper (visibility timeout)

**Objective:** Add a reaper that periodically scans the `processing` queue for jobs older than the visibility timeout and moves them back to `pending`.

**Files:**
- Create: `coordinator/services/reaper.py`
- Modify: `coordinator/main.py` (start reaper as background task)
- Modify: `coordinator/config.py` (add visibility_timeout setting)

**Step 1: Add visibility_timeout to coordinator config**

```python
# coordinator/config.py (or settings)
visibility_timeout: int = 2700  # 45 minutes
```

**Step 2: Create reaper service**

```python
# coordinator/services/reaper.py
import json
import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

PREP_PENDING = "prep:pending"
PREP_PROCESSING = "prep:processing"
CONVERT_PENDING = "convert:pending"
CONVERT_PROCESSING = "convert:processing"
FAILED_QUEUE = "failed"


def run_reaper(redis_client, visibility_timeout: int, max_retries: int = 3):
    """Scan processing queues for stale jobs and requeue them."""
    now = time.time()
    stale_threshold = now - visibility_timeout
    
    for processing_queue, pending_queue in [
        (PREP_PROCESSING, PREP_PENDING),
        (CONVERT_PROCESSING, CONVERT_PENDING),
    ]:
        # Get all jobs in processing queue
        jobs = redis_client.lrange(processing_queue, 0, -1)
        for raw in jobs:
            job = json.loads(raw)
            job_id = job.get("job_id")
            
            # Get claim time from hash
            claimed_at = redis_client.hget("job:claims", job_id)
            if claimed_at is None:
                # No claim record — treat as stale
                logger.warning("Job %s in %s has no claim record, requeueing", job_id, processing_queue)
                should_requeue = True
            else:
                claimed_at_ts = float(claimed_at)
                should_requeue = claimed_at_ts < stale_threshold
            
            if should_requeue:
                logger.info("Job %s stale in %s, requeueing", job_id, processing_queue)
                redis_client.lrem(processing_queue, 1, raw)
                redis_client.hdel("job:claims", job_id)
                job["retry_count"] = job.get("retry_count", 0) + 1
                if job["retry_count"] > max_retries:
                    redis_client.rpush(FAILED_QUEUE, json.dumps({
                        "job_id": job_id,
                        "error": "visibility_timeout_exceeded",
                        "retry_count": job["retry_count"],
                    }))
                else:
                    redis_client.rpush(pending_queue, json.dumps(job))
```

**Step 3: Add claimed_at timestamp when claiming**

In the worker's `claim_job`, record the claim time in a Redis hash:

```python
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
    # Record claim time
    self.redis.hset("job:claims", job_id, time.time())
    return job

def ack_job(self, job: dict) -> None:
    job_id = job.get("job_id")
    self.redis.lrem("prep:processing", 1, json.dumps(job, sort_keys=True))
    self.redis.hdel("job:claims", job_id)
```

**Step 4: Start reaper as background task in coordinator**

```python
# coordinator/main.py
import asyncio
from coordinator.services.reaper import run_reaper

@app.on_event("startup")
async def start_reaper():
    async def reaper_loop():
        while True:
            try:
                run_reaper(redis_client, settings.visibility_timeout, settings.max_retries)
            except Exception as e:
                logger.error("Reaper error: %s", e)
            await asyncio.sleep(60)  # Run every minute
    
    asyncio.create_task(reaper_loop())
```

**Step 5: Run tests**

```bash
pytest coordinator/tests/ -v
```

**Step 6: Commit**

```bash
git add coordinator/services/reaper.py coordinator/main.py coordinator/config.py
git commit -m "feat(coordinator): add job reaper with visibility timeout

The reaper scans processing queues every minute for jobs that have
been claimed longer than the visibility timeout (45 min) and moves
them back to pending. Jobs exceeding max retries go to the failed
queue. This prevents job loss when workers crash."
```

---

### Task B3: Make workers idempotent

**Objective:** Ensure the prep worker can safely process the same job twice without corrupting state.

**Files:**
- Modify: `worker/prep/worker.py`

**Step 1: Check job status before processing**

```python
def process_job(self, job: dict) -> dict:
    job_id = job["job_id"]
    
    # Check if job is already complete (idempotency)
    try:
        response = self.http.get(
            f"{self.coordinator_url}/jobs/{job_id}",
            headers=self._headers(),
        )
        response.raise_for_status()
        job_data = response.json()
        if job_data["status"] in ("prep_complete", "complete", "cancelled"):
            logger.info("Job %s already %s, skipping", job_id, job_data["status"])
            return job_data
    except Exception as e:
        logger.warning("Could not check job status: %s", e)
    
    # ... rest of processing
```

**Step 2: Verify all side effects are idempotent**

- Download: overwrites file ✓
- Parse: re-parses from scratch ✓
- LLM analysis: may differ slightly, acceptable ✓
- EPUB build: overwrites output ✓
- S3 upload: overwrites object ✓
- Job state: `submitted → in_progress → prep_complete` (need to handle re-entry)

**Step 3: Commit**

```bash
git add worker/prep/worker.py
git commit -m "fix(worker): make prep worker idempotent

Check job status before processing to skip jobs that are already
complete. This is required for at-least-once delivery semantics."
```

---

### Task B4: Migrate existing queue data

**Objective:** Rename existing queues from `prep`/`convert` to `prep:pending`/`convert:pending` and clear the `failed` queue.

**Step 1: Rename queues in Redis**

```bash
REDIS_PASS=$(sudo grep '^REDIS_PASSWORD=' /etc/audiobook/coordinator.env | cut -d= -f2)

# Rename prep queue
sudo docker exec coordinator-redis-1 redis-cli -a $REDIS_PASS RENAME prep prep:pending
sudo docker exec coordinator-redis-1 redis-cli -a $REDIS_PASS RENAME convert convert:pending

# Clear failed queue (jobs will be re-submitted manually if needed)
sudo docker exec coordinator-redis-1 redis-cli -a $REDIS_PASS DEL failed
```

**Step 2: Update any code that references the old queue names**

Verify all references to `prep` and `convert` queues use the new names.

**Step 3: Commit**

```bash
git commit -m "chore: migrate queue names to pending/processing pattern"
```

---

### Task B5: Add integration tests

**Objective:** Add tests for the new queue pattern.

**Files:**
- Create: `tests/test_queue_reliability.py`

**Step 1: Write tests**

```python
"""Integration tests for reliable queue pattern."""
import json
import time
import redis
from coordinator.services.reaper import run_reaper


def test_blmove_atomic_claim(redis_client):
    """Job moves atomically from pending to processing."""
    job = json.dumps({"job_id": "test-1", "type": "prep"})
    redis_client.rpush("prep:pending", job)
    
    result = redis_client.blmove("prep:pending", "prep:processing", timeout=1, src="RIGHT", dest="LEFT")
    assert result == job
    assert redis_client.llen("prep:pending") == 0
    assert redis_client.llen("prep:processing") == 1


def test_reaper_requeues_stale_job(redis_client):
    """Reaper moves stale jobs back to pending."""
    job = json.dumps({"job_id": "test-2", "type": "prep", "retry_count": 0})
    redis_client.lpush("prep:processing", job)
    redis_client.hset("job:claims", "test-2", time.time() - 3600)  # 1 hour ago
    
    run_reaper(redis_client, visibility_timeout=2700, max_retries=3)
    
    assert redis_client.llen("prep:processing") == 0
    assert redis_client.llen("prep:pending") == 1
    requeued = json.loads(redis_client.lindex("prep:pending", 0))
    assert requeued["retry_count"] == 1


def test_reaper_dead_letters_after_max_retries(redis_client):
    """Jobs exceeding max retries go to failed queue."""
    job = json.dumps({"job_id": "test-3", "type": "prep", "retry_count": 3})
    redis_client.lpush("prep:processing", job)
    redis_client.hset("job:claims", "test-3", time.time() - 3600)
    
    run_reaper(redis_client, visibility_timeout=2700, max_retries=3)
    
    assert redis_client.llen("prep:processing") == 0
    assert redis_client.llen("prep:pending") == 0
    assert redis_client.llen("failed") == 1
```

**Step 2: Run tests**

```bash
pytest tests/test_queue_reliability.py -v
```

**Step 3: Commit**

```bash
git add tests/test_queue_reliability.py
git commit -m "test: add integration tests for reliable queue pattern"
```

---

## Files Likely to Change

**Phase A:**
- `worker/prep/worker.py` — field name fixes
- `supervisor/docker_manager.py` — worker lifecycle logging

**Phase B:**
- `worker/prep/worker.py` — BLMOVE claiming, ack/nack, idempotency
- `worker/tts/worker.py` — BLMOVE claiming, ack/nack
- `coordinator/services/jobs.py` — queue name changes
- `coordinator/services/reaper.py` — new reaper service
- `coordinator/main.py` — start reaper background task
- `coordinator/config.py` — visibility_timeout setting
- `tests/test_queue_reliability.py` — new integration tests

## Tests / Validation

- `pytest worker/prep/tests/ -v` — existing prep worker tests
- `pytest coordinator/tests/ -v` — existing coordinator tests
- `pytest supervisor/tests/ -v` — existing supervisor tests
- `pytest tests/test_queue_reliability.py -v` — new queue reliability tests
- Live test: submit a job, kill the worker mid-processing, verify the reaper requeues it

## Risks, Tradeoffs, and Open Questions

1. **Risk:** The visibility timeout (45 min) must be longer than the longest job. If a job takes longer than 45 min, the reaper will requeue it while the original worker is still processing, causing duplicate work. **Mitigation:** Workers update their claim timestamp periodically (heartbeat) during long jobs.

2. **Risk:** Queue name migration (`prep` → `prep:pending`) will break any in-flight jobs. **Mitigation:** Do this during a maintenance window when no jobs are processing.

3. **Tradeoff:** At-least-once delivery means jobs may be processed twice. Workers must be idempotent. **Mitigation:** Task B3 adds idempotency checks.

4. **Open question:** Should the reaper run in the coordinator or as a separate service? **Recommendation:** Coordinator for now (simpler). If it becomes a bottleneck, extract it.

5. **Open question:** Should we use Redis Streams with consumer groups instead of lists? **Recommendation:** Lists are simpler and sufficient for our use case. Streams add complexity (consumer group management, pending entries) that we don't need yet. Revisit if we need fan-out or multiple consumer groups.

6. **Open question:** Should we add exponential backoff for retries? **Recommendation:** Yes, but as a follow-up. For now, immediate requeue is acceptable since our jobs are CPU-bound (OCR) and don't fail due to transient errors.
