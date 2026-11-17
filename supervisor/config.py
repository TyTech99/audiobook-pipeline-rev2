from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def expand_env(value):
    if isinstance(value, str):
        return _ENV.sub(lambda match: os.environ.get(match.group(1), ""), value)
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
    probe_image: str
    prep_image: str
    tts_image: str
    queues: list[str]
    working_hours: dict
    heartbeat_interval_seconds: int


def load_settings(path: str | Path) -> SupervisorSettings:
    data = expand_env(yaml.safe_load(Path(path).read_text()))
    node = data.get("node") or {}
    coordinator = data.get("coordinator") or {}
    redis_cfg = data.get("redis") or {}
    s3 = data.get("s3") or {}
    images = ((data.get("docker") or {}).get("images") or {})
    hours = data.get("working_hours") or {}
    queues = list(data.get("queues") or [])
    if not queues:
        # Older sample files omit queues. Infer from which images are set.
        if images.get("prep"):
            queues.append("prep")
        if images.get("tts"):
            queues.append("convert")
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
        probe_image=str(images.get("probe") or "audiobook-probe:dev"),
        prep_image=str(images.get("prep") or ""),
        tts_image=str(images.get("tts") or ""),
        queues=queues,
        working_hours={
            "enabled": bool(hours.get("enabled", True)),
            "start": hours.get("start"),
            "end": hours.get("end"),
            "timezone": hours.get("timezone"),
        },
        heartbeat_interval_seconds=int((data.get("heartbeat") or {}).get("interval_seconds") or 30),
    )
