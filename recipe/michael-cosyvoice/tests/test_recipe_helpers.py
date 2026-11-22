"""Unit tests for pure helpers in run.py (no torch/cosyvoice required)."""

import sys
import pathlib

# Make run.py importable without installing the recipe package, and the
# system annotation module (recipe/common) importable the same way the
# recipe image does it (PYTHONPATH=<recipe root> -> common.annotation).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))

from run import (
    chunk_text,
    normalize_for_comparison,
    validate_annotation,
    first_annotation_mismatch,
    style_base,
    synthesis_mode,
    zero_shot_prompt_text,
    CAPABILITIES_PATH,
    SYSTEM_PROMPT,
    ENDOFPROMPT,
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
    # Word cores only: punctuation strips, intra-word apostrophes stay.
    assert normalize_for_comparison("  A.B  ") == "a b"
    assert normalize_for_comparison("calamity,") == "calamity"
    assert normalize_for_comparison("don't") == "don't"


def test_validate_annotation_preserves_words():
    assert validate_annotation("Hello world", "Hello [pause] world") is True
    assert validate_annotation("Hello world", "Hello [emphasis] world") is True


def test_validate_annotation_allows_hint_punctuation():
    # Live #110 case: the model glues pause-commas to source words —
    # every word preserved in order must still validate.
    src = "We foresaw the calamity for we recalled the causes from which it sprang"
    ann = "We foresaw the calamity, for we recalled the causes from which it sprang [pause]"
    assert validate_annotation(src, ann) is True
    assert first_annotation_mismatch(src, ann) is None


def test_validate_annotation_rejects_missing_words():
    assert validate_annotation("Hello world", "Hello there") is False
    assert first_annotation_mismatch("Hello world", "Hello there") == "world"


def test_validate_annotation_rejects_changed_words():
    assert validate_annotation("the calamity came", "the calimity came") is False
    assert first_annotation_mismatch("the calamity came", "the calimity came") == "calamity"


def test_validate_annotation_empty_source():
    assert validate_annotation("", "anything") is True


def test_style_base_is_raw_direction_text():
    base = style_base("neutral")
    assert base.startswith("Even delivery")
    assert SYSTEM_PROMPT not in base
    assert ENDOFPROMPT not in base


def test_style_base_unknown_style_falls_back_to_neutral():
    assert style_base("does-not-exist") == style_base("neutral")


def test_style_base_flattens_lines():
    base = style_base("noir-mystery")
    assert "\n" not in base
    assert "gravelly" in base


def test_engine_contract_declares_cosyvoice_tags():
    # The declaration the SYSTEM reads (recipe.yaml -> engine/capabilities.yaml):
    # CosyVoice 3's real inline tags plus the instruct channel format.
    from common.annotation import AnnotationContract
    contract = AnnotationContract.from_capabilities(CAPABILITIES_PATH)
    assert contract.engine == "cosyvoice3"
    assert contract.declared_tag_set == frozenset({"[breath]", "[laughter]"})
    assert "{direction}" in contract.instruct_format
    assert "emotions" in contract.instruct_controls


def test_compose_instruct_uses_declared_engine_format():
    # Trace: declaration -> prompt/compose behavior. The per-segment
    # instruct wraps per the engine's declared format (#112).
    from common.annotation import AnnotationContract, compose_instruct
    contract = AnnotationContract.from_capabilities(CAPABILITIES_PATH)
    out = compose_instruct(contract, style_base("neutral"), "grave and low")
    assert out.startswith("You are a helpful assistant. ")
    assert out.endswith(ENDOFPROMPT)
    assert "Even delivery" in out and "grave and low" in out


def test_synthesis_mode_and_zero_shot_prompt():
    # The declared mode is the model card's canonical zero-shot shape:
    # no space before the endofprompt token, transcript after it.
    import run as run_module
    assert synthesis_mode() == "zero_shot"
    prompt = zero_shot_prompt_text()
    assert prompt.startswith("You are a helpful assistant.<|endofprompt|>")
    assert "brighter place" in prompt  # the committed reference transcript
    monkeypatch_manifest = {"tts": {"synthesis_mode": "instruct2"}}
    saved = run_module.MANIFEST
    try:
        run_module.MANIFEST = monkeypatch_manifest
        assert synthesis_mode() == "instruct2"
    finally:
        run_module.MANIFEST = saved


def test_engine_flags_default_on(monkeypatch):
    monkeypatch.delenv("RECIPE_FP16", raising=False)
    monkeypatch.delenv("RECIPE_TRT", raising=False)
    assert engine_flags() == {"fp16": True, "load_trt": True}


def test_engine_flags_kill_switches(monkeypatch):
    monkeypatch.setenv("RECIPE_FP16", "0")
    monkeypatch.setenv("RECIPE_TRT", "0")
    assert engine_flags() == {"fp16": False, "load_trt": False}


def test_engine_flags_follow_the_declaration(monkeypatch):
    # #114 follow-up: recipe.yaml tts.engine_flags is the iteration lever;
    # the env kill switches still override.
    import run as run_module
    monkeypatch.delenv("RECIPE_FP16", raising=False)
    monkeypatch.delenv("RECIPE_TRT", raising=False)
    monkeypatch.setattr(
        run_module, "MANIFEST",
        {"tts": {"engine_flags": {"fp16": False, "trt_flow": False}}},
    )
    assert engine_flags() == {"fp16": False, "load_trt": False}
    monkeypatch.setenv("RECIPE_TRT", "True")  # env overrides the declaration
    assert engine_flags() == {"fp16": False, "load_trt": True}


def test_parallel_workers_env(monkeypatch):
    monkeypatch.delenv("RECIPE_PARALLEL", raising=False)
    assert parallel_workers() == 2
    monkeypatch.setenv("RECIPE_PARALLEL", "1")
    assert parallel_workers() == 1
    monkeypatch.setenv("RECIPE_PARALLEL", "8")
    assert parallel_workers() == 8
    monkeypatch.setenv("RECIPE_PARALLEL", "junk")
    assert parallel_workers() == 2
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
