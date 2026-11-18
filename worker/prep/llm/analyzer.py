"""Prep-stage LLM analysis that fills llm-analysis.json."""

from __future__ import annotations

from typing import Any, Protocol

from worker.common.llm_client import LLMClient, LLMError, LLMSettings, llm_settings_from_mapping
from worker.prep.llm.prompts import SYSTEM_PROMPT, build_user_prompt


EMPTY_ANALYSIS: dict[str, Any] = {
    "subject_summary": "",
    "author_tone": "",
    "narration_style_recommendations": [],
    "suggested_voice_recipes": [],
}


class Analyzer(Protocol):
    def analyze(
        self,
        *,
        title: str,
        author: str,
        language: str,
        pages: list[dict],
    ) -> dict[str, Any]:
        ...


def _sample_text(pages: list[dict], max_chars: int = 10000) -> str:
    parts: list[str] = []
    total = 0
    for page in pages:
        text = (page.get("text") or "").strip()
        if not text:
            continue
        if total + len(text) > max_chars:
            remain = max_chars - total
            if remain > 0:
                parts.append(text[:remain])
            break
        parts.append(text)
        total += len(text)
    return "\n\n".join(parts)


def _normalize_style(item: Any) -> dict[str, str] | None:
    if not isinstance(item, dict):
        return None
    style = str(item.get("style") or "").strip()
    reason = str(item.get("reason") or "").strip()
    confidence = str(item.get("confidence") or "medium").strip().lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "medium"
    if not style or not reason:
        return None
    return {"style": style, "reason": reason, "confidence": confidence}


def _normalize_recipe(item: Any) -> dict[str, str] | None:
    if not isinstance(item, dict):
        return None
    recipe = str(item.get("recipe") or "").strip()
    style = str(item.get("style") or "").strip()
    reason = str(item.get("reason") or "").strip()
    if not recipe or not style or not reason:
        return None
    return {"recipe": recipe, "style": style, "reason": reason}


def normalize_analysis(raw: dict[str, Any]) -> dict[str, Any]:
    styles = []
    for item in raw.get("narration_style_recommendations") or []:
        normalized = _normalize_style(item)
        if normalized:
            styles.append(normalized)
    recipes = []
    for item in raw.get("suggested_voice_recipes") or []:
        normalized = _normalize_recipe(item)
        if normalized:
            recipes.append(normalized)
    summary = str(raw.get("subject_summary") or "").strip()
    tone = str(raw.get("author_tone") or "").strip()
    if not summary or not tone:
        raise LLMError("LLM analysis missing subject_summary or author_tone")
    if len(styles) < 3:
        raise LLMError("LLM analysis needs at least three narration_style_recommendations")
    return {
        "subject_summary": summary,
        "author_tone": tone,
        "narration_style_recommendations": styles,
        "suggested_voice_recipes": recipes,
    }


class LLMAnalyzer:
    """Calls an OpenAI-compatible endpoint and validates the book-package schema."""

    def __init__(self, client: LLMClient, required: bool = True):
        self.client = client
        self.required = required

    def analyze(
        self,
        *,
        title: str,
        author: str,
        language: str,
        pages: list[dict],
    ) -> dict[str, Any]:
        if not self.client.settings.configured:
            if self.required:
                raise LLMError(
                    "LLM base_url is not configured. Set llm.base_url in "
                    "config/worker-prep.yaml or config/supervisor.yaml before running prep jobs."
                )
            return dict(EMPTY_ANALYSIS)

        sample = _sample_text(pages)
        if not sample.strip():
            if self.required:
                raise LLMError("no book text available for LLM analysis")
            return dict(EMPTY_ANALYSIS)

        raw = self.client.chat_json(
            system=SYSTEM_PROMPT,
            user=build_user_prompt(
                title=title,
                author=author,
                language=language,
                sample_text=sample,
            ),
        )
        return normalize_analysis(raw)


def analyzer_from_settings(settings: dict, http=None) -> LLMAnalyzer:
    llm = llm_settings_from_mapping(settings)
    nested = settings.get("llm") if isinstance(settings.get("llm"), dict) else {}
    required_raw = settings.get("llm_required", nested.get("required", True))
    if isinstance(required_raw, str):
        required = required_raw.strip().lower() not in {"0", "false", "no", "off"}
    else:
        required = bool(required_raw)
    client = LLMClient(llm, http=http)
    return LLMAnalyzer(client, required=required)


def empty_analysis() -> dict[str, Any]:
    return dict(EMPTY_ANALYSIS)
