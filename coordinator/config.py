from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
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
class Settings:
    coordinator_id: str
    operator_api_keys: list[str]
    postgres_dsn: str
    redis_host: str
    redis_port: int
    redis_password: str
    redis_db: int
    s3_endpoint: str
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    heartbeat_timeout_seconds: int = 120
    probe_timeout_seconds: int = 90
    drain_grace_seconds: int = 3600
    raw: dict = field(default_factory=dict)

    def redis_url(self) -> str:
        password = self.redis_password
        auth = f":{password}@" if password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"


def load_settings(path: str | Path) -> Settings:
    data = expand_env(yaml.safe_load(Path(path).read_text()))
    server = data.get("server") or {}
    redis_cfg = data.get("redis") or {}
    postgres = data.get("postgres") or {}
    s3 = data.get("s3") or {}
    auth = data.get("auth") or {}
    worker = data.get("worker") or {}
    keys = [key for key in auth.get("operator_api_keys") or [] if key]
    if not keys:
        raise SystemExit("coordinator config has no operator API key")
    coordinator_id = (data.get("coordinator_id") or "").strip()
    if not coordinator_id:
        raise SystemExit("coordinator config has no coordinator_id")
    user = postgres.get("user") or ""
    password = postgres.get("password") or ""
    host = postgres.get("host") or "127.0.0.1"
    port = int(postgres.get("port") or 5432)
    database = postgres.get("database") or "audiobook"
    auth_part = f"{user}:{password}@" if user else ""
    dsn = f"postgresql://{auth_part}{host}:{port}/{database}"
    return Settings(
        coordinator_id=coordinator_id,
        operator_api_keys=keys,
        postgres_dsn=dsn,
        redis_host=str(redis_cfg.get("host") or "127.0.0.1"),
        redis_port=int(redis_cfg.get("port") or 6379),
        redis_password=str(redis_cfg.get("password") or ""),
        redis_db=int(redis_cfg.get("db") or 0),
        s3_endpoint=str(s3.get("endpoint") or ""),
        host=str(server.get("host") or "0.0.0.0"),
        port=int(server.get("port") or 8000),
        log_level=str((data.get("logging") or {}).get("level") or "INFO"),
        heartbeat_timeout_seconds=int(worker.get("heartbeat_timeout_seconds") or 120),
        probe_timeout_seconds=int(worker.get("probe_timeout_seconds") or 90),
        raw=data,
    )
