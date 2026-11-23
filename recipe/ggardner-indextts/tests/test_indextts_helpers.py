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
    assert run.chaining_enabled() is False   # #119 verdict: chained references drift — declaration off


def test_contract_declares_chapter_held_directions():
    contract = AnnotationContract.from_capabilities(CAPABILITIES)
    assert contract.direction_granularity == "chapter"   # #120: one held direction per chapter


def test_engine_declarations_for_120():
    assert run.declared_segment_tokens() == 80        # explicit English budget (upstream #775)
    assert run.declared_interval_silence_ms() == 300
    assert run.declared_seam_pause_ms() == 300


def test_infer_guarded_escalates_runtime_warning():
    import warnings

    class UnstableEngine:
        def infer(self, **kwargs):
            warnings.warn("The tokens length of segment exceeds limit: 120", RuntimeWarning)

    raised = False
    try:
        run._infer_guarded(UnstableEngine(), {})
    except RuntimeError as exc:
        raised = "tokens length" in str(exc)
    assert raised


def test_infer_guarded_passes_clean_engine():
    class CleanEngine:
        def __init__(self):
            self.seen = None

        def infer(self, **kwargs):
            self.seen = kwargs

    eng = CleanEngine()
    run._infer_guarded(eng, {"a": 1})
    assert eng.seen == {"a": 1}


def test_prepend_seam_pauses(monkeypatch, tmp_path):
    chdir = tmp_path / "ch001"
    chdir.mkdir()

    def fake_run(cmd, check, capture_output, text):
        assert "adelay=500:all=1" in cmd          # seam_pause 300 + overlap 200
        pathlib.Path(cmd[-1]).write_bytes(b"pad")

    monkeypatch.setattr(run.subprocess, "run", fake_run)
    wavs = [tmp_path / "a.wav", tmp_path / "b.wav", tmp_path / "c.wav"]
    seq = run.prepend_seam_pauses(wavs, chdir, 1)
    assert seq[0] == wavs[0]                     # the first chunk is untouched
    assert len(seq) == 3
    assert seq[1].exists() and seq[2].exists()    # later chunks are padded copies


def test_prepend_seam_pauses_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "declared_seam_pause_ms", lambda: 0)
    wavs = [tmp_path / "a.wav", tmp_path / "b.wav"]
    assert run.prepend_seam_pauses(wavs, tmp_path, 1) == wavs


def test_next_reference_carries_the_last_good_take():
    assert run.next_reference("/tmp/ch100/seg-001.wav", True) == "/tmp/ch100/seg-001.wav"
    assert run.next_reference(None, True) == str(run.VOICE_PATH)               # chain start: canonical re-anchor
    assert run.next_reference("/tmp/ch100/seg-001.wav", False) == str(run.VOICE_PATH)  # kill switch: canonical


def test_run_chain_counters_measure_tone_carry(monkeypatch, tmp_path):
    calls = []

    def fake_generate(model, text, out_wav, direction, seed, emo_alpha, df, qwen, spk_prompt=None, **kwargs):
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

    def fake_generate(model, text, out_wav, direction, seed, emo_alpha, df, qwen, spk_prompt=None, **kwargs):
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


def test_declared_emotion_mode_defaults_and_validates():
    import run
    # default from manifest (emo_text)
    assert run.declared_emotion_mode() in ("emo_text", "emo_vector")
    # env override
    import os
    os.environ["RECIPE_EMOTION_MODE"] = "emo_vector"
    assert run.declared_emotion_mode() == "emo_vector"
    os.environ["RECIPE_EMOTION_MODE"] = "garbage"
    assert run.declared_emotion_mode() == "emo_text"
    del os.environ["RECIPE_EMOTION_MODE"]


def test_declared_emotion_vector_clamps_and_length():
    import run, os
    os.environ["RECIPE_EMOTION_VECTOR"] = "2.0, -1, 0, 0, 0, 0.2, 0, 0.8, 9"  # 9 -> fallback
    v = run.declared_emotion_vector()
    assert len(v) == 8
    # fallback to default length 8
    assert v == run._DEFAULT_EMOTION_VECTOR
    os.environ["RECIPE_EMOTION_VECTOR"] = "0.3,0,0.2,0,0,0.4,0.15,0.3"
    v = run.declared_emotion_vector()
    assert len(v) == 8
    assert v[0] == 0.3 and v[5] == 0.4
    # clamp
    os.environ["RECIPE_EMOTION_VECTOR"] = "1.5,0,0,0,0,0,0,0"
    v = run.declared_emotion_vector()
    assert v[0] == 1.2
    del os.environ["RECIPE_EMOTION_VECTOR"]
