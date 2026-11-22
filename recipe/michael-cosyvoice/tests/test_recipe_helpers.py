"""Unit tests for pure helpers in run.py (no torch/cosyvoice required)."""

import sys
import pathlib

# Make run.py importable without installing the recipe package.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from run import (
    chunk_text,
    normalize_for_comparison,
    validate_annotation,
    strip_performance_hints,
    style_instruct,
    engine_flags,
    parallel_workers,
    chapter_filename,
    output_layout,
    SYSTEM_PROMPT,
    ENDOFPROMPT,
)


def test_chunk_text_short():
    assert chunk_text("Hello world.") == ["Hello world."]


def test_chunk_text_empty():
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_chunk_text_respects_max_chars():
    text = "One two three. Four five six. Seven eight nine."
    chunks = chunk_text(text, max_chars=20)
    assert all(len(c) <= 20 for c in chunks)
    # All words preserved in order
    joined = " ".join(chunks)
    assert "One two three" in joined
    assert "Four five six" in joined


def test_chunk_text_splits_long_sentence():
    long_sentence = "word " * 50  # 299 chars, no sentence boundary
    chunks = chunk_text(long_sentence.strip(), max_chars=100)
    assert all(len(c) <= 100 for c in chunks)
    assert len(chunks) > 1


def test_normalize_for_comparison():
    assert normalize_for_comparison("Hello   World") == "hello world"
    assert normalize_for_comparison("  A.B  ") == "a.b"


def test_validate_annotation_preserves_words():
    assert validate_annotation("Hello world", "Hello [pause] world") is True
    assert validate_annotation("Hello world", "Hello [emphasis] world") is True


def test_validate_annotation_rejects_missing_words():
    assert validate_annotation("Hello world", "Hello there") is False


def test_validate_annotation_empty_source():
    assert validate_annotation("", "anything") is True


def test_strip_performance_hints_removes_hint_brackets():
    assert strip_performance_hints("Hello [pause] world") == "Hello world"
    assert strip_performance_hints("[emphasis] Wait...") == "Wait..."
    assert strip_performance_hints("A [softly] quiet [whisper] line.") == "A quiet line."


def test_strip_performance_hints_keeps_engine_tags():
    # [breath]/[laughter] are engine-native inline tags, not annotation
    # hints — they must survive the strip (walk phase will emit them).
    text = "He drew a [breath] and went on."
    assert strip_performance_hints(text) == text
    text2 = "She nearly [laughter] laughed."
    assert strip_performance_hints(text2) == text2


def test_strip_performance_hints_all_hints_returns_original():
    # Stripping everything would empty the segment; the original is kept.
    text = "[pause][emphasis]"
    assert strip_performance_hints(text) == text


def test_style_instruct_wraps_in_engine_format():
    instruct = style_instruct("neutral")
    assert instruct.startswith(SYSTEM_PROMPT + " ")
    assert instruct.endswith(ENDOFPROMPT)
    assert "Even delivery" in instruct


def test_style_instruct_unknown_style_falls_back_to_neutral():
    assert style_instruct("does-not-exist") == style_instruct("neutral")


def test_style_instruct_flattens_lines():
    instruct = style_instruct("noir-mystery")
    assert "\n" not in instruct
    assert "gravelly" in instruct


def test_engine_flags_default_on(monkeypatch):
    monkeypatch.delenv("RECIPE_FP16", raising=False)
    monkeypatch.delenv("RECIPE_TRT", raising=False)
    assert engine_flags() == {"fp16": True, "load_trt": True}


def test_engine_flags_kill_switches(monkeypatch):
    monkeypatch.setenv("RECIPE_FP16", "0")
    monkeypatch.setenv("RECIPE_TRT", "0")
    assert engine_flags() == {"fp16": False, "load_trt": False}


def test_parallel_workers_env(monkeypatch):
    monkeypatch.delenv("RECIPE_PARALLEL", raising=False)
    assert parallel_workers() == 3
    monkeypatch.setenv("RECIPE_PARALLEL", "1")
    assert parallel_workers() == 1
    monkeypatch.setenv("RECIPE_PARALLEL", "8")
    assert parallel_workers() == 8
    monkeypatch.setenv("RECIPE_PARALLEL", "junk")
    assert parallel_workers() == 3
    monkeypatch.setenv("RECIPE_PARALLEL", "0")
    assert parallel_workers() == 1


def test_chapter_filename():
    assert chapter_filename(1) == "chapter-001.mp3"
    assert chapter_filename(12) == "chapter-012.mp3"
    assert chapter_filename(123) == "chapter-123.mp3"


def test_output_layout():
    layout = output_layout()
    assert layout["m4b"].name == "audiobook.m4b"
    assert layout["mp3_dir"].name == "mp3"
    assert layout["opening_credits"].name == "opening-credits.mp3"
    assert layout["closing_credits"].name == "closing-credits.mp3"
    assert layout["retail_sample"].name == "retail-sample.mp3"
    assert layout["metadata"].name == "metadata.json"
    assert layout["qc_report"].name == "qc-report.json"
