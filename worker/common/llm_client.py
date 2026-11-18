"""OpenAI-compatible chat client for pipeline LLM calls."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx


class LLMError(RuntimeError):
    """Raised when the inference endpoint rejects a request or returns bad data."""


@dataclass(frozen=True)
class LLMSettings:
    base_url: str
    api_key: str = ""
    model: str = ""
    timeout_seconds: float = 120.0
    slots: int = 0

    @property
    def configured(self) -> bool:
        return bool(self.base_url.strip())

    def chat_url(self) -> str:
        root = self.base_url.rstrip("/")
        if root.endswith("/v1"):
            return f"{root}/chat/completions"
        return f"{root}/v1/chat/completions"


def llm_settings_from_mapping(settings: dict | None, environ: dict | None = None) -> LLMSettings:
    """Resolve LLM settings from a flat/nested worker dict and process env."""
    import os

    env = environ if environ is not None else os.environ
    data = settings or {}
    nested = data.get("llm") if isinstance(data.get("llm"), dict) else {}

    def first(*values: object, default: str = "") -> str:
        for value in values:
            if value not in (None, ""):
                return str(value)
        return default

    base_url = first(
        data.get("llm_base_url"),
        nested.get("base_url"),
        env.get("LLM_BASE_URL"),
    )
    # Do not fall back to settings["api_key"] (that is the coordinator node key).
    api_key = first(
        data.get("llm_api_key"),
        nested.get("api_key"),
        env.get("LLM_API_KEY"),
    )
    model = first(
        data.get("llm_model"),
        nested.get("model"),
        env.get("LLM_MODEL"),
    )
    timeout_raw = first(
        data.get("llm_timeout_seconds"),
        nested.get("timeout_seconds"),
        env.get("LLM_TIMEOUT_SECONDS"),
        default="120",
    )
    slots_raw = first(
        data.get("llm_slots"),
        nested.get("slots"),
        env.get("LLM_SLOTS"),
        default="0",
    )
    try:
        timeout = float(timeout_raw or 120)
    except (TypeError, ValueError):
        timeout = 120.0
    try:
        slots = int(slots_raw or 0)
    except (TypeError, ValueError):
        slots = 0
    return LLMSettings(
        base_url=base_url.strip(),
        api_key=api_key.strip(),
        model=model.strip(),
        timeout_seconds=timeout,
        slots=slots,
    )


def llm_env(settings: LLMSettings) -> dict[str, str]:
    """Environment block injected into worker and recipe containers."""
    return {
        "LLM_BASE_URL": settings.base_url,
        "LLM_API_KEY": settings.api_key,
        "LLM_MODEL": settings.model,
        "LLM_TIMEOUT_SECONDS": str(int(settings.timeout_seconds)),
        "LLM_SLOTS": str(settings.slots),
    }


_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def extract_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from model output, tolerating markdown fences."""
    raw = (text or "").strip()
    if not raw:
        raise LLMError("empty model response")
    candidates = [raw]
    fenced = _JSON_FENCE.search(raw)
    if fenced:
        candidates.insert(0, fenced.group(1))
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if isinstance(value, dict):
            return value
    raise LLMError(f"model response is not a JSON object: {last_error}")


class LLMClient:
    """Minimal chat client against an OpenAI-compatible /v1 endpoint."""

    def __init__(self, settings: LLMSettings, http: httpx.Client | None = None):
        self.settings = settings
        self.http = http or httpx.Client(timeout=settings.timeout_seconds)

    def chat_json(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        if not self.settings.configured:
            raise LLMError("LLM_BASE_URL is not set")
        if not self.settings.model:
            raise LLMError("LLM_MODEL is not set")

        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"

        body = {
            "model": self.settings.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }
        try:
            response = self.http.post(self.settings.chat_url(), headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc
        if response.status_code >= 400:
            raise LLMError(f"LLM HTTP {response.status_code}: {response.text[:500]}")
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise LLMError("LLM response was not JSON") from exc
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("LLM response missing choices[0].message.content") from exc
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part) for part in content
            )
        return extract_json_object(str(content))
