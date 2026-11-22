"""Unit tests for the ggardner-indextts recipe helpers (no torch needed).

The heart of this file is the annotation-contract check: the engine's
declared vocabulary is EXACTLY what IndexTTS-2.5 consumes — no inline
tags (this engine reads brackets aloud) and the 8-emotion direction
channel that flows into emo_text.
"""

import sys
import pathlib

# Make run.py importable without installing the recipe package, and the
# system annotation module (recipe/common) importable the same way the
# recipe image does it (PYTHONPATH=<recipe root> -> common.annotation).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))

# Suite-order isolation: another recipe's tests may have already cached
# a module named "run" — purge it so THIS recipe's run.py is imported.
sys.modules.pop("run", None)

from common.annotation import AnnotationContract, sanitize_direction  # noqa: E402
import run  # noqa: E402


CAPABILITIES = run.CAPABILITIES_PATH


def test_contract_declares_no_inline_tags():
    contract = AnnotationContract.from_capabilities(CAPABILITIES)
    assert contract.configured is True          # instruct channel declared
    assert contract.inline_tags == ()           # IndexTTS reads brackets aloud — none declared
    assert contract.declared_tag_set == frozenset()


def test_contract_declares_exactly_the_engine_emotions():
    contract = AnnotationContract.from_capabilities(CAPABILITIES)
    assert sorted(contract.instruct_controls) == [
        "afraid", "angry", "calm", "disgusted",
        "happy", "melancholic", "sad", "surprised",
    ]
    assert contract.max_direction_words == 8
    assert contract.engine == "indextts2.5"


def test_direction_from_cues_sanitizes_and_passes_through():
    cues = {"direction": "somber, quietly resolute"}
    out = run.direction_from_cues(cues, max_words=8)
    assert out is not None and "somber" in out and "[a" not in out


def test_direction_from_cues_none_cases():
    assert run.direction_from_cues(None, 8) is None
    assert run.direction_from_cues({}, 8) is None
    assert run.direction_from_cues({"direction": ""}, 8) is None
    assert run.direction_from_cues({"direction": "<|junk|>"}, 8) is None or "[" not in str(run.direction_from_cues({"direction": "<|junk|>"}, 8))


def test_declarations_read_from_recipe_yaml():
    assert run.declared_speed() == 1.0          # duration_factor: Gardner's own pace
    assert run.declared_emo_alpha() == 0.8
    assert run.parallel_workers() == 2
    assert run._declared_voice == "voices/ggardner.wav"
    assert run.INDEXTTS_REVISION == "c39ce5ba981572cb187443877ff559dfb246ce63"


def test_reference_and_transcript_ship_with_the_recipe():
    assert run.VOICE_PATH.exists()
    assert pathlib.Path(str(run.VOICE_PATH) + ".transcript.txt").exists()


def test_sanitize_direction_clamps_to_word_budget():
    out = sanitize_direction("a very long direction with many many words in it", 4)
    assert len(out.split()) <= 4


def test_chaining_declaration_read_from_recipe_yaml():
    assert run.chaining_enabled() is True


def test_next_reference_carries_the_last_good_take():
    assert run.next_reference("/tmp/ch100/seg-001.wav", True) == "/tmp/ch100/seg-001.wav"
    assert run.next_reference(None, True) == str(run.VOICE_PATH)               # chain start: canonical re-anchor
    assert run.next_reference("/tmp/ch100/seg-001.wav", False) == str(run.VOICE_PATH)  # kill switch: canonical


def test_run_chain_counters_measure_tone_carry(monkeypatch, tmp_path):
    calls = []

    def fake_generate(model, text, out_wav, direction, seed, emo_alpha, df, qwen, spk_prompt=None):
        calls.append(spk_prompt)
        pathlib.Path(out_wav).write_bytes(b"wav")
        if "boom" in text:
            raise RuntimeError("boom")

    monkeypatch.setattr(run, "generate_chunk", fake_generate)
    chain = [
        (0, "one", None, str(tmp_path / "a.wav"), 1),
        (1, "two boom", None, str(tmp_path / "b.wav"), 1),
        (2, "three", None, str(tmp_path / "c.wav"), 1),
    ]
    results, counts = run._run_chain(None, chain, False, 8, 0.8, 1.0, True)
    assert results[0] is None and results[1] and results[2] is None
    assert counts["chained"] == 2 and counts["re_anchor"] == 1
    assert calls[0] == str(run.VOICE_PATH)           # chain start = canonical
    assert calls[1] == str(tmp_path / "a.wav")       # primes from the last good take
    assert calls[2] == str(tmp_path / "a.wav")       # a failed chunk does not break the chain


def test_run_chain_with_chaining_off_is_canonical_everywhere(monkeypatch, tmp_path):
    calls = []

    def fake_generate(model, text, out_wav, direction, seed, emo_alpha, df, qwen, spk_prompt=None):
        calls.append(spk_prompt)
        pathlib.Path(out_wav).write_bytes(b"wav")

    monkeypatch.setattr(run, "generate_chunk", fake_generate)
    chain = [
        (0, "one", None, str(tmp_path / "a.wav"), 1),
        (1, "two", None, str(tmp_path / "b.wav"), 1),
    ]
    results, counts = run._run_chain(None, chain, False, 8, 0.8, 1.0, False)
    assert results[0] is None and results[1] is None
    assert counts["chained"] == 0 and counts["re_anchor"] == 2
    assert calls == [str(run.VOICE_PATH), str(run.VOICE_PATH)]  # regression guard: pre-#119 behavior
