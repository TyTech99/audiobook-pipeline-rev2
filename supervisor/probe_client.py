"""Container entrypoint. Registers, reports probe_ok, unregisters, exits."""

from __future__ import annotations

import os
import sys

import httpx


def run_probe(env: dict | None = None, http: httpx.Client | None = None) -> int:
    env = env if env is not None else os.environ
    base = env["COORDINATOR_URL"].rstrip("/")
    root = base[: -len("/api/v1")] if base.endswith("/api/v1") else base
    headers = {"Authorization": f"Bearer {env['API_KEY']}"}
    client = http or httpx.Client(timeout=10)
    try:
        health = client.get(root + "/health")
        health.raise_for_status()
        expected = env.get("COORDINATOR_ID") or ""
        if expected and health.json().get("coordinator_id") != expected:
            return 2
        worker_id = env["WORKER_ID"]
        registered = client.post(
            base + "/workers/register",
            headers=headers,
            json={
                "worker_id": worker_id,
                "node_id": env["NODE_ID"],
                "type": env["WORKER_TYPE"],
                "queues": ["prep" if env["WORKER_TYPE"] == "prep" else "convert"],
                "probe_id": env["PROBE_ID"],
            },
        )
        if registered.status_code >= 400:
            return 1
        beat = client.post(
            base + f"/workers/{worker_id}/heartbeat",
            headers=headers,
            json={"status": "probe_ok", "current_job": None},
        )
        if beat.status_code >= 400:
            return 1
        done = client.delete(base + f"/workers/{worker_id}", headers=headers)
        if done.status_code >= 400:
            return 1
        return 0
    except Exception:
        return 1
    finally:
        if http is None:
            client.close()


def main() -> None:
    sys.exit(run_probe())


if __name__ == "__main__":
    main()
