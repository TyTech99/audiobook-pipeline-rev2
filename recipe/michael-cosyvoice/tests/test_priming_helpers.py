"""Unit tests for the #115 rolling-reference priming helpers.

Pure inputs only — no torch, no engine. The silence scan and tail cut
are deterministic, so synthetic waveforms pin them down exactly.
"""

import sys
import pathlib

# Same import bootstrap as test_recipe_helpers.py: recipe root for `run`,
# recipe/ parent for `common.annotation` (run.py imports it at top).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))

from run import (  # noqa: E402
    priming_enabled,
    priming_tail_ms,
    prime_tail_slice,
    silence_gaps,
    split_sentences,
    window_rms_db,
)


def _speech(seconds: float, sr: int, amp: float = 0.3) -> list[float]:
    return [amp] * int(seconds * sr)


def _silence(seconds: float, sr: int) -> list[float]:
    return [0.0] * int(seconds * sr)


def test_split_sentences_round_trip():
    assert split_sentences("One. Two! Three?") == ["One.", "Two!", "Three?"]
    assert split_sentences("  ") == []


def test_window_rms_db_speech_vs_silence():
    sr = 1000
    loud = window_rms_db(_speech(0.2, sr), sr)
    quiet = window_rms_db(_silence(0.2, sr), sr)
    assert all(v > -20 for v in loud)
    assert all(v < -100 for v in quiet)


def test_silence_gaps_counts_inter_sentence_pauses():
    sr = 1000
    samples = _speech(1.0, sr) + _silence(0.1, sr) + _speech(1.0, sr) + _silence(0.1, sr) + _speech(1.0, sr)
    gaps = silence_gaps(window_rms_db(samples, sr))
    assert len(gaps) == 2


def test_prime_tail_slice_whole_chunk_when_short():
    sr = 1000
    samples = _speech(5.0, sr)  # 5s <= 7.5s cap: whole chunk is the pair
    assert prime_tail_slice(samples, sr, ["One."], 6000) == (0, 1)


def test_prime_tail_slice_picks_boundary_nearest_target():
    sr = 1000
    # 9.2s total: 3s speech, 0.1 pause, 3s, 0.1 pause, 3s
    samples = _speech(3.0, sr) + _silence(0.1, sr) + _speech(3.0, sr) + _silence(0.1, sr) + _speech(3.0, sr)
    fragments = ["One.", "Two.", "Three."]
    # tails from the two boundaries: 6.1s and 3.0s; target 6000 -> boundary 1
    assert prime_tail_slice(samples, sr, fragments, 6000) == (3100, 2)
    # target 4000 -> boundary 2 (distance 1000 vs 2100)
    assert prime_tail_slice(samples, sr, fragments, 4000) == (6200, 1)


def test_prime_tail_slice_rejects_pause_count_mismatch():
    sr = 1000
    samples = _speech(3.0, sr) + _silence(0.1, sr) + _speech(3.0, sr) + _silence(0.1, sr) + _speech(3.0, sr)
    # 4 fragments but only 2 detected boundaries: mapping is not 1:1 -> None
    assert prime_tail_slice(samples, sr, ["One.", "Two.", "Three.", "Four."], 6000) is None


def test_prime_tail_slice_rejects_when_all_tails_too_short():
    sr = 1000
    # 8.4s total but every trailing span is under the 3s in-range floor
    samples = _speech(7.0, sr) + _silence(0.1, sr) + _speech(1.0, sr) + _silence(0.1, sr) + _speech(0.2, sr)
    assert prime_tail_slice(samples, sr, ["One.", "Two.", "Three."], 6000) is None


def test_prime_tail_slice_rejects_empty_inputs():
    assert prime_tail_slice([], 1000, ["One."], 6000) is None
    assert prime_tail_slice(_speech(9.0, 1000), 1000, [], 6000) is None


def test_priming_declaration_read_from_recipe_yaml():
    # recipe.yaml declares priming: true / priming_tail_ms: 6000 (#115)
    assert priming_tail_ms() == 6000
    assert priming_enabled() is True
