"""Reaper tests: Redis scan + DB orphan scan (#34).

Runs against the same harness as the other coordinator tests (real
Postgres + real Redis, isolated by conftest). Each test seeds a synthetic
job row and/or queue state, runs one reaper pass, and asserts the
end state of both stores.
"""

from __future__ import annotations

import json
import time
from datetime import timedelta

import pytest

from coordinator.clock import utcnow
from coordinator.services.jobs import (
    CLAIMS_HASH,
    CONVERT_QUEUE,
    FAILED_QUEUE,
    PREP_PROCESSING,
    PREP_QUEUE,
)
from coordinator.services.reaper import run_reaper

pytestmark = pytest.mark.usefixtures("clean_db")

ORPHAN_1 = "11111111-1111-4111-8111-111111111111"
ORPHAN_2 = "22222222-2222-4222-8222-222222222222"
ORPHAN_3 = "33333333-3333-4333-8333-333333333333"
ORPHAN_4 = "44444444-4444-4444-8444-444444444444"
ORPHAN_5 = "55555555-5555-4555-8555-555555555555"
ORPHAN_6 = "66666666-6666-4666-8666-666666666666"
ORPHAN_7 = "77777777-7777-4777-8777-777777777777"
ORPHAN_8 = "88888888-8888-4888-8888-888888888888"


def _insert_job(sql, job_id, *, job_type="prep", status="in_progress", retry_count=0,
                updated_age_seconds=3600, metadata=None, convert=None):
    sql(
        """
        INSERT INTO jobs (
            job_id, type, status, input_format, input_url, package_url, metadata,
            prep, convert, progress, error, retry_count, created_at, updated_at
        ) VALUES (
            %s, %s, %s, 'pdf', 'file:///input/book.pdf', NULL, %s::jsonb,
            '{}'::jsonb, %s::jsonb, '{}'::jsonb, NULL, %s, now(), %s
        )
        """,
        (
            job_id, job_type, status,
            json.dumps(metadata or {}), json.dumps(convert or {}),
            retry_count, utcnow() - timedelta(seconds=updated_age_seconds),
        ),
    )


def _row(sql, job_id):
    return sql("SELECT * FROM jobs WHERE job_id = %s", (job_id,))[0]


def _queue(redis_client, name):
    return [json.loads(raw) for raw in redis_client.lrange(name, 0, -1)]


def test_orphan_in_progress_without_redis_state_is_requeued(app, sql):
    """#34's headline case: in_progress in the DB, invisible in Redis."""
    redis_client = app.app.state.redis
    _insert_job(sql, ORPHAN_1, metadata={"page_range": [1, 60]})

    stats = run_reaper(redis_client, visibility_timeout=60, max_retries=3, pool=app.app.state.pool)

    assert stats["db_orphans_recovered"] == 1
    assert stats["requeued"] == 1
    row = _row(sql, ORPHAN_1)
    assert row["status"] == "submitted"
    assert row["retry_count"] == 1
    assert row["error"] == "recovered_after_orphan_scan"
    messages = _queue(redis_client, PREP_QUEUE)
    assert [m["job_id"] for m in messages] == [ORPHAN_1]
    assert messages[0]["page_range"] == [1, 60]
    assert messages[0]["retry_count"] == 1
    assert messages[0]["output_prefix"].endswith(f"packages/{ORPHAN_1}/")


def test_orphan_past_max_retries_is_dead_lettered(app, sql):
    redis_client = app.app.state.redis
    _insert_job(sql, ORPHAN_2, retry_count=3)

    stats = run_reaper(redis_client, visibility_timeout=60, max_retries=3, pool=app.app.state.pool)

    assert stats["dead_lettered"] == 1
    assert stats["requeued"] == 0
    row = _row(sql, ORPHAN_2)
    assert row["status"] == "failed"
    assert row["retry_count"] == 4
    failed = _queue(redis_client, FAILED_QUEUE)
    assert [m["job_id"] for m in failed] == [ORPHAN_2]
    assert _queue(redis_client, PREP_QUEUE) == []


def test_fresh_claim_is_left_to_the_redis_scan(app, sql):
    """A stale updated_at with a fresh claim means a live worker mid-operation."""
    redis_client = app.app.state.redis
    _insert_job(sql, ORPHAN_3, updated_age_seconds=3600)
    redis_client.hset(CLAIMS_HASH, ORPHAN_3, f"{time.time()}:tok-live")

    stats = run_reaper(redis_client, visibility_timeout=60, max_retries=3, pool=app.app.state.pool)

    assert stats == {"requeued": 0, "dead_lettered": 0, "purged": 0, "db_orphans_recovered": 0}
    assert _row(sql, ORPHAN_3)["status"] == "in_progress"


def test_in_flight_row_with_recent_progress_is_not_orphaned(app, sql):
    redis_client = app.app.state.redis
    _insert_job(sql, ORPHAN_4, updated_age_seconds=30)

    stats = run_reaper(redis_client, visibility_timeout=3600, max_retries=3, pool=app.app.state.pool)

    assert stats == {"requeued": 0, "dead_lettered": 0, "purged": 0, "db_orphans_recovered": 0}
    assert _row(sql, ORPHAN_4)["status"] == "in_progress"


def test_stale_processing_entry_resets_db_row_on_requeue(app, sql):
    """The Redis scan's requeue must also reset the DB row (#34's latent twin):
    previously the row stayed 'in_progress' and the next claim 409'd."""
    redis_client = app.app.state.redis
    _insert_job(sql, ORPHAN_5)
    message = {"job_id": ORPHAN_5, "type": "prep", "input_url": "file:///input/book.pdf", "retry_count": 0}
    redis_client.rpush(PREP_PROCESSING, json.dumps(message))
    redis_client.hset(CLAIMS_HASH, ORPHAN_5, f"{time.time() - 7200}:tok-dead")

    stats = run_reaper(redis_client, visibility_timeout=60, max_retries=3, pool=app.app.state.pool)

    assert stats["requeued"] == 1
    row = _row(sql, ORPHAN_5)
    assert row["status"] == "submitted"
    assert row["retry_count"] == 1
    assert row["error"] == "reclaimed_after_visibility_timeout"
    assert _queue(redis_client, PREP_PROCESSING) == []
    assert [m["job_id"] for m in _queue(redis_client, PREP_QUEUE)] == [ORPHAN_5]


def test_row_already_submitted_keeps_status_on_requeue(app, sql):
    """Worker died between the queue move and mark_in_progress: the row is
    already 'submitted'; the requeue must not corrupt it."""
    redis_client = app.app.state.redis
    _insert_job(sql, ORPHAN_6, status="submitted")
    message = {"job_id": ORPHAN_6, "type": "prep", "input_url": "file:///input/book.pdf", "retry_count": 0}
    redis_client.rpush(PREP_PROCESSING, json.dumps(message))
    redis_client.hset(CLAIMS_HASH, ORPHAN_6, f"{time.time() - 7200}:tok-dead")

    stats = run_reaper(redis_client, visibility_timeout=60, max_retries=3, pool=app.app.state.pool)

    assert stats["requeued"] == 1
    row = _row(sql, ORPHAN_6)
    assert row["status"] == "submitted"
    assert row["retry_count"] == 1


def test_terminal_row_processing_entry_is_purged_not_rerun(app, sql):
    """A completed job whose worker never cleaned its queue entry must be
    purged, not requeued (requeueing would re-run finished work)."""
    redis_client = app.app.state.redis
    _insert_job(sql, ORPHAN_7, status="prep_complete")
    message = {"job_id": ORPHAN_7, "type": "prep", "input_url": "file:///input/book.pdf", "retry_count": 0}
    redis_client.rpush(PREP_PROCESSING, json.dumps(message))
    redis_client.hset(CLAIMS_HASH, ORPHAN_7, f"{time.time() - 7200}:tok-done")

    stats = run_reaper(redis_client, visibility_timeout=60, max_retries=3, pool=app.app.state.pool)

    assert stats["purged"] == 1
    assert stats["requeued"] == 0
    assert _row(sql, ORPHAN_7)["status"] == "prep_complete"
    assert _queue(redis_client, PREP_PROCESSING) == []
    assert _queue(redis_client, PREP_QUEUE) == []


def test_orphan_convert_job_requeues_with_package_fields(app, sql):
    """A converting orphan rebuilds the convert message (package_url,
    voice_recipe, audio output prefix) — mirrors fail_job's retry message."""
    redis_client = app.app.state.redis
    _insert_job(
        sql, ORPHAN_8,
        job_type="convert", status="converting",
        metadata={"performance_style": "warm"},
        convert={"voice_recipe": {"voice": "michael"}},
    )

    stats = run_reaper(redis_client, visibility_timeout=60, max_retries=3, pool=app.app.state.pool)

    assert stats["requeued"] == 1
    row = _row(sql, ORPHAN_8)
    assert row["status"] == "submitted"
    messages = _queue(redis_client, CONVERT_QUEUE)
    assert [m["job_id"] for m in messages] == [ORPHAN_8]
    assert messages[0]["package_url"] == "file:///input/book.pdf"
    assert messages[0]["voice_recipe"] == {"voice": "michael"}
    assert messages[0]["performance_style"] == "warm"
    assert messages[0]["output_prefix"].endswith(f"audio/{ORPHAN_8}/")


def test_no_pool_keeps_original_redis_only_behavior(app, sql):
    """Signature compatibility: without a pool the reaper never touches the
    DB (the pre-#34 behavior for any other caller)."""
    redis_client = app.app.state.redis
    _insert_job(sql, "99999999-9999-4999-8999-999999999999")
    message = {"job_id": "99999999-9999-4999-8999-999999999999", "type": "prep",
               "input_url": "file:///input/book.pdf", "retry_count": 0}
    redis_client.rpush(PREP_PROCESSING, json.dumps(message))

    stats = run_reaper(redis_client, visibility_timeout=60, max_retries=3)

    assert stats["requeued"] == 1
    # DB row untouched: still in_progress (the gap this PR closes in prod).
    assert _row(sql, "99999999-9999-4999-8999-999999999999")["status"] == "in_progress"
    assert [m["job_id"] for m in _queue(redis_client, PREP_QUEUE)] == [message["job_id"]]
