"""Shared worker settings helpers.

Worker config YAMLs use a nested layout (redis.host, coordinator.url, etc.)
while worker constructors expect flat keys (redis_host, coordinator_url, ...).
flatten_worker_settings bridges the two.
"""

from __future__ import annotations

import os


def flatten_worker_settings(raw: dict) -> dict:
    """Map a nested worker config dict to the flat keys workers expect.

    Handles:
      - node_id from worker.node_id or NODE_ID env
      - redis_host/port/password/db from redis.*
      - coordinator_url, api_key from coordinator.*
      - llm nested block kept as `llm`
      - output_prefix from processing.output_prefix or STORAGE_ROOT-based default
      - default_recipe_image from env DEFAULT_RECIPE_IMAGE or top-level or
        `audiobook-recipe-michael-cosyvoice:local`
    """
    raw = raw or {}
    worker = raw.get("worker") or {}
    redis_cfg = raw.get("redis") or {}
    coordinator = raw.get("coordinator") or {}
    processing = raw.get("processing") or {}
    storage = raw.get("storage") or {}

    node_id = str(worker.get("node_id") or os.environ.get("NODE_ID", "")).strip()
    if not node_id:
        raise SystemExit("worker config has no node_id (worker.node_id or NODE_ID env)")

    redis_host = str(
        os.environ.get("REDIS_HOST", "").strip()
        or redis_cfg.get("host")
        or "127.0.0.1"
    )
    redis_port = int(os.environ.get("REDIS_PORT", "").strip() or redis_cfg.get("port") or 6379)
    redis_password = str(
        os.environ.get("REDIS_PASSWORD", "").strip()
        or redis_cfg.get("password")
        or ""
    )
    redis_db = int(os.environ.get("REDIS_DB", "").strip() or redis_cfg.get("db") or 0)

    coordinator_url = str(
        os.environ.get("COORDINATOR_URL", "").strip()
        or coordinator.get("url")
        or ""
    ).rstrip("/")
    if not coordinator_url:
        raise SystemExit("worker config has no coordinator.url (or COORDINATOR_URL env)")
    api_key = str(coordinator.get("api_key") or "").strip()
    if not api_key:
        raise SystemExit("worker config has no coordinator.api_key")

    # Keep the llm block nested; llm_settings_from_mapping reads it.
    llm = raw.get("llm") if isinstance(raw.get("llm"), dict) else {}

    worker_type = str(worker.get("type") or os.environ.get("WORKER_TYPE") or "prep").strip().lower()
    # output_prefix: processing.output_prefix, then STORAGE_ROOT-based default.
    # Leave empty when unset so per-job code can append job_id under packages/ or audio/.
    output_prefix = str(processing.get("output_prefix") or "").strip()
    storage_root = str(
        storage.get("root") or os.environ.get("STORAGE_ROOT", "/var/lib/audiobook")
    ).rstrip("/")

    default_recipe_image = (
        os.environ.get("DEFAULT_RECIPE_IMAGE", "").strip()
        or str(raw.get("default_recipe_image", "")).strip()
        or "audiobook-recipe-michael-cosyvoice:local"
    )

    return {
        "node_id": node_id,
        "worker_type": worker_type,
        "redis_host": redis_host,
        "redis_port": redis_port,
        "redis_password": redis_password,
        "redis_db": redis_db,
        "coordinator_url": coordinator_url,
        "api_key": api_key,
        "llm": llm,
        "output_prefix": output_prefix,
        "storage_root": storage_root,
        "default_recipe_image": default_recipe_image,
    }
