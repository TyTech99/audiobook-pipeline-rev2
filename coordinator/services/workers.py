from __future__ import annotations

from uuid import UUID

from psycopg import Connection

from coordinator.clock import iso, utcnow
from coordinator.config import Settings
from coordinator.errors import ApiError
from coordinator.services.nodes import _load
from coordinator.services.rules import TYPE_QUEUE, stale


def _mark_stale(conn: Connection, settings: Settings, now) -> None:
    rows = conn.execute("SELECT worker_id, last_heartbeat_at, status FROM workers").fetchall()
    for row in rows:
        if row["status"] == "online" and stale(
            row["last_heartbeat_at"], now, settings.heartbeat_timeout_seconds
        ):
            conn.execute(
                "UPDATE workers SET status = 'offline' WHERE worker_id = %s",
                (row["worker_id"],),
            )


def register_worker(conn: Connection, body: dict, node_id: str, settings: Settings) -> dict:
    if body["node_id"] != node_id:
        raise ApiError(403, "forbidden", "node key does not match this node")
    worker_type = body["type"]
    if worker_type not in TYPE_QUEUE:
        raise ApiError(400, "invalid_request", "type must be prep or tts")
    row = _load(conn, node_id, settings)
    if row["state"] not in {"commissioned", "offline"}:
        raise ApiError(
            409,
            "invalid_state",
            "worker registration refused for this node state",
            {"state": row["state"]},
        )
    needed = TYPE_QUEUE[worker_type]
    effective = list(row["effective_queues"] or [])
    queues = body.get("queues") or []
    if needed not in effective or needed not in queues or any(queue not in effective for queue in queues):
        raise ApiError(409, "invalid_state", "queues are outside the node's effective queues")
    probe_id = body.get("probe_id")
    if probe_id is not None:
        probe = conn.execute(
            """
            SELECT * FROM probes
            WHERE probe_id = %s AND node_id = %s AND status = 'pending' AND worker_type = %s
            """,
            (probe_id, node_id, worker_type),
        ).fetchone()
        if probe is None:
            raise ApiError(409, "invalid_state", "probe is not pending for this node")
    now = utcnow()
    conn.execute(
        """
        INSERT INTO workers (
            worker_id, node_id, worker_type, status, probe, probe_id, last_heartbeat_at, created_at
        )
        VALUES (%s, %s, %s, 'online', %s, %s, %s, %s)
        ON CONFLICT (worker_id) DO UPDATE
        SET status = 'online', last_heartbeat_at = EXCLUDED.last_heartbeat_at,
            probe = EXCLUDED.probe, probe_id = EXCLUDED.probe_id
        """,
        (body["worker_id"], node_id, worker_type, probe_id is not None, probe_id, now, now),
    )
    return {"worker_id": str(body["worker_id"]), "registered_at": iso(now)}


def heartbeat_worker(conn: Connection, worker_id: UUID, body: dict, node_id: str, settings: Settings) -> dict:
    row = conn.execute("SELECT * FROM workers WHERE worker_id = %s", (worker_id,)).fetchone()
    if row is None:
        raise ApiError(404, "worker_not_found", "worker not found")
    if row["node_id"] != node_id:
        raise ApiError(403, "forbidden", "node key does not match this worker")
    now = utcnow()
    conn.execute(
        """
        UPDATE workers
        SET status = 'online', last_heartbeat_at = %s, current_job = %s
        WHERE worker_id = %s
        """,
        (now, body.get("current_job"), worker_id),
    )
    if body.get("status") == "probe_ok" and row["probe_id"] is not None:
        conn.execute(
            """
            UPDATE probes
            SET status = 'passed', worker_id = %s, updated_at = %s
            WHERE probe_id = %s AND node_id = %s AND status = 'pending'
            """,
            (worker_id, now, row["probe_id"], node_id),
        )
    return {"status": "ok", "server_time": iso(now)}


def unregister_worker(conn: Connection, worker_id: UUID, node_id: str) -> dict:
    row = conn.execute("SELECT * FROM workers WHERE worker_id = %s", (worker_id,)).fetchone()
    if row is None:
        raise ApiError(404, "worker_not_found", "worker not found")
    if row["node_id"] != node_id:
        raise ApiError(403, "forbidden", "node key does not match this worker")
    now = utcnow()
    conn.execute(
        "UPDATE workers SET status = 'offline', last_heartbeat_at = %s WHERE worker_id = %s",
        (now, worker_id),
    )
    return {"worker_id": str(worker_id), "unregistered_at": iso(now)}


def list_workers(conn: Connection, settings: Settings, status: str | None, worker_type: str | None, node_id: str | None) -> dict:
    now = utcnow()
    _mark_stale(conn, settings, now)
    clauses = []
    args: list = []
    if status:
        clauses.append("status = %s")
        args.append(status)
    if worker_type:
        clauses.append("worker_type = %s")
        args.append(worker_type)
    if node_id:
        clauses.append("node_id = %s")
        args.append(node_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(f"SELECT * FROM workers {where} ORDER BY created_at", args).fetchall()
    workers = [
        {
            "worker_id": str(row["worker_id"]),
            "node_id": row["node_id"],
            "type": row["worker_type"],
            "status": row["status"],
            "current_job": row["current_job"],
            "last_heartbeat": iso(row["last_heartbeat_at"]),
        }
        for row in rows
    ]
    return {"workers": workers, "total": len(workers)}


def count_online(conn: Connection, settings: Settings) -> int:
    _mark_stale(conn, settings, utcnow())
    row = conn.execute("SELECT count(*) AS n FROM workers WHERE status = 'online'").fetchone()
    return int(row["n"])
