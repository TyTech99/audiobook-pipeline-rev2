from __future__ import annotations

from pathlib import Path

import yaml


def load_state(path: str | Path) -> dict:
    data = yaml.safe_load(Path(path).read_text())
    if not isinstance(data, dict):
        raise SystemExit("state file must be a mapping")
    return data


def dump_env(values: dict) -> str:
    return "".join(f"{key}={values[key]}\n" for key in sorted(values))


def parse_env(text: str | None) -> dict:
    if not text:
        return {}
    found = {}
    for line in text.splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        found[key] = value
    return found
