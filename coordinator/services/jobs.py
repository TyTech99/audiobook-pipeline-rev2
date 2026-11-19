from __future__ import annotations

import json
import os
import re
from uuid import uuid4

import redis
from psycopg import Connection
from psycopg.types.json import Json

import os

from coordinator.clock import iso, utcnow
from coordinator.config import Settings
from coordinator.errors import ApiError


def _storage_root() -> str:
    return os.environ.get("STORAGE_ROOT", "/var/lib/audiobook")


_ARTIFACT_MEDIA_TYPES = {
    ".epub": "application/epub+zip",
    ".json": "application/json",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".m4b": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".txt": "text/plain",
    ".pdf": "application/pdf",
}


def _media_type(filename: str) -> str:
    return _ARTIFACT_MEDIA_TYPES.get(os.path.splitext(filename)[1].lower(), "application/octet-stream")


def _artifact_dir(row: dict) -> str | None:
    """Local directory holding a job's artifacts (audio output first, else package).

    Workers report package_url/output_url as bare absolute paths, while the
    API's own submit path (convert jobs) stores file:// URLs — accept both,
    reject anything non-local (s3://, https://, relative paths).
    """
    url = row["output_url"] or row["package_url"]
    if not url:
        return None
    if url.startswith("file://"):
        url = url[len("file://"):]
    if not os.path.isabs(url):
        return None
    return url.rstrip("/") + "/"


def _artifact_components(filename: str) -> list[str] | None:
    """Split an artifact path into safe components, or None if unsafe."""
    parts = [p for p in filename.split("/") if p]
    if not parts:
        return None
    for part in parts:
        if part in (".", "..") or not re.fullmatch(r"[A-Za-z0-9._-]+", part):
            return None
    return parts


def list_artifacts(conn: Connection, job_id) -> dict:
    """List downloadable artifacts for a job (#57)."""
    row = _load_job(conn, job_id)
    directory = _artifact_dir(row)
    if directory is None or not os.path.isdir(directory):
        raise ApiError(404, "artifacts_not_found", "no artifacts available for this job")
    artifacts = []
    for current_root, dirnames, filenames in os.walk(directory):
        dirnames.sort()
        for name in sorted(filenames):
            rel = os.path.relpath(os.path.join(current_root, name), directory)
            path = os.path.join(current_root, name)
            artifacts.append(
                {
                    "filename": rel,
                    "size_bytes": os.path.getsize(path),
                    "media_type": _media_type(name),
                    "url": f"/api/v1/jobs/{job_id}/artifacts/{rel}",
                }
            )
    return {"job_id": str(job_id), "status": row["status"], "artifacts": artifacts}


def get_artifact(conn: Connection, job_id, filename: str) -> tuple[str, str]:
    """Resolve an artifact to (local_path, media_type) for download (#57).

    Path components are restricted to [A-Za-z0-9._-] — no traversal, no
    absolute paths, no hidden tricks.
    """
    parts = _artifact_components(filename)
    if parts is None:
        raise ApiError(400, "invalid_request", "invalid artifact filename")
    row = _load_job(conn, job_id)
    directory = _artifact_dir(row)
    path = os.path.join(directory, *parts) if directory else None
    if path is None or not os.path.isfile(path):
        raise ApiError(404, "artifact_not_found", "artifact not found")
    return path, _media_type(parts[-1])

PREP_QUEUE = "prep:pending"
CONVERT_QUEUE = "convert:pending"
PREP_PROCESSING = "prep:processing"
CONVERT_PROCESSING = "convert:processing"
FAILED_QUEUE = "failed"
CLAIMS_HASH = "job:claims"  # single definition; reaper.py imports this

PREP_TRANSITIONS = {
    "submitted": {"in_progress", "cancelled"},
    "in_progress": {"prep_complete", "failed", "cancelled"},
    "prep_complete": {"converting", "cancelled"},
    "converting": {"complete", "convert_failed", "cancelled"},
    "failed": set(),
    "convert_failed": set(),
    "complete": set(),
    "cancelled": set(),
}


def _load_job(conn: Connection, job_id) -> dict:
    row = conn.execute("SELECT * FROM jobs WHERE job_id = %s", (job_id,)).fetchone()
    if row is None:
        raise ApiError(404, "job_not_found", f"job '{job_id}' not found")
    return row


def _public(row: dict) -> dict:
    return {
        "job_id": str(row["job_id"]),
        "type": row["type"],
        "status": row["status"],
        "input_format": row["input_format"],
        "input_url": row["input_url"],
        "package_url": row["package_url"],
        "output_url": row["output_url"],
        "metadata": row["metadata"],
        "prep": row["prep"],
        "convert": row["convert"],
        "progress": row["progress"],
        "error": row["error"],
        "retry_count": row["retry_count"],
        "created_at": iso(row["created_at"]),
        "updated_at": iso(row["updated_at"]),
    }


def submit_job(conn: Connection, body: dict, settings: Settings, redis_client: redis.Redis) -> dict:
    job_type = body.get("type")
    if job_type not in ("prep", "convert"):
        raise ApiError(400, "invalid_request", "type must be prep or convert")
    if not body.get("input_url"):
        raise ApiError(400, "invalid_request", "input_url is required")
    # Convert jobs take the prep package path as input_url. The queue message
    # also carries package_url so TTS workers can download without remapping.
    package_url = body.get("input_url") if job_type == "convert" else None
    metadata = body.get("metadata") or {}
    page_range = body.get("page_range")
    if page_range is not None:
        if len(page_range) != 2 or not all(isinstance(p, int) and p >= 1 for p in page_range):
            raise ApiError(400, "invalid_request", "page_range must be [start, end] with start >= 1 and end >= start")
        if page_range[1] < page_range[0]:
            raise ApiError(400, "invalid_request", "page_range end must be >= start")
        metadata["page_range"] = page_range
    job_id = uuid4()
    now = utcnow()
    conn.execute(
        """
        INSERT INTO jobs (
            job_id, type, status, input_format, input_url, package_url, metadata,
            prep, convert, progress, error, retry_count, created_at, updated_at
        )
        VALUES (%s, %s, 'submitted', %s, %s, %s, %s, %s, %s, %s, %s, 0, %s, %s)
        """,
        (
            job_id,
            job_type,
            body.get("input_format"),
            body.get("input_url"),
            package_url,
            Json(metadata),
            Json({}),
            Json({}),
            Json({}),
            None,
            now,
            now,
        ),
    )
    queue = PREP_QUEUE if job_type == "prep" else CONVERT_QUEUE
    root = _storage_root()
    subdir = "packages" if job_type == "prep" else "audio"
    message = {
        "job_id": str(job_id),
        "type": job_type,
        "input_format": body.get("input_format"),
        "input_url": body.get("input_url"),
        "output_prefix": f"file://{root}/{subdir}/{job_id}/",
        "metadata": metadata,
        "created_at": iso(now),
    }
    if page_range is not None:
        message["page_range"] = page_range
    if job_type == "convert":
        message["package_url"] = package_url
        message["voice_recipe"] = metadata.get("voice_recipe") or {}
        message["performance_style"] = metadata.get("performance_style") or "neutral"
    redis_client.rpush(queue, json.dumps(message))
    return {"job_id": str(job_id), "status": "submitted", "created_at": iso(now)}


def get_job(conn: Connection, job_id) -> dict:
    row = _load_job(conn, job_id)
    return _public(row)


def list_jobs(conn: Connection, status: str | None, limit: int, offset: int, customer_id: str | None) -> dict:
    clauses = []
    args: list = []
    if status:
        clauses.append("status = %s")
        args.append(status)
    if customer_id:
        clauses.append("metadata->>'customer_id' = %s")
        args.append(customer_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    total_row = conn.execute(f"SELECT count(*) AS n FROM jobs {where}", args).fetchone()
    rows = conn.execute(
        f"SELECT * FROM jobs {where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
        args + [limit, offset],
    ).fetchall()
    return {"jobs": [_public(row) for row in rows], "total": int(total_row["n"]), "limit": limit, "offset": offset}


def report_progress(conn: Connection, job_id, body: dict, node_id: str | None) -> dict:
    row = _load_job(conn, job_id)
    if row["status"] not in ("in_progress", "converting"):
        raise ApiError(409, "invalid_state", "job is not in progress", {"status": row["status"]})
    now = utcnow()
    conn.execute(
        "UPDATE jobs SET progress = %s, updated_at = %s WHERE job_id = %s",
        (Json(body), now, job_id),
    )
    return {"status": "ok"}


def mark_in_progress(conn: Connection, job_id, worker_id: str, stage: str) -> dict:
    row = _load_job(conn, job_id)
    # Prep jobs: submitted → in_progress. Convert jobs (separate type): submitted → converting.
    if row["type"] == "convert" or stage == "convert":
        if row["type"] == "convert":
            if row["status"] != "submitted":
                raise ApiError(409, "invalid_state", "job is not submitted", {"status": row["status"]})
        else:
            if row["status"] != "prep_complete":
                raise ApiError(409, "invalid_state", "job is not prep_complete", {"status": row["status"]})
        new_status = "converting"
        phase = "convert"
    else:
        if row["status"] != "submitted":
            raise ApiError(409, "invalid_state", "job is not submitted", {"status": row["status"]})
        new_status = "in_progress"
        phase = "prep"
    now = utcnow()
    phase_data = row[phase] or {}
    phase_data["worker_id"] = worker_id
    phase_data["started_at"] = iso(now)
    conn.execute(
        f"UPDATE jobs SET status = %s, {phase} = %s, updated_at = %s WHERE job_id = %s",
        (new_status, Json(phase_data), now, job_id),
    )
    return {"status": "ok"}


def _verify_claim_token(job_id, body: dict, redis_client: redis.Redis | None) -> None:
    """Verify the claim token matches the current claim. Rejects stale workers."""
    token = body.get("claim_token")
    if token is None or redis_client is None:
        return  # No token provided or no Redis — skip verification (backward compat)
    stored = redis_client.hget(CLAIMS_HASH, str(job_id))
    if stored is None:
        return  # No claim record — job may have been completed already
    # Claim record format: "timestamp:token"
    parts = stored.decode() if isinstance(stored, bytes) else str(stored)
    if ":" in parts:
        stored_token = parts.split(":", 1)[1]
        if stored_token != token:
            raise ApiError(409, "stale_claim", "job has been re-claimed by another worker", {"status": "reclaimed"})


def complete_job(conn: Connection, job_id, body: dict, worker_id: str, redis_client: redis.Redis | None = None) -> dict:
    _verify_claim_token(job_id, body, redis_client)
    row = _load_job(conn, job_id)
    now = utcnow()
    # Branch on job type so a fresh convert job (status submitted) can finish
    # without a fake prep_complete transition.
    if row["type"] == "convert":
        if row["status"] not in ("submitted", "converting"):
            raise ApiError(409, "invalid_state", "job is not in progress", {"status": row["status"]})
        new_status = "complete"
        phase_data = row["convert"] or {}
        phase_data["worker_id"] = worker_id
        phase_data["completed_at"] = iso(now)
        if body.get("voice_recipe") is not None:
            phase_data["voice_recipe"] = body["voice_recipe"]
        if body.get("performance_style") is not None:
            phase_data["performance_style"] = body["performance_style"]
        output_url = body.get("output_url")
        conn.execute(
            "UPDATE jobs SET status = %s, output_url = %s, convert = %s, updated_at = %s WHERE job_id = %s",
            (new_status, output_url, Json(phase_data), now, job_id),
        )
    elif row["status"] in ("submitted", "in_progress"):
        new_status = "prep_complete"
        phase_data = row["prep"] or {}
        phase_data["worker_id"] = worker_id
        phase_data["completed_at"] = iso(now)
        if body.get("total_pages") is not None:
            phase_data["total_pages"] = body["total_pages"]
        if body.get("kept_pages") is not None:
            phase_data["kept_pages"] = body["kept_pages"]
        if body.get("skipped_pages") is not None:
            phase_data["skipped_pages"] = body["skipped_pages"]
        package_url = body.get("package_url")
        conn.execute(
            "UPDATE jobs SET status = %s, package_url = %s, prep = %s, updated_at = %s WHERE job_id = %s",
            (new_status, package_url, Json(phase_data), now, job_id),
        )
    else:
        raise ApiError(409, "invalid_state", "job is not in progress", {"status": row["status"]})
    return {"job_id": str(job_id), "status": new_status}



def fail_job(conn: Connection, job_id, body: dict, worker_id: str, redis_client: redis.Redis, settings: Settings) -> dict:
    _verify_claim_token(job_id, body, redis_client)
    row = _load_job(conn, job_id)
    now = utcnow()
    error = body.get("error") or "unknown error"
    retry_count = row["retry_count"] + 1
    max_retries = int(settings.raw.get("job", {}).get("max_retries") or 3)
    metadata = row["metadata"] or {}
    is_convert = row["type"] == "convert" or row["status"] == "converting"
    if is_convert:
        if row["status"] not in ("submitted", "converting"):
            raise ApiError(409, "invalid_state", "job is not in progress", {"status": row["status"]})
        if retry_count >= max_retries:
            new_status = "convert_failed"
            redis_client.rpush(FAILED_QUEUE, json.dumps({"job_id": str(job_id), "error": error, "retry_count": retry_count}))
        else:
            new_status = "submitted"
            root = _storage_root()
            package_url = row["package_url"] or row["input_url"]
            message = {
                "job_id": str(job_id),
                "type": "convert",
                "input_url": row["input_url"],
                "package_url": package_url,
                "output_prefix": f"file://{root}/audio/{job_id}/",
                "voice_recipe": (row["convert"] or {}).get("voice_recipe") or metadata.get("voice_recipe") or {},
                "performance_style": (row["convert"] or {}).get("performance_style")
                or metadata.get("performance_style")
                or "neutral",
                "metadata": metadata,
                "created_at": iso(row["created_at"]),
            }
            redis_client.rpush(CONVERT_QUEUE, json.dumps(message))
        phase = "convert"
    elif row["status"] in ("submitted", "in_progress"):
        if retry_count >= max_retries:
            new_status = "failed"
            redis_client.rpush(FAILED_QUEUE, json.dumps({"job_id": str(job_id), "error": error, "retry_count": retry_count}))
        else:
            new_status = "submitted"
            root = _storage_root()
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
            redis_client.rpush(PREP_QUEUE, json.dumps(message))
        phase = "prep"
    else:
        raise ApiError(409, "invalid_state", "job is not in progress", {"status": row["status"]})
    phase_data = row[phase] or {}
    phase_data["worker_id"] = worker_id
    if phase == "prep":
        conn.execute(
            "UPDATE jobs SET status = %s, error = %s, retry_count = %s, prep = %s, updated_at = %s WHERE job_id = %s",
            (new_status, error, retry_count, Json(phase_data), now, job_id),
        )
    else:
        conn.execute(
            "UPDATE jobs SET status = %s, error = %s, retry_count = %s, convert = %s, updated_at = %s WHERE job_id = %s",
            (new_status, error, retry_count, Json(phase_data), now, job_id),
        )
    return {"job_id": str(job_id), "status": new_status, "retry_count": retry_count}



def cancel_job(conn: Connection, job_id, redis_client: redis.Redis | None = None) -> dict:
    row = _load_job(conn, job_id)
    if row["status"] in ("failed", "convert_failed", "complete", "cancelled"):
        raise ApiError(409, "invalid_state", "job cannot be cancelled", {"status": row["status"]})
    now = utcnow()
    conn.execute("UPDATE jobs SET status = 'cancelled', updated_at = %s WHERE job_id = %s", (now, job_id))
    # Set cancel flag so any worker processing this job stops promptly
    if redis_client is not None:
        redis_client.set(f"job:cancel:{job_id}", "1", ex=3600)
    return {"job_id": str(job_id), "status": "cancelled"}


def queue_depths(redis_client: redis.Redis) -> dict:
    return {
        "prep": redis_client.llen(PREP_QUEUE),
        "convert": redis_client.llen(CONVERT_QUEUE),
        "prep_processing": redis_client.llen(PREP_PROCESSING),
        "convert_processing": redis_client.llen(CONVERT_PROCESSING),
        "failed": redis_client.llen(FAILED_QUEUE),
    }
