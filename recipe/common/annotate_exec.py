"""Engine-neutral annotation EXECUTION: the LLM call loop that turns a
chapter's clean segments into metadata cues, per the declared contract.

Prompt assembly and cue extraction live in common/annotation.py; this
module runs the loop (serial segments with state passing, chapter as
emotional reset, reject-and-retry per the declaration). Recipes pass
their engine's prompt/example paths — nothing here is engine-specific.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from common.annotation import (
    AnnotationContract,
    AnnotationRejected,
    build_prompt,
    extract_cues_from_response,
    parse_annotation,
)
from common.text import first_annotation_mismatch

log = logging.getLogger("recipe.annotate")


def load_llm_settings() -> dict[str, Any] | None:
    """LLM settings from the worker-provided env, or None when unset.

    LLM_SLOTS is read too — the worker passes it and the annotation
    chapter-parallelism is bounded by the DECLARED seats, never a
    hardcoded fallback.
    """
    base_url = os.environ.get("LLM_BASE_URL", "").strip()
    model = os.environ.get("LLM_MODEL", "").strip()
    if not base_url or not model:
        return None
    try:
        slots = int(os.environ.get("LLM_SLOTS", "2"))
    except ValueError:
        slots = 2
    return {
        "base_url": base_url,
        "api_key": os.environ.get("LLM_API_KEY", "").strip(),
        "model": model,
        "timeout": float(os.environ.get("LLM_TIMEOUT_SECONDS", "120")),
        "slots": max(1, slots),
    }


def _llm_chat(llm_settings: dict[str, Any], user_prompt: str) -> str:
    import httpx

    root = llm_settings["base_url"].rstrip("/")
    if not root.endswith("/v1"):
        root += "/v1"
    url = f"{root}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if llm_settings["api_key"]:
        headers["Authorization"] = f"Bearer {llm_settings['api_key']}"
    body = {
        "model": llm_settings["model"],
        "temperature": 0.3,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You annotate audiobook narration for a TTS engine. "
                    "You preserve every source word exactly."
                ),
            },
            {"role": "user", "content": user_prompt},
        ],
    }
    with httpx.Client(timeout=llm_settings["timeout"]) as client:
        resp = client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
        return str(content).strip()


def annotate_chapter(
    chapter: dict[str, Any],
    contract: AnnotationContract,
    llm_settings: dict[str, Any],
    style: str,
    style_desc: str,
    annotation_prompt_path: Path,
    markup_examples_path: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """Annotate one chapter: segments serial with state passing, chapter
    as emotional reset point, reject-and-retry per the declaration, then
    fall back clean and count. Cues are metadata keyed by segment ID —
    the returned text is never kept."""
    cues_map: dict[str, dict[str, Any]] = {}
    stats = {"annotated": 0, "retried": 0, "rejected": 0,
             "dropped_undeclared": 0, "tags_used": 0, "directions": 0}
    state: str | None = None  # chapter = emotional reset point
    for i, seg in enumerate(chapter.get("segments", [])):
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        seg_id = str(seg.get("id", f"seg-{i:03d}"))
        content_type = str(seg.get("content_type", "body_text"))
        prompt = build_prompt(
            contract, annotation_prompt_path, markup_examples_path,
            style=style, style_description=style_desc,
            content_type=content_type, previous_state=state, chunk_text=text,
        )
        cues: dict[str, Any] | None = None
        parsed: dict[str, Any] | None = None
        attempts = 1 + max(0, contract.retries)
        for attempt in range(attempts):
            if attempt:
                stats["retried"] += 1
                log.info("Retrying annotation for %s (attempt %d)", seg_id, attempt + 1)
            try:
                raw = _llm_chat(llm_settings, prompt)
            except Exception as exc:
                log.warning("LLM call failed for %s: %s", seg_id, exc)
                break
            parsed = parse_annotation(raw)
            try:
                cues = extract_cues_from_response(text, parsed, contract)
            except AnnotationRejected:
                bad = first_annotation_mismatch(text, (parsed or {}).get("text") or "")
                log.warning("Rejected annotation for %s (first mismatch: %r), attempt %d/%d",
                            seg_id, bad, attempt + 1, attempts)
                continue
            break
        if cues is None:
            stats["rejected"] += 1
        else:
            stats["annotated"] += 1
            stats["dropped_undeclared"] += int(cues.get("dropped_undeclared", 0))
            stats["tags_used"] += len(cues.get("tags", []))
            if cues.get("direction"):
                stats["directions"] += 1
            state = (parsed.get("state") or state) if parsed else state
            log.info("Annotated %s (direction=%s, state=%s)",
                     seg_id, "yes" if cues.get("direction") else "no",
                     (parsed.get("state") or "-") if parsed else "-")
        cues_map[seg_id] = {"cues": cues, "state_after": state}
    return cues_map, stats


def merge_annotation_stats(target: dict[str, int], source: dict[str, int]) -> None:
    for key, value in source.items():
        target[key] = target.get(key, 0) + value


def write_annotated_script(path: Path, engine: str, cues_map: dict[str, dict[str, Any]]) -> None:
    """Metadata-only cues keyed by segment ID; no second text copy."""
    path.write_text(json.dumps(
        {
            "engine": engine,
            "segments": {
                seg_id: {"cues": entry.get("cues"), "state_after": entry.get("state_after")}
                for seg_id, entry in cues_map.items()
            },
        },
        indent=2,
    ))
