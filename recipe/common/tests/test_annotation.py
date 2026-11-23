"""Tests for the system annotation layer (recipe/common/annotation.py).

The contract: the prompt is built FROM the engine's declared
capabilities; annotations are metadata only (clean text never stored
twice); word fidelity is enforced with reject-and-retry semantics;
undeclared brackets degrade with a count; the instruct is composed per
the declaration's format.
"""

from __future__ import annotations

import pathlib
import textwrap

import pytest

from common.annotation import (
    AnnotationContract,
    AnnotationRejected,
    build_prompt,
    compose_instruct,
    compose_tts_text,
    extract_cues_from_response,
    normalize_words,
    parse_annotation,
    render_simple_template,
    sanitize_direction,
    words_in_order,
)


CAPABILITIES = textwrap.dedent(
    """
    engine: "cosyvoice3"
    annotation_contract:
      inline_tags:
        - tag: "[breath]"
          description: "an audible inhale"
          example: "He drew a long [breath] and went on."
        - tag: "[laughter]"
          description: "a short laugh"
          example: "She tried not to [laughter] laugh."
      instruct_channel:
        format: "You are a helpful assistant. {direction}<|endofprompt|>"
        controls: ["emotions", "speed", "volume", "tone"]
      punctuation_rules:
        - "Add commas for natural breathing pauses"
        - "Use ellipses for trailing off"
      retries: 2
    """
)


@pytest.fixture
def capabilities_file(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "capabilities.yaml"
    path.write_text(CAPABILITIES)
    return path


@pytest.fixture
def contract(capabilities_file: pathlib.Path) -> AnnotationContract:
    return AnnotationContract.from_capabilities(capabilities_file)


class TestContract:
    def test_loads_declared_tags(self, contract: AnnotationContract):
        assert contract.engine == "cosyvoice3"
        assert contract.declared_tag_set == frozenset({"[breath]", "[laughter]"})
        assert contract.configured
        assert contract.retries == 2

    def test_missing_file_disables(self, tmp_path: pathlib.Path):
        empty = AnnotationContract.from_capabilities(tmp_path / "nope.yaml")
        assert not empty.configured
        assert empty.declared_tag_set == frozenset()

    def test_direction_granularity_defaults_to_segment(self, contract: AnnotationContract):
        assert contract.direction_granularity == "segment"

    def test_direction_granularity_chapter_declared(self, tmp_path: pathlib.Path):
        text = CAPABILITIES.replace(
            "  retries: 2\n", "  retries: 2\n  direction_granularity: chapter\n")
        path = tmp_path / "capabilities.yaml"
        path.write_text(text)
        assert AnnotationContract.from_capabilities(path).direction_granularity == "chapter"

    def test_unknown_granularity_falls_back_to_segment(self, tmp_path: pathlib.Path):
        text = CAPABILITIES.replace(
            "  retries: 2\n", "  retries: 2\n  direction_granularity: nonsense\n")
        path = tmp_path / "capabilities.yaml"
        path.write_text(text)
        assert AnnotationContract.from_capabilities(path).direction_granularity == "segment"

    def test_prompt_vocabulary_is_generated_from_declaration(self, contract: AnnotationContract):
        vocab = contract.prompt_vocabulary()
        assert "[breath]" in vocab and "[laughter]" in vocab
        assert "emotions" in vocab  # instruct controls leak into the direction guidance
        assert "[pause]" not in vocab  # never invents vocabulary

    def test_no_tags_declaration_forbids_brackets(self):
        empty = AnnotationContract(engine="x")
        assert "Do NOT add any bracketed markers" in empty.prompt_vocabulary()


class TestValidation:
    def test_words_in_order_with_hint_commas(self):
        src = normalize_words("We foresaw the calamity for we recalled")
        ann = normalize_words("We foresaw the calamity, for we recalled [pause]")
        assert words_in_order(src, ann)

    def test_word_change_fails(self):
        src = normalize_words("the calamity came")
        ann = normalize_words("the calimity came")
        assert not words_in_order(src, ann)


class TestParse:
    def test_labeled_response(self):
        parsed = parse_annotation(
            "DIRECTION: grave and low\n"
            "STATE: reflective, slowing\n"
            "TEXT: He drew a long [breath] and went on.\n"
        )
        assert parsed["direction"] == "grave and low"
        assert parsed["state"] == "reflective, slowing"
        assert parsed["text"] == "He drew a long [breath] and went on."

    def test_unlabeled_body_becomes_text(self):
        parsed = parse_annotation("Just the annotated text, no labels.")
        assert parsed["text"] == "Just the annotated text, no labels."
        assert parsed["direction"] is None


class TestExtractCues:
    def test_declared_tag_anchors_to_source_word(self, contract: AnnotationContract):
        clean = "He drew a long breath and went on."
        parsed = {
            "direction": "slow, weary",
            "state": "weary",
            "text": "He drew a long [breath] and went on.",
        }
        cues = extract_cues_from_response(clean, parsed, contract)
        assert cues["direction"] == "slow, weary"
        assert cues["dropped_undeclared"] == 0
        assert cues["tags"] == [{"tag": "[breath]", "after_word_index": 4}]

    def test_undeclared_brackets_degrade_with_count(self, contract: AnnotationContract):
        clean = "She tried not to laugh."
        parsed = {"direction": None, "state": None,
                  "text": "She tried [pause] not to [laughter] laugh."}
        cues = extract_cues_from_response(clean, parsed, contract)
        assert cues["dropped_undeclared"] == 1
        assert cues["tags"] == [{"tag": "[laughter]", "after_word_index": 4}]
        # Roundtrip: cues composed onto the CLEAN text reproduce the
        # intended TTS input — the returned text is never stored.
        assert compose_tts_text(clean, cues) == "She tried not to [laughter] laugh."

    def test_reworded_chunk_is_rejected(self, contract: AnnotationContract):
        clean = "the calamity came"
        parsed = {"direction": None, "state": None, "text": "the calimity came"}
        with pytest.raises(AnnotationRejected):
            extract_cues_from_response(clean, parsed, contract)

    def test_overlong_direction_dropped(self, contract: AnnotationContract):
        clean = "one two"
        parsed = {"direction": "x" * 300, "state": None, "text": "one two"}
        cues = extract_cues_from_response(clean, parsed, contract)
        assert cues["direction"] is None


class TestCompose:
    def test_compose_tts_text_inserts_declared_tags(self):
        clean = "He drew a long breath and went on."
        cues = {"tags": [{"tag": "[breath]", "after_word_index": 5}], "direction": None}
        assert compose_tts_text(clean, cues) == "He drew a long breath [breath] and went on."

    def test_compose_no_cues_returns_clean_text(self):
        clean = "plain text"
        assert compose_tts_text(clean, None) == clean
        assert compose_tts_text(clean, {"tags": [], "direction": None}) == clean

    def test_compose_instruct_uses_declared_engine_format(self, contract: AnnotationContract):
        out = compose_instruct(contract, "Even delivery, neutral tone.", "grave and low")
        assert out.startswith("You are a helpful assistant. ")
        assert out.endswith("<|endofprompt|>")
        assert "grave and low" in out


    def test_compose_instruct_keeps_directions_short(self, contract: AnnotationContract):
        # #114: out-of-distribution long instructs leak into narration — the
        # module clamps to the declared max_direction_words (12 here).
        long_direction = " ".join(f"word{i}" for i in range(40))
        out = compose_instruct(contract, "Even delivery.", long_direction)
        inner = out.split("You are a helpful assistant. ", 1)[1].rsplit("<|endofprompt|>", 1)[0]
        assert len(inner.split()) <= 3 + contract.max_direction_words


    def test_sanitize_direction_strips_bracket_junk(self):
        assert sanitize_direction("[think] grave, low", 12) == "grave, low"
        assert sanitize_direction("<think> pause", 12) == "pause"
        assert sanitize_direction("a " * 30, 5) == "a a a a a"

    def test_compose_instruct_without_direction_keeps_style(self, contract: AnnotationContract):
        out = compose_instruct(contract, "Even delivery, neutral tone.", None)
        assert "Even delivery, neutral tone" in out


class TestBuildPrompt:
    def test_prompt_assembles_all_documented_inputs(self, contract, tmp_path):
        tpl = tmp_path / "annotation-prompt.j2"
        tpl.write_text(
            "Engine: {{ engine }}\n{{ engine_vocab }}\n{{ output_contract }}\n"
            "Style: {{ style }}\n{{ style_description }}\nType: {{ content_type }}\n"
            "Previous state: {{ previous_state }}\nExamples:\n{{ markup_examples }}\n"
            "Text:\n{{ text }}\n"
        )
        examples = tmp_path / "markup-examples.md"
        examples.write_text("He drew a long [breath] and went on.")
        prompt = build_prompt(
            contract, tpl, examples,
            style="neutral",
            style_description="Even delivery, neutral tone.",
            content_type="body_text",
            previous_state="tense, quick",
            chunk_text="The war which today seems permanent will one day come to an end.",
        )
        assert "[breath]" in prompt          # from the declaration
        assert "DIRECTION:" in prompt        # output contract
        assert "tense, quick" in prompt      # previous chunk state
        assert "neutral" in prompt           # style
        assert "come to an end" in prompt    # chunk text
        assert "He drew a long" in prompt    # markup examples
        assert "[pause]" not in prompt       # never invents vocabulary

    def test_render_simple_template_missing_vars_empty(self):
        assert render_simple_template("A {{ x }} B", {}) == "A  B"
