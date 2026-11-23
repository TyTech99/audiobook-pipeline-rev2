"""Annotation execution tests (recipe/common/annotate_exec.py) — #118.

The contract under test: a recipe container proves its LLM endpoint from
INSIDE itself before annotating; transport/dependency failures count as
llm_errors with the reason recorded per segment, never as content
"rejections"; content rejections keep their own counter.
"""

from __future__ import annotations

import json
import pathlib

from common import annotate_exec
from common.annotate_exec import (
    annotate_chapter,
    probe_llm,
    write_annotated_script,
)
from common.annotation import AnnotationContract

RECIPE_ROOT = pathlib.Path(__file__).resolve().parents[2]
PROMPT_PATH = RECIPE_ROOT / "ggardner-indextts" / "engine" / "annotation-prompt.j2"
EXAMPLES_PATH = RECIPE_ROOT / "ggardner-indextts" / "engine" / "markup-examples.md"

LLM_SETTINGS = {
    "base_url": "http://192.168.1.202:8081/v1",
    "api_key": "",
    "model": "test-model",
    "timeout": 5.0,
    "slots": 1,
}

CONTRACT = AnnotationContract(
    engine="indextts2.5",
    instruct_format="{direction}",
    instruct_controls=("calm",),
    max_direction_words=8,
    retries=0,
)

CHAPTER = {"segments": [{"id": "s1", "content_type": "body_text", "text": "Hello world."}]}


# --- probe_llm -------------------------------------------------------------


def test_probe_llm_reachable():
    verdict = probe_llm(LLM_SETTINGS, transport=lambda url, headers: None)
    assert verdict == {"reachable": True, "error": None}


def test_probe_llm_connection_refused():
    def refuse(url, headers):
        raise ConnectionRefusedError("dial refused")

    verdict = probe_llm(LLM_SETTINGS, transport=refuse)
    assert verdict["reachable"] is False
    assert "ConnectionRefusedError" in verdict["error"]


def test_probe_llm_missing_transport_library():
    def no_httpx(url, headers):
        raise ImportError("No module named 'httpx'")

    verdict = probe_llm(LLM_SETTINGS, transport=no_httpx)
    assert verdict["reachable"] is False
    assert "transport library missing" in verdict["error"]


def test_probe_llm_appends_v1_and_passes_auth():
    seen = {}

    def record(url, headers):
        seen["url"] = url
        seen["headers"] = headers

    probe_llm({**LLM_SETTINGS, "api_key": "sekrit"}, transport=record)
    assert seen["url"] == "http://192.168.1.202:8081/v1/models"
    assert seen["headers"]["Authorization"] == "Bearer sekrit"


# --- transport vs content accounting --------------------------------------


def test_transport_failure_counts_llm_errors_not_rejections(monkeypatch):
    def dead(llm_settings, prompt):
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(annotate_exec, "_llm_chat", dead)
    cues_map, stats = annotate_chapter(
        CHAPTER, CONTRACT, LLM_SETTINGS, "neutral", "",
        PROMPT_PATH, EXAMPLES_PATH,
    )
    assert stats["llm_errors"] == 1
    assert stats["rejected"] == 0
    assert stats["annotated"] == 0
    assert stats["retried"] == 0  # no retry on a dead path
    entry = cues_map["s1"]
    assert entry["cues"] is None
    assert "ConnectionRefusedError" in entry["error"]


def test_content_rejection_still_counts_rejected(monkeypatch):
    def bad_text(llm_settings, prompt):
        return "DIRECTION: calm\nSTATE: calm\nTEXT: totally different words"

    monkeypatch.setattr(annotate_exec, "_llm_chat", bad_text)
    cues_map, stats = annotate_chapter(
        CHAPTER, CONTRACT, LLM_SETTINGS, "neutral", "",
        PROMPT_PATH, EXAMPLES_PATH,
    )
    assert stats["rejected"] == 1
    assert stats["llm_errors"] == 0
    assert cues_map["s1"]["cues"] is None
    assert cues_map["s1"]["error"] is None


def test_successful_annotation_counts_directions(monkeypatch):
    def good(llm_settings, prompt):
        return "DIRECTION: calm\nSTATE: calm\nTEXT: Hello world."

    monkeypatch.setattr(annotate_exec, "_llm_chat", good)
    cues_map, stats = annotate_chapter(
        CHAPTER, CONTRACT, LLM_SETTINGS, "neutral", "",
        PROMPT_PATH, EXAMPLES_PATH,
    )
    assert stats["annotated"] == 1
    assert stats["directions"] == 1
    assert cues_map["s1"]["cues"]["direction"] == "calm"


# --- annotated-script error field ------------------------------------------


def test_write_annotated_script_records_error(tmp_path):
    path = tmp_path / "annotated-script.json"
    write_annotated_script(path, "indextts2.5", {
        "s1": {"cues": None, "state_after": None, "error": "ConnectionRefusedError: refused"},
    })
    data = json.loads(path.read_text())
    assert data["segments"]["s1"]["error"] == "ConnectionRefusedError: refused"
    assert data["segments"]["s1"]["cues"] is None


# --- chapter-held directions (#120) -----------------------------------------


CHAPTER_CONTRACT = AnnotationContract(
    engine="indextts2.5",
    instruct_format="{direction}",
    instruct_controls=("calm", "grave"),
    max_direction_words=8,
    retries=0,
    direction_granularity="chapter",
)

MULTI = {"segments": [
    {"id": "s1", "content_type": "body_text", "text": "First."},
    {"id": "s2", "content_type": "body_text", "text": "Second."},
]}


def test_chapter_held_stamps_one_direction_on_every_segment(monkeypatch):
    def one_direction(llm_settings, prompt):
        assert "ENTIRE chapter" in prompt
        assert "calm, grave" in prompt        # the DECLARED vocabulary rides the prompt
        return "DIRECTION: calm, steady and grave"

    monkeypatch.setattr(annotate_exec, "_llm_chat", one_direction)
    cues_map, stats = annotate_chapter(
        MULTI, CHAPTER_CONTRACT, LLM_SETTINGS, "neutral", "",
        PROMPT_PATH, EXAMPLES_PATH,
    )
    assert stats["directions"] == 1
    assert stats["annotated"] == 2
    assert cues_map["s1"]["cues"]["direction"] == "calm, steady and grave"
    assert cues_map["s2"]["cues"]["direction"] == "calm, steady and grave"
    assert cues_map["s1"]["error"] is None


def test_chapter_held_transport_failure_degrades_clean(monkeypatch):
    def dead(llm_settings, prompt):
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(annotate_exec, "_llm_chat", dead)
    cues_map, stats = annotate_chapter(
        MULTI, CHAPTER_CONTRACT, LLM_SETTINGS, "neutral", "",
        PROMPT_PATH, EXAMPLES_PATH,
    )
    assert stats["llm_errors"] == 1
    assert stats["annotated"] == 0
    assert cues_map["s1"]["cues"] is None
    assert "ConnectionRefusedError" in cues_map["s1"]["error"]


def test_chapter_held_malformed_response_counts_rejected(monkeypatch):
    monkeypatch.setattr(annotate_exec, "_llm_chat", lambda s, p: "no labeled line at all")
    cues_map, stats = annotate_chapter(
        MULTI, CHAPTER_CONTRACT, LLM_SETTINGS, "neutral", "",
        PROMPT_PATH, EXAMPLES_PATH,
    )
    assert stats["rejected"] == 1
    assert stats["annotated"] == 0
    assert cues_map["s1"]["cues"] is None


def test_chapter_held_sanitizes_the_direction(monkeypatch):
    monkeypatch.setattr(annotate_exec, "_llm_chat",
                        lambda s, p: "DIRECTION: calm [junk] <evil> and quite gravely steady forever more")
    cues_map, stats = annotate_chapter(
        MULTI, CHAPTER_CONTRACT, LLM_SETTINGS, "neutral", "",
        PROMPT_PATH, EXAMPLES_PATH,
    )
    direction = cues_map["s1"]["cues"]["direction"]
    assert "[" not in direction and "<" not in direction
    assert len(direction.split()) <= 8       # contract word budget


def test_segment_granularity_still_runs_the_serial_loop(monkeypatch):
    calls = []

    def per_segment(llm_settings, prompt):
        calls.append(prompt)
        return "DIRECTION: calm\nSTATE: calm\nTEXT: First."

    monkeypatch.setattr(annotate_exec, "_llm_chat", per_segment)
    cues_map, stats = annotate_chapter(
        MULTI, CONTRACT, LLM_SETTINGS, "neutral", "",
        PROMPT_PATH, EXAMPLES_PATH,
    )
    assert len(calls) == 2                   # one call per segment, as before
