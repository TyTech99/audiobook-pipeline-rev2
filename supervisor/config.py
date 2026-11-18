from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-([^}]*))?\}")


def expand_env(value):
    if isinstance(value, str):
        def _sub(match):
            var = match.group(1)
            default = match.group(3)
            return os.environ.get(var, default if default is not None else "")
        return _ENV.sub(_sub, value)
    if isinstance(value, list):
        return [expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: expand_env(item) for key, item in value.items()}
    return value


@dataclass
class SupervisorSettings:
    node_id: str
    display_name: str
    coordinator_url: str
    api_key: str
    coordinator_id: str
    ca_file: str
    redis_host: str
    redis_port: int
    redis_password: str
    redis_db: int
    s3_endpoint: str
    s3_bucket: str
    s3_access_key: str
    s3_secret_key: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_timeout_seconds: int
    llm_slots: int
    probe_image: str
    prep_image: str
    tts_image: str
    recipe_image: str
    queues: list[str]
    working_hours: dict
    heartbeat_interval_seconds: int
    idle_timeout_seconds: int
    log_level: str
    log_file: str


def load_settings(path: str | Path) -> SupervisorSettings:
    data = expand_env(yaml.safe_load(Path(path).read_text()))
    node = data.get("node") or {}
    coordinator = data.get("coordinator") or {}
    redis_cfg = data.get("redis") or {}
    s3 = data.get("s3") or {}
    llm = data.get("llm") or {}
    images = ((data.get("docker") or {}).get("images") or {})
    hours = data.get("working_hours") or {}
    queues = list(data.get("queues") or [])
    if not queues:
        # Older sample files omit queues. Infer from which images are set.
        if images.get("prep"):
            queues.append("prep")
        if images.get("tts"):
            queues.append("convert")
    # Env vars override image tags from the YAML file.
    prep_image = os.environ.get("PREP_IMAGE") or images.get("prep") or ""
    tts_image = os.environ.get("TTS_IMAGE") or images.get("tts") or ""
    recipe_image = os.environ.get("RECIPE_IMAGE") or images.get("recipe") or ""
    probe_image = os.environ.get("PROBE_IMAGE") or images.get("probe") or "audiobook-probe:dev"
    try:
        llm_timeout = int(llm.get("timeout_seconds") or 120)
    except (TypeError, ValueError):
        llm_timeout = 120
    try:
        llm_slots = int(llm.get("slots") or 0)
    except (TypeError, ValueError):
        llm_slots = 0
    return SupervisorSettings(
        node_id=str(node.get("id") or ""),
        display_name=str(node.get("name") or ""),
        coordinator_url=str(coordinator.get("url") or "").rstrip("/"),
        api_key=str(coordinator.get("api_key") or ""),
        coordinator_id=str(coordinator.get("coordinator_id") or ""),
        ca_file=str(coordinator.get("ca_file") or ""),
        redis_host=str(redis_cfg.get("host") or ""),
        redis_port=int(redis_cfg.get("port") or 6379),
        redis_password=str(redis_cfg.get("password") or ""),
        redis_db=int(redis_cfg.get("db") or 0),
        s3_endpoint=str(s3.get("endpoint") or ""),
        s3_bucket=str(s3.get("bucket") or ""),
        s3_access_key=str(s3.get("access_key") or ""),
        s3_secret_key=str(s3.get("secret_key") or ""),
        # config/supervisor.yaml is the source of truth. LLM_* env only fills
        # blanks (e.g. older installs that still inject env without editing YAML).
        llm_base_url=str(llm.get("base_url") or "").strip() or str(os.environ.get("LLM_BASE_URL") or ""),
        llm_api_key=str(llm.get("api_key") or "") if "api_key" in llm else str(os.environ.get("LLM_API_KEY") or ""),
        llm_model=str(llm.get("model") or "").strip() or str(os.environ.get("LLM_MODEL") or ""),
        llm_timeout_seconds=llm_timeout,
        llm_slots=llm_slots,
        probe_image=str(probe_image),
        prep_image=str(prep_image),
        tts_image=str(tts_image),
        recipe_image=str(recipe_image),
        queues=queues,
        working_hours={
            "enabled": bool(hours.get("enabled", True)),
            "start": hours.get("start"),
            "end": hours.get("end"),
            "timezone": hours.get("timezone"),
        },
        heartbeat_interval_seconds=int((data.get("heartbeat") or {}).get("interval_seconds") or 30),
        idle_timeout_seconds=int((data.get("idle") or {}).get("timeout_minutes", 5) * 60),
        log_level=str((data.get("logging") or {}).get("level") or "INFO"),
        log_file=str((data.get("logging") or {}).get("file") or ""),
    )
