from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime, timedelta

NODE_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
QUEUES = ("prep", "convert")
TYPE_QUEUE = {"prep": "prep", "tts": "convert"}


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def keys_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)


def valid_node_id(node_id: str) -> bool:
    return bool(node_id) and len(node_id) <= 64 and NODE_ID_RE.match(node_id) is not None


def validate_queues(queues: list[str]) -> None:
    from coordinator.errors import ApiError

    if not queues:
        raise ApiError(400, "invalid_request", "queues must not be empty")
    unknown = [queue for queue in queues if queue not in QUEUES]
    if unknown:
        raise ApiError(400, "invalid_request", "unknown queue", {"queues": unknown})


def effective_queues(allowed: list[str], requested: list[str], hardware: dict | None) -> list[str]:
    allowed_set = set(allowed or [])
    requested_set = set(requested or [])
    chosen = [queue for queue in QUEUES if queue in allowed_set and queue in requested_set]
    gpu = None if not hardware else hardware.get("gpu")
    if not gpu:
        chosen = [queue for queue in chosen if queue != "convert"]
    return chosen


def stale(last_heartbeat: datetime | None, now: datetime, timeout_seconds: int) -> bool:
    if last_heartbeat is None:
        return True
    if last_heartbeat.tzinfo is None:
        last_heartbeat = last_heartbeat.replace(tzinfo=now.tzinfo)
    return now - last_heartbeat > timedelta(seconds=timeout_seconds)
