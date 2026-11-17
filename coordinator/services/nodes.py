from __future__ import annotations

import secrets
from datetime import timedelta
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Json

from coordinator.clock import iso, utcnow
from coordinator.config import Settings
from coordinator.errors import ApiError
from coordinator.services.rules import (
    TYPE_QUEUE,
    effective_queues,
    hash_key,
    stale,
    valid_node_id,
    validate_queues,
)


def _check_node_id(node_id: str) -> None:
    if not valid_node_id(node_id):
        raise ApiError(400, "invalid_request", "node_id must be a lowercase slug")


def _hours(value: dict) -> dict:
    return {
        "enabled": bool(value.get("enabled", True)),
        "start": value.get("start"),
        "end": value.get("end"),
        "timezone": value.get("timezone"),
    }


def _load(conn: Connection, node_id: str, settings: Settings, now=None) -> dict:
    row = conn.execute("SELECT * FROM nodes WHERE node_id = %s", (node_id,)).fetchone()
    if row is None:
        raise ApiError(404, "node_not_found", f"node '{node_id}' not found")
    now = now or utcnow()
    if row["state"] == "commissioned" and stale(
        row["last_heartbeat_at"], now, settings.heartbeat_timeout_seconds
    ):
        conn.execute(
            "UPDATE nodes SET state = 'offline', updated_at = %s WHERE node_id = %s",
            (now, node_id),
        )
        row["state"] = "offline"
    return row


def _expire_probes(conn: Connection, node_id: str, settings: Settings, now) -> None:
    cutoff = now - timedelta(seconds=settings.probe_timeout_seconds)
    conn.execute(
        """
        UPDATE probes
        SET status = 'expired', updated_at = %s
        WHERE node_id = %s AND status = 'pending' AND created_at < %s
        """,
        (now, node_id, cutoff),
    )


def _public(row: dict, probe: dict | None = None) -> dict:
    body = {
        "node_id": row["node_id"],
        "display_name": row["display_name"],
        "state": row["state"],
        "allowed_queues": list(row["allowed_queues"] or []),
        "effective_queues": list(row["effective_queues"] or []),
        "hardware": row["hardware"],
        "working_hours": row["working_hours"],
        "last_heartbeat_at": iso(row["last_heartbeat_at"]),
    }
    if probe is not None:
        body["last_probe"] = {
            "probe_id": str(probe["probe_id"]),
            "worker_type": probe["worker_type"],
            "worker_id": None if probe["worker_id"] is None else str(probe["worker_id"]),
            "result": probe["status"],
        }
    return body


def _latest_probe(conn: Connection, node_id: str) -> dict | None:
    return conn.execute(
        """
        SELECT * FROM probes WHERE node_id = %s
        ORDER BY created_at DESC LIMIT 1
        """,
        (node_id,),
    ).fetchone()


def invite(conn: Connection, body: dict, settings: Settings) -> dict:
    node_id = body["node_id"]
    _check_node_id(node_id)
    validate_queues(body["allowed_queues"])
    hours = _hours(body["working_hours"])
    raw_key = secrets.token_hex(32)
    digest = hash_key(raw_key)
    now = utcnow()
    existing = conn.execute("SELECT state FROM nodes WHERE node_id = %s", (node_id,)).fetchone()
    if existing and existing["state"] != "decommissioned":
        raise ApiError(409, "invalid_state", "node already exists", {"state": existing["state"]})
    if existing:
        conn.execute(
            """
            UPDATE nodes
            SET display_name = %s, state = 'pending', key_hash = %s,
                previous_key_hash = NULL, previous_key_expires_at = NULL,
                allowed_queues = %s, effective_queues = '{}', hardware = NULL,
                working_hours = %s, last_heartbeat_at = NULL, updated_at = %s
            WHERE node_id = %s
            """,
            (body.get("display_name") or "", digest, body["allowed_queues"], Json(hours), now, node_id),
        )
    else:
        conn.execute(
            """
            INSERT INTO nodes (
                node_id, display_name, state, key_hash, allowed_queues,
                effective_queues, working_hours, created_at, updated_at
            )
            VALUES (%s, %s, 'pending', %s, %s, '{}', %s, %s, %s)
            """,
            (
                node_id,
                body.get("display_name") or "",
                digest,
                body["allowed_queues"],
                Json(hours),
                now,
                now,
            ),
        )
    return {
        "node_id": node_id,
        "state": "pending",
        "node_api_key": raw_key,
        "created_at": iso(now),
        "coordinator_id": settings.coordinator_id,
    }


def register(conn: Connection, node_id: str, body: dict, settings: Settings) -> dict:
    _check_node_id(node_id)
    validate_queues(body["queues"])
    row = _load(conn, node_id, settings)
    if row["state"] not in {"pending", "commissioned", "offline", "draining"}:
        raise ApiError(401, "unauthorized", "node is not authorized to register")
    now = utcnow()
    hardware = body.get("hardware") or {}
    hours = _hours(body["working_hours"])
    effective = effective_queues(list(row["allowed_queues"]), body["queues"], hardware)
    new_state = "draining" if row["state"] == "draining" else "commissioned"
    conn.execute(
        """
        UPDATE nodes
        SET state = %s, hardware = %s, working_hours = %s, effective_queues = %s,
            last_heartbeat_at = %s, updated_at = %s
        WHERE node_id = %s
        """,
        (new_state, Json(hardware), Json(hours), effective, now, now, node_id),
    )
    return {"node_id": node_id, "state": new_state, "effective_queues": effective}


def heartbeat(conn: Connection, node_id: str, settings: Settings) -> dict:
    row = _load(conn, node_id, settings)
    if row["state"] in {"pending", "decommissioned"}:
        raise ApiError(409, "invalid_state", "node cannot heartbeat in this state", {"state": row["state"]})
    now = utcnow()
    new_state = row["state"]
    if new_state == "offline":
        new_state = "commissioned"
    conn.execute(
        """
        UPDATE nodes SET state = %s, last_heartbeat_at = %s, updated_at = %s
        WHERE node_id = %s
        """,
        (new_state, now, now, node_id),
    )
    _expire_probes(conn, node_id, settings, now)
    command = "run"
    extra = {}
    if new_state == "draining":
        command = "drain"
    else:
        probe = conn.execute(
            """
            SELECT probe_id, worker_type FROM probes
            WHERE node_id = %s AND status = 'pending'
            ORDER BY created_at ASC LIMIT 1
            """,
            (node_id,),
        ).fetchone()
        if probe:
            command = "start_probe"
            extra = {"probe_id": str(probe["probe_id"]), "worker_type": probe["worker_type"]}
    return {
        "status": "ok",
        "server_time": iso(now),
        "node_state": new_state,
        "command": command,
        **extra,
    }


def get_node(conn: Connection, node_id: str, settings: Settings) -> dict:
    row = _load(conn, node_id, settings)
    return _public(row, _latest_probe(conn, node_id))


def list_nodes(conn: Connection, settings: Settings, state: str | None) -> dict:
    rows = conn.execute("SELECT * FROM nodes ORDER BY node_id").fetchall()
    now = utcnow()
    visible = []
    for row in rows:
        if row["state"] == "commissioned" and stale(
            row["last_heartbeat_at"], now, settings.heartbeat_timeout_seconds
        ):
            conn.execute(
                "UPDATE nodes SET state = 'offline', updated_at = %s WHERE node_id = %s",
                (now, row["node_id"]),
            )
            row["state"] = "offline"
        if state is None or row["state"] == state:
            visible.append(_public(row))
    return {"nodes": visible, "total": len(visible)}


def patch_node(conn: Connection, node_id: str, body: dict, settings: Settings) -> dict:
    row = _load(conn, node_id, settings)
    if row["state"] == "decommissioned":
        raise ApiError(409, "invalid_state", "node is decommissioned")
    display = row["display_name"] if body.get("display_name") is None else body["display_name"]
    allowed = list(row["allowed_queues"])
    if body.get("allowed_queues") is not None:
        validate_queues(body["allowed_queues"])
        allowed = body["allowed_queues"]
    hours = row["working_hours"] if body.get("working_hours") is None else _hours(body["working_hours"])
    hardware = row["hardware"]
    capable = ["prep", "convert"] if hardware and hardware.get("gpu") else ["prep"]
    effective = effective_queues(allowed, capable, hardware)
    now = utcnow()
    conn.execute(
        """
        UPDATE nodes
        SET display_name = %s, allowed_queues = %s, effective_queues = %s,
            working_hours = %s, updated_at = %s
        WHERE node_id = %s
        """,
        (display, allowed, effective, Json(hours), now, node_id),
    )
    row.update(
        {
            "display_name": display,
            "allowed_queues": allowed,
            "effective_queues": effective,
            "working_hours": hours,
        }
    )
    return _public(row, _latest_probe(conn, node_id))


def create_probe(conn: Connection, node_id: str, worker_type: str, settings: Settings) -> dict:
    if worker_type not in TYPE_QUEUE:
        raise ApiError(400, "invalid_request", "worker_type must be prep or tts")
    row = _load(conn, node_id, settings)
    if row["state"] != "commissioned":
        raise ApiError(409, "invalid_state", "node must be commissioned to probe", {"state": row["state"]})
    needed = TYPE_QUEUE[worker_type]
    if needed not in (row["effective_queues"] or []):
        raise ApiError(409, "invalid_state", "node cannot run this worker type", {"queue": needed})
    probe_id = uuid4()
    now = utcnow()
    conn.execute(
        """
        INSERT INTO probes (probe_id, node_id, worker_type, status, created_at, updated_at)
        VALUES (%s, %s, %s, 'pending', %s, %s)
        """,
        (probe_id, node_id, worker_type, now, now),
    )
    return {
        "probe_id": str(probe_id),
        "node_id": node_id,
        "worker_type": worker_type,
        "status": "pending",
    }


def fail_probe(conn: Connection, node_id: str, probe_id: UUID, detail: str, settings: Settings) -> dict:
    _load(conn, node_id, settings)
    row = conn.execute(
        "SELECT * FROM probes WHERE probe_id = %s AND node_id = %s",
        (probe_id, node_id),
    ).fetchone()
    if row is None:
        raise ApiError(404, "node_not_found", "probe not found")
    if row["status"] != "pending":
        raise ApiError(409, "invalid_state", "probe is not pending", {"status": row["status"]})
    now = utcnow()
    conn.execute(
        """
        UPDATE probes SET status = 'failed', detail = %s, updated_at = %s
        WHERE probe_id = %s
        """,
        (detail, now, probe_id),
    )
    return {"probe_id": str(probe_id), "status": "failed"}


def rotate_key(conn: Connection, node_id: str, settings: Settings) -> dict:
    row = _load(conn, node_id, settings)
    if row["state"] == "decommissioned" or not row["key_hash"]:
        raise ApiError(409, "invalid_state", "node has no key to rotate")
    raw_key = secrets.token_hex(32)
    now = utcnow()
    expires = now + timedelta(minutes=10)
    conn.execute(
        """
        UPDATE nodes
        SET previous_key_hash = key_hash, previous_key_expires_at = %s,
            key_hash = %s, updated_at = %s
        WHERE node_id = %s
        """,
        (expires, hash_key(raw_key), now, node_id),
    )
    return {
        "node_id": node_id,
        "node_api_key": raw_key,
        "previous_key_expires_at": iso(expires),
    }


def drain(conn: Connection, node_id: str, settings: Settings) -> dict:
    row = _load(conn, node_id, settings)
    if row["state"] == "decommissioned":
        raise ApiError(409, "invalid_state", "node is decommissioned")
    now = utcnow()
    conn.execute(
        "UPDATE nodes SET state = 'draining', updated_at = %s WHERE node_id = %s",
        (now, node_id),
    )
    deadline = now + timedelta(seconds=settings.drain_grace_seconds)
    return {"node_id": node_id, "state": "draining", "grace_deadline": iso(deadline)}


def decommission(conn: Connection, node_id: str, settings: Settings) -> dict:
    row = _load(conn, node_id, settings)
    if row["state"] == "commissioned":
        raise ApiError(409, "invalid_state", "drain the node or wait until it is offline")
    if row["state"] not in {"pending", "draining", "offline", "decommissioned"}:
        raise ApiError(409, "invalid_state", "node cannot be decommissioned", {"state": row["state"]})
    now = utcnow()
    conn.execute(
        """
        UPDATE nodes
        SET state = 'decommissioned', key_hash = NULL, previous_key_hash = NULL,
            previous_key_expires_at = NULL, updated_at = %s
        WHERE node_id = %s
        """,
        (now, node_id),
    )
    return {"node_id": node_id, "state": "decommissioned", "decommissioned_at": iso(now)}
