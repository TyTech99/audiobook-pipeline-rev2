"""Unit tests for pure helpers in run.py (no torch/chatterbox required)."""

import sys
import pathlib

# Make run.py importable without installing the recipe package.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from run import (
    chunk_text,
    normalize_for_comparison,
    validate_annotation,
    chapter_filename,
    output_layout,
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
