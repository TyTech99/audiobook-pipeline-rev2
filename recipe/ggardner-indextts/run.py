"""Grover Gardner voice recipe on IndexTTS-2.5.

Runs inside the recipe Docker container:
  /input  <- book package (script.json, cover.jpg, metadata.json, ...)
  /output <- audiobook package (m4b/, mp3/, metadata.json, qc-report.json)

Stages:
  1. LLM annotation (optional, LLM_BASE_URL + LLM_MODEL set): the
     SYSTEM layer builds the prompt from the engine's DECLARED
     capabilities (engine/capabilities.yaml annotation_contract) — this
     engine has NO inline tags, so the contract declares none and the
     prompt forbids bracketed markers outright; per-segment DIRECTION
     phrases (over the engine's 8-emotion vocabulary) are metadata cues
     keyed by segment ID. Segments serial with state passing inside a
     chapter; chapters are emotional reset points and annotate in
     parallel bounded by the declared LLM seats.
  2. IndexTTS-2.5 synthesis: zero-shot cloning from the reference
     clip (voices/ggardner.wav — timbre AND its natural delivery).
     Tone carry (#119): each chapter chain starts at the canonical
     reference (chapter = emotional reset point) and every later chunk
     primes from the last good take — the engine officially does not
     model prosody across segment boundaries, so the reference channel
     is the only tone carrier (the engine head-truncates any reference
     to 15 s, bounding each prime). Each chunk's annotation direction
     flows verbatim into the engine's emo_text channel (its bundled
     QwenEmotion translates to the 8-dim vector); emo_alpha grades
     intensity; duration_factor 1.0 keeps the reference's own pace.
     Parallelism is per CHAPTER CHAIN — chapters in parallel, chunks
     serial within each (the documented model). The seed is re-applied
     per call.
  3. Mastering (ffmpeg loudnorm to -18 LUFS + room tone).
  4. Packaging (M4B with stik=2 + nero chapters, per-chapter MP3s,
     credits, retail sample).
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any

import yaml

from common.annotation import AnnotationContract, sanitize_direction
from common.annotate_exec import (
    annotate_chapter,
    load_llm_settings,
    merge_annotation_stats,
    probe_llm,
    write_annotated_script,
)
from common.audio import (
    acx_verdict,
    concat_wavs,
    crossfade_join,
    get_audio_duration,
    make_silence_wav,
    master_wav,
    room_tone_af,
    trim_to_duration,
    wav_to_mp3,
)
from common.pkg import build_m4b_from_wav, chapter_filename, output_layout
from common.text import chunk_text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("recipe")

RECIPE_DIR = pathlib.Path(__file__).resolve().parent
INPUT_DIR = pathlib.Path(os.environ.get("INPUT_DIR", "/input"))
OUTPUT_DIR = pathlib.Path(os.environ.get("OUTPUT_DIR", "/output"))
STYLES_DIR = RECIPE_DIR / "styles"


def recipe_manifest() -> dict[str, Any]:
    try:
        return yaml.safe_load((RECIPE_DIR / "recipe.yaml").read_text()) or {}
    except Exception:
        return {}


MANIFEST = recipe_manifest()
ENGINE_MANIFEST = MANIFEST.get("engine") or {}
CAPABILITIES_PATH = RECIPE_DIR / ENGINE_MANIFEST.get("capabilities", "engine/capabilities.yaml")
ANNOTATION_PROMPT_PATH = RECIPE_DIR / ENGINE_MANIFEST.get("annotation_prompt", "engine/annotation-prompt.j2")
MARKUP_EXAMPLES_PATH = RECIPE_DIR / ENGINE_MANIFEST.get("markup_examples", "engine/markup-examples.md")

# Voice reference — DECLARED (recipe.yaml tts.voice): a voice swap is a
# declaration edit, never a code edit.
_declared_voice = str((MANIFEST.get("tts") or {}).get("voice") or "voices/ggardner.wav")
VOICE_PATH = RECIPE_DIR / _declared_voice

TTS_SAMPLE_RATE = 22050      # IndexTTS-2.5 native output
PACK_SAMPLE_RATE = 44100
MP3_BITRATE = "192k"
AAC_BITRATE = "96k"
MAX_CHARS_PER_TTS = 300
RETAIL_SAMPLE_SECONDS = 60   # iteration convention (Ty, 2026-11-22): 60s samples

# Pinned weights: same input + same recipe = same model. The QwenEmotion
# 0.6B ships inside this snapshot (qwen0.6bemo4-merge/).
INDEXTTS_REPO = "IndexTeam/IndexTTS-2.5"
INDEXTTS_REVISION = "c39ce5ba981572cb187443877ff559dfb246ce63"

DEFAULT_SEED = 1234


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable without torch)
# ---------------------------------------------------------------------------


def declared_speed() -> float:
    """duration_factor — INVERTED vs CosyVoice: >1.0 SLOWS, <1.0 speeds.

    1.0 keeps the reference clip's own pace (the flat-clip era tuned
    CosyVoice speed 0.9 for a 'too fast' complaint; that lever does not
    transfer to this engine's semantics)."""
    try:
        value = float((MANIFEST.get("tts") or {}).get("duration_factor", 1.0))
    except (TypeError, ValueError):
        value = 1.0
    return max(0.5, min(2.0, value))


def declared_emo_alpha() -> float:
    """Emotion conditioning strength [0,1] (tts.emo_alpha, default 0.8 —
    the engine's own demonstrated mid grade)."""
    try:
        value = float((MANIFEST.get("tts") or {}).get("emo_alpha", 0.8))
    except (TypeError, ValueError):
        value = 0.8
    return max(0.0, min(1.0, value))


_EMOTION_MODES = ("emo_text", "emo_vector")
_DEFAULT_EMOTION_VECTOR = [0.0, 0.0, 0.0, 0.0, 0.0, 0.2, 0.0, 0.8]


def declared_emotion_mode() -> str:
    """Which emotion channel drives synthesis: 'emo_text' (QwenEmotion
    translates the annotation direction to the 8-dim vector — the
    default) or 'emo_vector' (a declared 8-dim vector passed directly,
    graded control bypassing QwenEmotion). Env override RECIPE_EMOTION_MODE."""
    declared = (MANIFEST.get("tts") or {}).get("emotion_mode", "emo_text")
    env = os.environ.get("RECIPE_EMOTION_MODE", str(declared))
    mode = str(env).strip().lower()
    if mode not in _EMOTION_MODES:
        mode = "emo_text"
    return mode


def declared_emotion_vector() -> list[float]:
    """The 8-dim emotion vector [happy, angry, sad, afraid, disgusted,
    melancholic, surprised, calm] used when emotion_mode == 'emo_vector'.
    Each entry clamped to [0, 1.2] (engine range). Env override
    RECIPE_EMOTION_VECTOR (comma-separated 8 floats)."""
    raw = os.environ.get("RECIPE_EMOTION_VECTOR")
    if raw is None:
        raw = (MANIFEST.get("tts") or {}).get("emotion_vector", _DEFAULT_EMOTION_VECTOR)
    try:
        if isinstance(raw, str):
            parts = [p.strip() for p in raw.split(",") if p.strip()]
            vals = [float(p) for p in parts]
        else:
            vals = [float(v) for v in raw]
    except (TypeError, ValueError):
        vals = list(_DEFAULT_EMOTION_VECTOR)
    if len(vals) != 8:
        vals = list(_DEFAULT_EMOTION_VECTOR)
    return [max(0.0, min(1.2, v)) for v in vals]


def bf16_enabled() -> bool:
    declared = (MANIFEST.get("tts") or {}).get("engine_flags", {}).get("bf16", True)
    env = os.environ.get("RECIPE_BF16", "1" if declared else "0")
    return env not in ("0", "false", "False")


def qwen_emo_enabled() -> bool:
    declared = (MANIFEST.get("tts") or {}).get("engine_flags", {}).get("qwen_emo", True)
    env = os.environ.get("RECIPE_QWEN_EMO", "1" if declared else "0")
    return env not in ("0", "false", "False")


def parallel_workers() -> int:
    """Chapter chains in flight (the documented parallelism model)."""
    try:
        return max(1, int(os.environ.get("RECIPE_PARALLEL", str(
            (MANIFEST.get("tts") or {}).get("engine_flags", {}).get("parallel_workers", 2)))))
    except ValueError:
        return 2


def chaining_enabled() -> bool:
    """Tone carry (#119): the tts.chaining declaration with a
    RECIPE_CHAINING env kill switch. With chaining off, every chunk
    re-anchors on the canonical clip (the pre-#119 behavior)."""
    declared = (MANIFEST.get("tts") or {}).get("chaining", True)
    env = os.environ.get("RECIPE_CHAINING", "1" if declared else "0")
    return env not in ("0", "false", "False")


def next_reference(last_good: str | None, chaining: bool) -> str:
    """The speaker reference for the next chunk: the last good take when
    tone carry is on and one exists, else the canonical clip. The engine
    officially does not model prosody across segment boundaries (HF model
    card) and head-truncates any reference to 15 s (infer_v2_5.py:627),
    which bounds each prime. Pure — unit-tested without torch."""
    if chaining and last_good:
        return last_good
    return str(VOICE_PATH)


def declared_segment_tokens() -> int:
    """#120: the explicit English token budget per engine segment. Upstream's
    120 default is Chinese-calibrated (issue #775: English runs ~3.1 tokens/s
    vs Chinese ~4.3, so the same budget yields ~1.4x longer English audio,
    brushing the ~25-30s stability ceiling); the official recommended range
    is 80-200."""
    try:
        value = int((MANIFEST.get("tts") or {}).get("segment_tokens", 80))
    except (TypeError, ValueError):
        value = 80
    return max(20, min(200, value))


def declared_interval_silence_ms() -> int:
    """#120: the pause the engine inserts between its internal segments —
    a deliberate reader's pause, not the 200ms default splice."""
    try:
        value = int((MANIFEST.get("tts") or {}).get("interval_silence_ms", 300))
    except (TypeError, ValueError):
        value = 300
    return max(0, min(2000, value))


def declared_seam_pause_ms() -> int:
    """#120: silence inserted at chunk joins so a seam reads as a reader's
    paragraph pause (crossfaded on both edges)."""
    try:
        value = int((MANIFEST.get("tts") or {}).get("seam_pause_ms", 300))
    except (TypeError, ValueError):
        value = 300
    return max(0, min(2000, value))


def prepend_seam_pauses(wav_list: list[pathlib.Path], temp_dir: pathlib.Path,
                        chapter: int) -> list[pathlib.Path]:
    """#120: chunk joins become deliberate reader pauses. Each chunk after
    the first is prefixed with (seam_pause_ms + overlap_ms) of silence via
    ffmpeg adelay, so the crossfade consumes its overlap INSIDE the pause
    and the net inter-speech gap equals the declared seam_pause_ms.

    Never feed short gap wavs into the pairwise acrossfade chain instead:
    the recipe image's ffmpeg 7.1.5 TRUNCATES the accumulated stream when
    the next input is sub-second (measured 2026-11-22: interleaved 0.3s
    gap files collapsed a ~190s chapter chain to ~11s with every ffmpeg
    call exiting 0). Padded wavs are full-length, the shape the chain has
    always handled correctly. Pure file-planning; padded wavs land in the
    chapter temp dir (removed with it at the end of the run)."""
    pause_ms = declared_seam_pause_ms()
    overlap_ms = declared_overlap_ms()
    if len(wav_list) <= 1 or pause_ms <= 0:
        return wav_list
    out = [wav_list[0]]
    delay = pause_ms + max(0, overlap_ms)
    for i, wav in enumerate(wav_list[1:]):
        padded = temp_dir / f"seampad-{int(chapter):03d}-{i:03d}.wav"
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
               "-i", str(wav), "-af", f"adelay={delay}:all=1",
               "-ar", str(TTS_SAMPLE_RATE), "-ac", "1", str(padded)]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        out.append(padded)
    return out


def style_text(style: str) -> str:
    """The raw style guide (styles/*.j2) — fed to the ANNOTATOR as the
    emotional baseline; the engine itself never sees it."""
    path = STYLES_DIR / f"{style}.j2"
    if not path.exists():
        path = STYLES_DIR / "neutral.j2"
    return " ".join(line.strip() for line in path.read_text().splitlines() if line.strip())


def style_description(style: str) -> str:
    for entry in MANIFEST.get("styles", []):
        if entry.get("name") == style:
            return str(entry.get("description", ""))
    return ""


def direction_from_cues(cues: dict[str, Any] | None, max_words: int) -> str | None:
    """The per-segment emotion direction, sanitized to the contract's
    word budget — flows verbatim into IndexTTS emo_text. None when the
    segment carries no direction (the engine then uses the reference
    clip's own delivery)."""
    if not cues:
        return None
    raw = str(cues.get("direction") or "").strip()
    if not raw:
        return None
    return sanitize_direction(raw, max_words) or None


# ---------------------------------------------------------------------------
# IndexTTS-2.5 synthesis
# ---------------------------------------------------------------------------


def resolve_model_dir() -> str:
    """Pinned snapshot; COSYVOICE-style env override name kept generic:
    TTS_MODEL_DIR, else snapshot_download at the pinned revision."""
    model_dir = os.environ.get("TTS_MODEL_DIR", "").strip()
    if model_dir:
        return model_dir
    from huggingface_hub import snapshot_download

    log.info("Resolving IndexTTS-2.5 weights %s@%s...", INDEXTTS_REPO, INDEXTTS_REVISION[:12])
    start = time.time()
    model_dir = snapshot_download(INDEXTTS_REPO, revision=INDEXTTS_REVISION)
    log.info("Weights ready in %.1fs", time.time() - start)
    return model_dir


def load_tts(model_dir: str, bf16: bool, use_qwen_emo: bool):
    from indextts.infer_v2_5 import IndexTTS2

    log.info("Loading IndexTTS-2.5 from %s (bf16=%s, qwen_emo=%s)...", model_dir, bf16, use_qwen_emo)
    start = time.time()
    model = IndexTTS2(
        cfg_path=str(pathlib.Path(model_dir) / "config.yaml"),
        model_dir=model_dir,
        use_bf16=bf16,
        use_qwen_emo=use_qwen_emo,
    )
    log.info("IndexTTS-2.5 loaded in %.1fs", time.time() - start)
    return model


def _infer_guarded(model, kwargs: dict[str, Any]) -> None:
    """Run model.infer with the engine's RuntimeWarnings escalated to hard
    failures (#120): the force-split and max_mel-truncation warnings mark
    takes that exceed the stability budget — a fluent-but-wrong or
    truncated take must fail loudly into segment_failures, never ship
    silently (upstream issue #775: no other signal fires)."""
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        try:
            model.infer(**kwargs)
        except Warning as exc:
            raise RuntimeError(f"engine stability warning: {exc}") from exc


def generate_chunk(model, text: str, output_wav: pathlib.Path, direction: str | None, seed: int,
                   emo_alpha: float, duration_factor: float, use_qwen_emo: bool,
                   spk_prompt: str | None = None,
                   emotion_mode: str = "emo_text",
                   emotion_vector: list[float] | None = None) -> None:
    """Synthesize one chunk. The seed is re-applied before every call so
    the take is reproducible. use_random stays off (it degrades cloning
    fidelity). A missing/failed emotion-text channel degrades to the
    reference clip's own delivery — counted, never fatal. spk_prompt is
    the tone-carry reference (#119): the last good take's path when
    chaining, else None = the canonical clip. The English token budget and
    interval silence are declared values passed explicitly (#120) — the
    engine's defaults are Chinese-calibrated."""
    import torch

    emo_note = f" emo_text={direction!r}" if direction else ""
    if spk_prompt and spk_prompt != str(VOICE_PATH):
        emo_note = f" chained<-{pathlib.Path(spk_prompt).name}" + emo_note
    log.info("TTS: %d chars -> %s%s", len(text), output_wav.name, emo_note)
    torch.manual_seed(seed)
    kwargs: dict[str, Any] = dict(
        spk_audio_prompt=str(spk_prompt or VOICE_PATH),
        text=text,
        lang="EN",
        output_path=str(output_wav),
        duration_factor=duration_factor,
        use_random=False,
        verbose=False,
        max_text_tokens_per_segment=declared_segment_tokens(),
        interval_silence=declared_interval_silence_ms(),
    )
    if emotion_mode == "emo_vector" and emotion_vector is not None:
        kwargs.update(emo_vector=list(emotion_vector), emo_alpha=emo_alpha)
    elif direction and use_qwen_emo:
        kwargs.update(use_emo_text=True, emo_text=direction, emo_alpha=emo_alpha)
    try:
        _infer_guarded(model, kwargs)
    except Exception:
        if not (emotion_mode == "emo_vector" or (direction and use_qwen_emo)):
            raise
        # Emotion channel failed: degrade to the reference delivery.
        log.warning("emo_text channel failed for %s; neutral take with the reference delivery", output_wav.name)
        kwargs.pop("use_emo_text", None)
        kwargs.pop("emo_text", None)
        kwargs.pop("emo_vector", None)
        kwargs.pop("emo_alpha", None)
        _infer_guarded(model, kwargs)


def _new_chain_counters() -> dict[str, int]:
    return {"segments": 0, "with_direction": 0, "neutral": 0, "emo_fallback": 0,
            "chained": 0, "re_anchor": 0}


def _run_chain(model, chain: list[tuple], use_qwen_emo: bool, max_words: int,
               emo_alpha: float, duration_factor: float,
               chaining: bool,
               emotion_mode: str = "emo_text",
               emotion_vector: list[float] | None = None) -> tuple[dict[int, str | None], dict[str, int]]:
    """One chapter's chunks serially (#115 pool shape; chapters are the
    unit of parallelism). Tone carry (#119): the chain starts at the
    canonical reference (chapter = emotional reset point), every later
    chunk primes from the last good take — the engine does not model
    prosody across boundaries, so the reference channel is the only tone
    carrier. A failed chunk does not break the chain: the next chunk
    primes from the most recent successful take."""
    results: dict[int, str | None] = {}
    counts = _new_chain_counters()
    last_good: str | None = None
    for idx, text, direction, out_path, seed in chain:
        counts["segments"] += 1
        if direction:
            counts["with_direction"] += 1
        else:
            counts["neutral"] += 1
        ref = next_reference(last_good, chaining)
        if ref == str(VOICE_PATH):
            counts["re_anchor"] += 1
        else:
            counts["chained"] += 1
        try:
            generate_chunk(model, text, pathlib.Path(out_path), direction, seed,
                           emo_alpha, duration_factor, use_qwen_emo, spk_prompt=ref,
                           emotion_mode=emotion_mode, emotion_vector=emotion_vector)
            results[idx] = None
            last_good = out_path
        except Exception as exc:  # noqa: BLE001 - per-chunk fallback
            results[idx] = str(exc)
            counts["emo_fallback"] += 1
    return results, counts


_POOL_MODEL = None


def _pool_init(model_dir: str, bf16: bool, use_qwen_emo: bool) -> None:
    global _POOL_MODEL
    _POOL_MODEL = load_tts(model_dir, bf16, use_qwen_emo)


def _pool_chain(args: tuple) -> tuple[dict[int, str | None], dict[str, int]]:
    chain, use_qwen_emo, max_words, emo_alpha, duration_factor, chaining, emotion_mode, emotion_vector = args
    try:
        return _run_chain(_POOL_MODEL, chain, use_qwen_emo, max_words, emo_alpha, duration_factor, chaining,
                          emotion_mode=emotion_mode, emotion_vector=emotion_vector)
    except Exception as exc:  # noqa: BLE001 - a dead engine fails its whole chain
        return {task[0]: str(exc) for task in chain}, _new_chain_counters()


def synthesize_chains(chains: list[list[tuple]], model_dir: str, n_parallel: int,
                       use_qwen_emo: bool, max_words: int,
                       emo_alpha: float, duration_factor: float,
                       chaining: bool,
                       emotion_mode: str = "emo_text",
                       emotion_vector: list[float] | None = None) -> tuple[dict[int, str | None], dict[str, int]]:
    """Chapters in parallel (one chain per worker process), chunks serial
    within each — the documented parallelism model."""
    if n_parallel <= 1 or len(chains) <= 1:
        model = load_tts(model_dir, bf16_enabled(), use_qwen_emo)
        results: dict[int, str | None] = {}
        counts = _new_chain_counters()
        for chain in chains:
            r, c = _run_chain(model, chain, use_qwen_emo, max_words, emo_alpha, duration_factor, chaining,
                              emotion_mode=emotion_mode, emotion_vector=emotion_vector)
            results.update(r)
            for key, value in c.items():
                counts[key] = counts.get(key, 0) + value
        return results, counts

    import multiprocessing as mp

    total = sum(len(chain) for chain in chains)
    log.info("Synthesizing %d chunks across %d chapter chains on %d workers (chaining=%s)...",
             total, len(chains), n_parallel, chaining)
    ctx = mp.get_context("spawn")
    with ctx.Pool(n_parallel, initializer=_pool_init, initargs=(model_dir, bf16_enabled(), use_qwen_emo)) as pool:
        outputs = pool.map(_pool_chain, [
            (chain, use_qwen_emo, max_words, emo_alpha, duration_factor, chaining, emotion_mode, emotion_vector) for chain in chains
        ])
    results = {}
    counts = _new_chain_counters()
    for r, c in outputs:
        results.update(r)
        for key, value in c.items():
            counts[key] = counts.get(key, 0) + value
    return results, counts


# ---------------------------------------------------------------------------
# Leak gate (diagnostic transcript; this engine has no instruct text)
# ---------------------------------------------------------------------------


def transcribe_excerpt(retail_mp3: pathlib.Path) -> dict[str, Any]:
    """Whisper-transcribe the retail sample into the QC report — the
    diagnostic that caught the CosyVoice 'quote' insertion. No phrase
    gate for this engine (it has no instruct channel to leak)."""
    try:
        import warnings

        import whisper

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = whisper.load_model("tiny")
            result = model.transcribe(str(retail_mp3), temperature=0)
        text = " ".join((result.get("text") or "").split())
        return {"transcript_excerpt": text[:300]}
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def load_script(input_dir: pathlib.Path) -> dict[str, Any]:
    script_path = input_dir / "script.json"
    if not script_path.exists():
        raise FileNotFoundError(f"Missing {script_path}")
    with open(script_path) as f:
        script = json.load(f)
    if not script.get("chapters"):
        raise ValueError("script.json has no chapters")
    return script


def main() -> int:
    start_time = time.time()
    log.info("Recipe starting: input=%s output=%s", INPUT_DIR, OUTPUT_DIR)

    if not INPUT_DIR.exists():
        log.error("Input directory %s does not exist", INPUT_DIR)
        return 1
    if not VOICE_PATH.exists():
        log.error("Voice file %s does not exist", VOICE_PATH)
        return 1

    # GPU or fail (#103): synthesis never runs CPU-only.
    try:
        import torch

        if not torch.cuda.is_available():
            raise RuntimeError("no CUDA device")
    except Exception:
        log.error("No CUDA device visible to the recipe — TTS is GPU-or-fail (#103). "
                  "Check the worker's GPU device request (GPU_DEVICE).")
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "m4b").mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "mp3").mkdir(parents=True, exist_ok=True)
    checkpoint_dir = OUTPUT_DIR / "checkpoint"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    script = load_script(INPUT_DIR)
    metadata = json.loads((INPUT_DIR / "metadata.json").read_text()) if (INPUT_DIR / "metadata.json").exists() else {}
    title = metadata.get("title") or script.get("title", "Untitled")
    author = metadata.get("author") or script.get("author", "Unknown")
    style = os.environ.get("PERFORMANCE_STYLE", "neutral")
    recipe_version = os.environ.get("RECIPE_VERSION", "unknown")
    cover_path = INPUT_DIR / "cover.jpg"
    if not cover_path.exists():
        cover_env = os.environ.get("COVER_ART", "")
        if cover_env and pathlib.Path(cover_env).exists():
            cover_path = pathlib.Path(cover_env)
    seed = int(os.environ.get("RECIPE_SEED", str(DEFAULT_SEED)))

    use_qwen_emo = qwen_emo_enabled()
    bf16 = bf16_enabled()
    n_parallel = parallel_workers()
    emo_alpha = declared_emo_alpha()
    duration_factor = declared_speed()
    chaining = chaining_enabled()

    log.info("Book: %s by %s (%d chapters), style=%s, seed=%d, bf16=%s, qwen_emo=%s, workers=%d, emo_alpha=%.2f, duration_factor=%.2f, chaining=%s",
             title, author, len(script["chapters"]), style, seed, bf16, use_qwen_emo,
             n_parallel, emo_alpha, duration_factor, chaining)

    contract = AnnotationContract.from_capabilities(CAPABILITIES_PATH)
    style_rich = style_text(style)
    style_desc = style_description(style)
    if not contract.configured:
        log.warning("No annotation contract declared — clean text, no directions")

    llm_settings = load_llm_settings()
    llm_endpoint = probe_llm(llm_settings) if llm_settings else None
    if llm_settings and llm_endpoint:
        if llm_endpoint["reachable"]:
            log.info("LLM endpoint reachable from inside the container: %s", llm_settings["base_url"])
        else:
            log.error("LLM endpoint UNREACHABLE from inside the recipe container (%s) — "
                      "annotation pass skipped; synthesizing with the reference's own delivery (#118)",
                      llm_endpoint["error"])
    annotation_cues: dict[str, dict[str, Any]] = {}
    annotation_stats: dict[str, int] = {}
    if llm_settings and contract.configured and (llm_endpoint or {}).get("reachable"):
        slots = max(1, int(llm_settings.get("slots") or 2))
        log.info("LLM annotation enabled (engine=%s, declared emotions=%s, seats=%d, model=%s)",
                 contract.engine, sorted(contract.instruct_controls), slots, llm_settings["model"])
        chapters_to_annotate = [ch for ch in script["chapters"] if ch.get("segments")]
        if chapters_to_annotate:
            if slots == 1 or len(chapters_to_annotate) == 1:
                for ch in chapters_to_annotate:
                    cues_map, stats = annotate_chapter(
                        ch, contract, llm_settings, style, style_desc,
                        ANNOTATION_PROMPT_PATH, MARKUP_EXAMPLES_PATH)
                    annotation_cues.update(cues_map)
                    merge_annotation_stats(annotation_stats, stats)
            else:
                from concurrent.futures import ThreadPoolExecutor

                with ThreadPoolExecutor(max_workers=min(slots, len(chapters_to_annotate))) as pool:
                    results = pool.map(
                        lambda ch: annotate_chapter(
                            ch, contract, llm_settings, style, style_desc,
                            ANNOTATION_PROMPT_PATH, MARKUP_EXAMPLES_PATH),
                        chapters_to_annotate,
                    )
                    for cues_map, stats in results:
                        annotation_cues.update(cues_map)
                        merge_annotation_stats(annotation_stats, stats)
            log.info("Annotation pass complete: %s", annotation_stats)
            write_annotated_script(OUTPUT_DIR / "annotated-script.json", contract.engine, annotation_cues)
    elif llm_settings:
        log.info("LLM set but the engine declares no annotation contract — clean text")

    temp_dir = tempfile.mkdtemp(prefix="recipe-")

    chapter_info: list[dict[str, Any]] = []
    existing_checkpoints: dict[int, pathlib.Path] = {}
    for f in checkpoint_dir.glob("chapter-*.wav"):
        try:
            existing_checkpoints[int(f.stem.split("-")[1])] = f
        except (ValueError, IndexError):
            continue

    chains: list[list[tuple]] = []
    chapter_wav_lists: dict[int, list[pathlib.Path]] = {}
    next_idx = 0

    for chapter in script["chapters"]:
        ch_num = chapter.get("chapter", len(chapter_info) + 1)
        ch_title = chapter.get("title", f"Chapter {ch_num}")
        segments = chapter.get("segments", [])
        if not segments:
            continue
        if ch_num in existing_checkpoints:
            cp_wav = existing_checkpoints[ch_num]
            chapter_info.append({"chapter": ch_num, "title": ch_title, "wav": cp_wav, "mastered": cp_wav})
            log.info("Chapter %d loaded from checkpoint", ch_num)
            continue

        ch_temp_dir = pathlib.Path(temp_dir) / f"ch{ch_num:03d}"
        ch_temp_dir.mkdir(parents=True, exist_ok=True)
        wav_list: list[pathlib.Path] = []
        chapter_chain: list[tuple] = []

        for i, seg in enumerate(segments):
            seg_id = seg.get("id", f"seg-{i:03d}")
            text = seg.get("text", "").strip()
            if not text:
                continue
            direction = direction_from_cues((annotation_cues.get(seg_id) or {}).get("cues"),
                                            contract.max_direction_words) if contract.configured else None
            for j, chunk in enumerate(chunk_text(text)):
                wav_name = f"{seg_id}-{j:02d}.wav" if len(chunk_text(text)) > 1 else f"{seg_id}.wav"
                wav_path = ch_temp_dir / wav_name
                chapter_chain.append((next_idx, chunk, direction, str(wav_path), seed))
                wav_list.append(wav_path)
                next_idx += 1

        chapter_wav_lists[ch_num] = wav_list
        chapter_info.append({"chapter": ch_num, "title": ch_title, "temp_dir": ch_temp_dir})
        if chapter_chain:
            chains.append(chapter_chain)

    # Credits synthesize through the same chain path — standalone
    # single-chunk chains, no direction (the reference's own delivery).
    credits_dir = pathlib.Path(temp_dir) / "credits"
    credits_dir.mkdir(parents=True, exist_ok=True)
    opening_text = f"{title}. By {author}. An audiobook."
    closing_text = f"Thank you for listening to {title}."
    opening_wav = credits_dir / "opening.wav"
    closing_wav = credits_dir / "closing.wav"
    opening_idx = next_idx
    chains.append([(opening_idx, opening_text, None, str(opening_wav), seed)])
    next_idx += 1
    closing_idx = next_idx
    chains.append([(closing_idx, closing_text, None, str(closing_wav), seed)])
    next_idx += 1

    tasks = [task for chain in chains for task in chain]
    total_segments = next_idx - 2

    if total_segments <= 0 and not existing_checkpoints:
        log.error("No chapters were generated")
        return 1

    model_dir = resolve_model_dir()
    synth_start = time.time()
    emotion_mode = declared_emotion_mode()
    emotion_vector = declared_emotion_vector() if emotion_mode == "emo_vector" else None
    log.info("Emotion mode: %s%s", emotion_mode,
             (" vector=" + str(emotion_vector)) if emotion_vector is not None else "")
    errors, chain_counts = synthesize_chains(
        chains, model_dir, n_parallel, use_qwen_emo,
        contract.max_direction_words, emo_alpha, duration_factor, chaining,
        emotion_mode=emotion_mode, emotion_vector=emotion_vector)
    synthesis_seconds = time.time() - synth_start

    failed_segments = 0
    for idx in range(total_segments):
        if errors.get(idx) is not None:
            failed_segments += 1
            log.error("TTS failed for %s: %s", pathlib.Path(tasks[idx][3]).name, errors[idx])
            make_silence_wav(pathlib.Path(tasks[idx][3]), 1.0, TTS_SAMPLE_RATE)
    for name, idx, wav in (("Opening", opening_idx, opening_wav), ("Closing", closing_idx, closing_wav)):
        if errors.get(idx) is not None:
            log.warning("%s credits TTS failed (%s), using silence", name, errors[idx])
            make_silence_wav(wav, 2.0, TTS_SAMPLE_RATE)

    for ch in chapter_info:
        if "mastered" in ch:
            continue
        wav_list = chapter_wav_lists.get(ch["chapter"], [])
        if not wav_list:
            continue
        ch_wav = ch["temp_dir"] / f"chapter-{ch['chapter']:03d}.wav"
        joined_list = prepend_seam_pauses(wav_list, ch["temp_dir"], ch["chapter"])
        crossfade_join(joined_list, ch_wav, declared_overlap_ms())
        try:
            in_durs = [round(get_audio_duration(p), 2) for p in joined_list]
            log.info("Join diagnostics ch%s: %d inputs, durations=%s, output=%.2fs",
                     ch["chapter"], len(joined_list), in_durs, get_audio_duration(ch_wav))
        except Exception as exc:  # noqa: BLE001 - diagnostics never fail the job
            log.warning("Join diagnostics failed: %s", exc)
        ch["wav"] = ch_wav
        log.info("Chapter %d complete (seam_pause=%dms)", ch["chapter"], declared_seam_pause_ms())

    if not any("wav" in ch for ch in chapter_info):
        log.error("No chapters were generated")
        return 1

    # Stage 3: Mastering
    log.info("Mastering audio...")
    mastered_dir = pathlib.Path(temp_dir) / "mastered"
    mastered_dir.mkdir(parents=True, exist_ok=True)
    rt_af = room_tone_af(declared_room_tone_head_ms(), declared_room_tone_tail_ms())

    mastered_chapters: list[pathlib.Path] = []
    for ch in chapter_info:
        if "mastered" in ch:
            mastered_chapters.append(ch["mastered"])
            continue
        mastered = mastered_dir / f"chapter-{ch['chapter']:03d}.wav"
        master_wav(ch["wav"], mastered, declared_target_lufs(), PACK_SAMPLE_RATE, rt_af)
        cp_path = checkpoint_dir / f"chapter-{ch['chapter']:03d}.wav"
        tmp_path = cp_path.with_suffix(".tmp")
        shutil.copy2(mastered, tmp_path)
        tmp_path.rename(cp_path)
        mastered_chapters.append(mastered)
        ch["mastered"] = mastered
        log.info("Chapter %d mastered and checkpointed", ch["chapter"])

    mastered_opening = mastered_dir / "opening.wav"
    mastered_closing = mastered_dir / "closing.wav"
    master_wav(opening_wav, mastered_opening, declared_target_lufs(), PACK_SAMPLE_RATE, rt_af)
    master_wav(closing_wav, mastered_closing, declared_target_lufs(), PACK_SAMPLE_RATE, rt_af)

    # Stage 4: Packaging
    log.info("Packaging...")
    layout = output_layout(OUTPUT_DIR)
    narrator = str(((MANIFEST.get("packaging") or {}).get("metadata") or {}).get("narrator") or "IndexTTS-2.5")

    for ch in chapter_info:
        wav_to_mp3(ch["mastered"], layout["mp3_dir"] / chapter_filename(ch["chapter"]), MP3_BITRATE, PACK_SAMPLE_RATE)
    wav_to_mp3(mastered_opening, layout["opening_credits"], MP3_BITRATE, PACK_SAMPLE_RATE)
    wav_to_mp3(mastered_closing, layout["closing_credits"], MP3_BITRATE, PACK_SAMPLE_RATE)

    full_wav = mastered_dir / "full.wav"
    concat_wavs([mastered_opening] + mastered_chapters + [mastered_closing], full_wav)
    full_duration = get_audio_duration(full_wav)

    m4b_chapters = []
    current_ms = 0
    opening_dur_ms = int(get_audio_duration(mastered_opening) * 1000)
    m4b_chapters.append({"start_ms": 0, "end_ms": opening_dur_ms, "title": "Opening Credits"})
    current_ms = opening_dur_ms
    for ch in chapter_info:
        ch_dur_ms = int(get_audio_duration(ch["mastered"]) * 1000)
        m4b_chapters.append({"start_ms": current_ms, "end_ms": current_ms + ch_dur_ms, "title": ch["title"]})
        current_ms += ch_dur_ms
    closing_dur_ms = int(get_audio_duration(mastered_closing) * 1000)
    m4b_chapters.append({"start_ms": current_ms, "end_ms": current_ms + closing_dur_ms, "title": "Closing Credits"})

    build_m4b_from_wav(full_wav, layout["m4b"], title, author, narrator,
                       AAC_BITRATE, PACK_SAMPLE_RATE, cover_path, m4b_chapters)

    trim_to_duration(full_wav, layout["retail_sample"].with_suffix(".wav"), RETAIL_SAMPLE_SECONDS)
    wav_to_mp3(layout["retail_sample"].with_suffix(".wav"), layout["retail_sample"], MP3_BITRATE, PACK_SAMPLE_RATE)
    layout["retail_sample"].with_suffix(".wav").unlink(missing_ok=True)

    transcript = transcribe_excerpt(layout["retail_sample"])

    layout["metadata"].write_text(json.dumps({
        "title": title,
        "author": author,
        "narrator": narrator,
        "recipe": "ggardner-indextts",
        "recipe_version": recipe_version,
        "performance_style": style,
        "language": script.get("language", "en"),
        "total_chapters": len(chapter_info),
        "total_duration_seconds": round(full_duration, 1),
        "sample_rate": PACK_SAMPLE_RATE,
        "mp3_bitrate": MP3_BITRATE,
        "m4b_codec": "aac",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }, indent=2))

    acx = acx_verdict(full_wav)
    log.info("ACX measured: LUFS=%s peak=%s dB floor=%s dB -> compliant=%s",
             acx["measured"]["integrated_lufs"], acx["measured"]["true_peak_db"],
             acx["measured"]["noise_floor_db"], acx["acx_compliant"])

    segment_failures = {
        pathlib.Path(tasks[idx][3]).name: str(errors[idx])
        for idx in range(total_segments)
        if errors.get(idx) is not None
    }

    layout["qc_report"].write_text(json.dumps({
        "acx_compliant": acx["acx_compliant"],
        "reference_hash": f"sha256:{__import__('hashlib').sha256(VOICE_PATH.read_bytes()).hexdigest()}",
        "acx_measurements": acx["measured"],
        "acx_checks": acx["checks"],
        "segment_failures": segment_failures,
        "transcript_excerpt": transcript.get("transcript_excerpt"),
        "total_chapters": len(chapter_info),
        "total_segments": total_segments,
        "failed_segments": failed_segments,
        "total_duration_seconds": round(full_duration, 1),
        "sample_rate": PACK_SAMPLE_RATE,
        "channels": 1,
        "files": {
            "m4b": str(layout["m4b"].relative_to(OUTPUT_DIR)),
            "mp3_chapters": [chapter_filename(ch["chapter"]) for ch in chapter_info],
            "opening_credits": "mp3/opening-credits.mp3",
            "closing_credits": "mp3/closing-credits.mp3",
            "retail_sample": "mp3/retail-sample.mp3",
        },
        "engine": {
            "name": "indextts2.5",
            "model_repo": INDEXTTS_REPO,
            "model_revision": INDEXTTS_REVISION,
            "code_revision": "d9e41aac89fd00b3d71497fddb287b7f24613712",
            "mode": "zero_shot_clone+emo_vector" if emotion_mode == "emo_vector" else "zero_shot_clone+emo_text",
            "voice": _declared_voice,
            "seed": seed,
            "bf16": bf16,
            "qwen_emo": use_qwen_emo,
            "duration_factor": duration_factor,
            "emo_alpha": emo_alpha,
            "parallel_workers": n_parallel,
            "chaining": chaining,
            "emotion_mode": emotion_mode,
            "emotion_vector": emotion_vector,
            "segment_tokens": declared_segment_tokens(),
            "interval_silence_ms": declared_interval_silence_ms(),
            "seam_pause_ms": declared_seam_pause_ms(),
            "direction_granularity": contract.direction_granularity,
            "synthesis_seconds": round(synthesis_seconds, 1),
            "synthesis_rtf": round(synthesis_seconds / full_duration, 3) if full_duration else None,
        },
        "emotion": {
            "contract": contract.engine if contract.configured else None,
            "chain_counters": chain_counts,
        },
        "llm_annotation": llm_settings is not None,
        "annotation": {
            "engine_contract": contract.engine if contract.configured else None,
            "endpoint": llm_endpoint,
            "stats": annotation_stats,
        },
        "device": "cuda",
        "elapsed_seconds": round(time.time() - start_time, 1),
    }, indent=2))

    shutil.rmtree(temp_dir, ignore_errors=True)

    log.info("Recipe complete in %.1fs: %s", time.time() - start_time, layout["m4b"])
    return 0


def declared_overlap_ms() -> int:
    try:
        return max(0, int((MANIFEST.get("tts") or {}).get("overlap_ms", 200)))
    except (TypeError, ValueError):
        return 200


def declared_target_lufs() -> float:
    try:
        return float((MANIFEST.get("mastering") or {}).get("target_rms_db", -18.0))
    except (TypeError, ValueError):
        return -18.0


def declared_room_tone_head_ms() -> int:
    rt = ((MANIFEST.get("mastering") or {}).get("room_tone_ms")) or {}
    try:
        return max(0, int(rt.get("head", 0)))
    except (TypeError, ValueError):
        return 0


def declared_room_tone_tail_ms() -> int:
    rt = ((MANIFEST.get("mastering") or {}).get("room_tone_ms")) or {}
    try:
        return max(0, int(rt.get("tail", 0)))
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    sys.exit(main())
