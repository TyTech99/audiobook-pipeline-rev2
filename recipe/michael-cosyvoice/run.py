"""Michael CosyVoice 3 voice recipe entrypoint.

Runs inside the recipe Docker container:
  /input  <- book package (script.json, cover.jpg, metadata.json, ...)
  /output <- audiobook package (m4b/, mp3/, metadata.json, qc-report.json)

Stages:
  1. LLM annotation (optional, when LLM_BASE_URL + LLM_MODEL are set):
     contract-driven from the engine's declared capabilities (#112) —
     the prompt is assembled from capabilities + markup examples + style
     + previous chunk state + chunk text; annotations are METADATA ONLY
     (cues keyed by segment ID, the clean script is the single text
     authority); chunks that fail word validation are rejected and
     retried; segments run serially with state passing inside a chapter,
     chapters are emotional reset points and annotate in parallel
     bounded by the declared LLM seats.
  2. CosyVoice 3 TTS generation (inference_instruct2, voice clone from
     voices/Michael.wav, fixed seed re-applied per chunk; fp16 autocast,
     TensorRT flow plan, and parallel chunk workers per #93). The TTS
     input is composed from the CLEAN text plus cues (declared engine
     tags inline; per-segment instruct from the style base + direction).
  3. Mastering (ffmpeg loudnorm + resample)
  4. Packaging (M4B with stik=2, per-chapter MP3, credits, retail sample)
"""

from __future__ import annotations

import json
import logging
import math
import os
import pathlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from typing import Any

import yaml

from common.annotation import (
    AnnotationContract,
    AnnotationRejected,
    build_prompt,
    compose_instruct,
    compose_tts_text,
    extract_cues_from_response,
    parse_annotation,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("recipe")

RECIPE_DIR = pathlib.Path(__file__).resolve().parent
VOICE_PATH = RECIPE_DIR / "voices" / "Michael.wav"
STYLES_DIR = RECIPE_DIR / "styles"
INPUT_DIR = pathlib.Path(os.environ.get("INPUT_DIR", "/input"))
OUTPUT_DIR = pathlib.Path(os.environ.get("OUTPUT_DIR", "/output"))


def recipe_manifest() -> dict[str, Any]:
    """The recipe's own declaration (recipe.yaml) — paths are read from
    here, never hardcoded, so the engine axis stays declarative (#112)."""
    try:
        return yaml.safe_load((RECIPE_DIR / "recipe.yaml").read_text()) or {}
    except Exception:
        return {}


MANIFEST = recipe_manifest()
ENGINE_MANIFEST = MANIFEST.get("engine") or {}
CAPABILITIES_PATH = RECIPE_DIR / ENGINE_MANIFEST.get("capabilities", "engine/capabilities.yaml")
ANNOTATION_PROMPT_PATH = RECIPE_DIR / ENGINE_MANIFEST.get("annotation_prompt", "engine/annotation-prompt.j2")
MARKUP_EXAMPLES_PATH = RECIPE_DIR / ENGINE_MANIFEST.get("markup_examples", "engine/markup-examples.md")

# Mastering target: the documented ACX loudness range is -23..-18 LUFS
# (voice-recipe.md); -18 is the ceiling-normal choice.
MASTER_TARGET_LUFS = -18.0

# CosyVoice 3 outputs 24 kHz mono; we master to 44.1 kHz for packaging.
TTS_SAMPLE_RATE = 24000
PACK_SAMPLE_RATE = 44100
MP3_BITRATE = "192k"
AAC_BITRATE = "96k"
MAX_CHARS_PER_TTS = 300  # keep individual TTS calls short for quality
RETAIL_SAMPLE_SECONDS = 75

# Pinned CosyVoice 3 weights (Apache-2.0). The revision is part of the
# recipe's reproducibility contract: same input + same recipe = same model.
COSYVOICE_REPO = "FunAudioLLM/Fun-CosyVoice3-0.5B-2512"
COSYVOICE_REVISION = "29e01c4e8d000f4bcd70751be16fa94bf3d85a18"

# inference_instruct2 format (model card usage): the Qwen system prompt,
# a natural-language delivery direction, and the end-of-prompt token.
SYSTEM_PROMPT = "You are a helpful assistant."
ENDOFPROMPT = "<|endofprompt|>"

# One seed for the whole take, re-applied before every inference call.
# Advancing the RNG per chunk rotates the sampled speaker mid-take
# (VoiceStudio #3730), so the recipe never advances it.
DEFAULT_SEED = 1234


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable without torch/cosyvoice)
# ---------------------------------------------------------------------------


def chunk_text(text: str, max_chars: int = MAX_CHARS_PER_TTS) -> list[str]:
    """Split text into chunks at sentence boundaries, respecting max_chars.

    If a single sentence exceeds max_chars, split at word boundaries.
    Returns a list of non-empty strings that concatenate (with spaces)
    to approximately the original text.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    # Split into sentences
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if not current:
            current = sentence
        elif len(current) + 1 + len(sentence) <= max_chars:
            current += " " + sentence
        else:
            chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)

    # Handle any sentence that was itself too long
    final: list[str] = []
    for chunk in chunks:
        if len(chunk) <= max_chars:
            final.append(chunk)
            continue
        words = chunk.split()
        buf = ""
        for word in words:
            if not buf:
                buf = word
            elif len(buf) + 1 + len(word) <= max_chars:
                buf += " " + word
            else:
                final.append(buf)
                buf = word
        if buf:
            final.append(buf)
    return final


def normalize_for_comparison(text: str) -> str:
    """Normalize text for source-fidelity comparison.

    Word cores only: lowercase, strip punctuation, keep intra-word
    apostrophes. Hint-added commas and re-quoted dialogue must not break
    the word check — the live #110 run failed every annotated segment
    because pause-commas glued to source words ("calamity," vs "calamity")
    made exact token matching fail, even though every source word was
    preserved in order.
    """
    words = re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)*", text.lower())
    return " ".join(words)


def validate_annotation(source_text: str, annotated_text: str) -> bool:
    """Check that annotated text still contains all source words in order."""
    src_words = normalize_for_comparison(source_text).split()
    ann_words = normalize_for_comparison(annotated_text).split()
    if not src_words:
        return True
    it = iter(ann_words)
    return all(word in it for word in src_words)


def first_annotation_mismatch(source_text: str, annotated_text: str) -> str | None:
    """First source word the annotation does not preserve (for logging)."""
    src_words = normalize_for_comparison(source_text).split()
    ann_words = normalize_for_comparison(annotated_text).split()
    it = iter(ann_words)
    for word in src_words:
        if word not in it:
            return word
    return None


def style_base(style: str) -> str:
    """The raw performance-style delivery direction (styles/*.j2).

    The instruct2 wrapping is done by the system annotation layer
    (compose_instruct) per the engine's declared instruct format — the
    recipe no longer wraps it itself (#112).
    """
    path = STYLES_DIR / f"{style}.j2"
    if not path.exists():
        path = STYLES_DIR / "neutral.j2"
    return " ".join(line.strip() for line in path.read_text().splitlines() if line.strip())


def style_description(style: str) -> str:
    """The style's declared description from recipe.yaml."""
    for entry in MANIFEST.get("styles", []):
        if entry.get("name") == style:
            return str(entry.get("description", ""))
    return ""


def engine_flags() -> dict[str, bool]:
    """Engine performance flags (#93). Defaults: fp16 autocast on,
    TensorRT flow plan on.

    fp16 — upstream constructor flag (cosyvoice.py:189-224): autocasts the
    LLM and the flow (model.py:103,136). The 0.5B Qwen token loop is
    memory-bandwidth-bound (stock RTF ~0.7 on any GPU — 4080 ≈ 5090 with
    GPU util <50%, upstream issue #1262), so halving memory traffic is the
    single biggest lever. Quality gate: the sample job ear test.
    load_trt — builds a GPU-specific TensorRT plan for the flow estimator
    from the fp32 ONNX that ships in the weights snapshot; the plan lands
    in the persistent model cache. With fp16 on, upstream builds the
    fp16-mode plan and explicitly cautions it ("use at caution") — the ear
    test gates that too. RECIPE_FP16=0 / RECIPE_TRT=0 are kill switches.
    """
    return {
        "fp16": os.environ.get("RECIPE_FP16", "1") != "0",
        "load_trt": os.environ.get("RECIPE_TRT", "1") != "0",
    }


def parallel_workers() -> int:
    """Chunk-synthesis worker processes (#93).

    Default 2: the twice-proven configuration on the 16 GB GPU 1 (0
    failed segments across both measured runs; the one 3-worker run that
    lost 2 segments to silence is undiagnosed — #111's per-segment
    failure capture now makes any recurrence self-documenting). Ty's
    call stands on the measured data; RECIPE_PARALLEL overrides.
    """
    try:
        return max(1, int(os.environ.get("RECIPE_PARALLEL", "2")))
    except ValueError:
        return 2


def chapter_filename(chapter_number: int) -> str:
    """Return the MP3 filename for a chapter number (1-indexed)."""
    return f"chapter-{chapter_number:03d}.mp3"


def output_layout() -> dict[str, pathlib.Path]:
    """Return the expected output file layout relative to OUTPUT_DIR."""
    return {
        "m4b": OUTPUT_DIR / "m4b" / "audiobook.m4b",
        "mp3_dir": OUTPUT_DIR / "mp3",
        "opening_credits": OUTPUT_DIR / "mp3" / "opening-credits.mp3",
        "closing_credits": OUTPUT_DIR / "mp3" / "closing-credits.mp3",
        "retail_sample": OUTPUT_DIR / "mp3" / "retail-sample.mp3",
        "metadata": OUTPUT_DIR / "metadata.json",
        "qc_report": OUTPUT_DIR / "qc-report.json",
    }


def build_m4b_from_wav(
    wav_path: pathlib.Path,
    m4b_path: pathlib.Path,
    title: str,
    author: str,
    cover_path: pathlib.Path | None = None,
    chapters: list[dict[str, Any]] | None = None,
) -> None:
    """Encode a WAV to M4B with ftyp=M4B, stik=2, and metadata via ffmpeg.

    Chapters are embedded as QuickTime chapter markers when provided.
    If cover_path is given, it is embedded as cover art.
    """
    m4b_path.parent.mkdir(parents=True, exist_ok=True)
    # All inputs first, then output options: ffmpeg binds options to the
    # file that follows them, so output options placed before later -i
    # flags get parsed as input options and the call dies with exit 234
    # (#109). The mp4 muxer is forced because the .m4b-extension default
    # (ipod muxer) rejects mjpeg cover streams in current ffmpeg builds.
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(wav_path)]
    has_cover = bool(cover_path and cover_path.exists())
    if has_cover:
        cmd += ["-i", str(cover_path)]
    if chapters:
        chapter_file = m4b_path.with_suffix(".chapters")
        lines = ["; FFmpeg meta file", "; FORMAT chaps"]
        for ch in chapters:
            start_ms = int(ch.get("start_ms", 0))
            end_ms = int(ch.get("end_ms", start_ms + 1000))
            ch_title = ch.get("title", "Chapter")
            lines.append("[CHAPTER]")
            lines.append("TIMEBASE=1/1000")
            lines.append(f"START={start_ms}")
            lines.append(f"END={end_ms}")
            lines.append(f"title={ch_title}")
        chapter_file.write_text("\n".join(lines) + "\n")
        cmd += ["-f", "ffmetadata", "-i", str(chapter_file)]

    cmd += ["-f", "mp4", "-brand", "M4B "]
    if has_cover:
        cmd += ["-map", "0:a", "-map", "1:v", "-c:v", "mjpeg", "-disposition:v:0", "attached_pic"]
    else:
        cmd += ["-map", "0:a"]
    cmd += [
        "-c:a", "aac", "-b:a", AAC_BITRATE, "-ar", str(PACK_SAMPLE_RATE), "-ac", "1",
        "-movflags", "+faststart",
        "-metadata", f"title={title}",
        "-metadata", f"artist={author}",
        "-metadata", "album_artist=Michael (CosyVoice 3)",
        "-metadata", "genre=Audiobook",
    ]
    if has_cover:
        cmd += ["-metadata:s:v:0", "title=Cover Art", "-metadata:s:v:0", "comment=Cover Art"]
    cmd.append(str(m4b_path))
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        # Surface ffmpeg's own complaint — the captured stderr is the
        # only real diagnosis of a packaging failure (#105/#109).
        raise RuntimeError(
            f"M4B ffmpeg failed ({exc.returncode}): {(exc.stderr or '').strip()[:800]}"
        ) from exc

    # Patch stik=2 into the M4B (ffmpeg doesn't set this atom)
    _patch_stik(m4b_path)


def _patch_stik(m4b_path: pathlib.Path) -> None:
    """Insert a stik=2 atom into an M4B file if not present.

    The validator checks for b"stik" followed by a 32-bit big-endian value of 2.
    We append a minimal stik box at the end of the file.
    """
    data = m4b_path.read_bytes()
    if b"stik" in data:
        # Check if existing stik has value 2
        idx = data.find(b"stik")
        if idx + 8 <= len(data):
            val = struct.unpack(">I", data[idx + 4 : idx + 8])[0]
            if val == 2:
                return
    # Append stik box: size(8) + 'stik' + value(2)
    stik_box = struct.pack(">I", 8) + b"stik" + struct.pack(">I", 2)
    m4b_path.write_bytes(data + stik_box)


def make_silence_wav(path: pathlib.Path, seconds: float, sample_rate: int = PACK_SAMPLE_RATE) -> None:
    """Create a silence WAV file using ffmpeg."""
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"anullsrc=r={sample_rate}:cl=mono",
        "-t", str(seconds),
        "-ar", str(sample_rate), "-ac", "1",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def concat_wavs(inputs: list[pathlib.Path], output: pathlib.Path) -> None:
    """Concatenate WAV files using ffmpeg."""
    if not inputs:
        raise ValueError("No input files to concatenate")
    if len(inputs) == 1:
        shutil.copy2(inputs[0], output)
        return
    list_file = output.with_suffix(".txt")
    list_file.write_text("\n".join(f"file '{p}'" for p in inputs))
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy",
        str(output),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    list_file.unlink(missing_ok=True)


def get_audio_duration(path: pathlib.Path) -> float:
    """Get audio duration in seconds using ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def master_wav(input_path: pathlib.Path, output_path: pathlib.Path) -> None:
    """Apply mastering chain: loudnorm + resample to 44.1k mono.

    Target I=-18 LUFS: the documented ACX range is -23 to -18
    (docs/data-models/voice-recipe.md mastering field table); -16 sat
    above (louder than) the permitted ceiling. True peak -3 dB per ACX.
    """
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(input_path),
        "-af", f"loudnorm=I={MASTER_TARGET_LUFS}:TP=-3:LRA=11,aresample={PACK_SAMPLE_RATE}",
        "-ac", "1",
        str(output_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"mastering ffmpeg failed ({exc.returncode}): {(exc.stderr or '').strip()[:600]}"
        ) from exc


def measure_loudness(wav_path: pathlib.Path) -> dict[str, float | None]:
    """Measure integrated LUFS and true peak (ffmpeg loudnorm analysis)."""
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(wav_path),
        "-af", "loudnorm=print_format=summary", "-f", "null", "-",
    ]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stderr
    lufs = peak = None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Input Integrated:"):
            lufs = float(line.split(":")[1].split()[0])
        elif line.startswith("Input True Peak:"):
            peak = float(line.split(":")[1].split()[0])
    return {"integrated_lufs": lufs, "true_peak_db": peak}


def measure_noise_floor(wav_path: pathlib.Path) -> float | None:
    """Minimum per-window RMS across the file — the noise floor (dB).

    astats over 100ms windows; the quietest window's RMS is the floor a
    listener hears in pauses. -60 dB is the ACX ceiling.
    """
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(wav_path),
        "-af", "astats=metadata=1:reset=1:length=0.1,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
        "-f", "null", "-",
    ]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    values = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("lavfi.astats.Overall.RMS_level="):
            raw = line.split("=", 1)[1].strip()
            try:
                values.append(float(raw))
            except ValueError:
                continue
    return min(values) if values else None


def acx_verdict(full_wav: pathlib.Path) -> dict[str, Any]:
    """Measure the final audiobook against the documented ACX spec
    (voice-recipe.md qc: -23..-18 LUFS, true peak <= -3 dB, noise floor
    <= -60 dB). The verdict is MEASURED, never asserted."""
    loud = measure_loudness(full_wav)
    floor = measure_noise_floor(full_wav)
    lufs, peak = loud.get("integrated_lufs"), loud.get("true_peak_db")
    ok_loudness = lufs is not None and -23.0 <= lufs <= -18.0
    ok_peak = peak is not None and peak <= -3.0
    ok_floor = floor is not None and floor <= -60.0
    return {
        "measured": {
            "integrated_lufs": lufs,
            "true_peak_db": peak,
            "noise_floor_db": floor,
        },
        "checks": {
            "loudness_-23_-18": ok_loudness,
            "true_peak_le_-3": ok_peak,
            "noise_floor_le_-60": ok_floor,
        },
        "acx_compliant": bool(ok_loudness and ok_peak and ok_floor),
    }


def wav_to_mp3(input_path: pathlib.Path, output_path: pathlib.Path) -> None:
    """Convert WAV to MP3 192k CBR 44.1k mono."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(input_path),
        "-c:a", "libmp3lame", "-b:a", MP3_BITRATE, "-ar", str(PACK_SAMPLE_RATE), "-ac", "1",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def trim_to_duration(input_path: pathlib.Path, output_path: pathlib.Path, seconds: float) -> None:
    """Trim audio to a maximum duration."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(input_path),
        "-t", str(seconds),
        "-c", "copy",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


# ---------------------------------------------------------------------------
# LLM annotation (optional)
# ---------------------------------------------------------------------------


def load_llm_settings() -> dict[str, Any] | None:
    """Return LLM settings if configured, else None."""
    base_url = os.environ.get("LLM_BASE_URL", "").strip()
    model = os.environ.get("LLM_MODEL", "").strip()
    if not base_url or not model:
        return None
    return {
        "base_url": base_url,
        "api_key": os.environ.get("LLM_API_KEY", "").strip(),
        "model": model,
        "timeout": float(os.environ.get("LLM_TIMEOUT_SECONDS", "120")),
    }


def _llm_chat(llm_settings: dict[str, Any], user_prompt: str) -> str:
    """One chat completion against the declared LLM endpoint."""
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
            content = "".join(
                p.get("text", "") if isinstance(p, dict) else str(p) for p in content
            )
        return str(content).strip()


def annotate_chapter(
    chapter: dict[str, Any],
    contract: AnnotationContract,
    llm_settings: dict[str, Any],
    style: str,
    style_desc: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """Annotate one chapter's segments (#112, per docs/tasks/recipe-schema.md).

    Segments run SERIALLY with the previous segment's ending state passed
    into the next prompt; the chapter is an emotional reset point. Each
    segment: prompt assembled from the engine's declared capabilities +
    markup examples + style + previous state + chunk text; response parsed
    to cues; a chunk whose words do not match the source is REJECTED AND
    RETRIED (contract.retries), then falls back to no-cues and is counted.

    Returns ({seg_id: {"cues": ..., "state_after": ...}}, stats). The
    returned text is never kept — cues are metadata keyed by segment ID.
    """
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
            contract, ANNOTATION_PROMPT_PATH, MARKUP_EXAMPLES_PATH,
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
                log.warning(
                    "Rejected annotation for %s (first mismatch: %r), attempt %d/%d",
                    seg_id, bad, attempt + 1, attempts,
                )
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
            log.info(
                "Annotated %s (%d tags, direction=%s, state=%s)",
                seg_id, len(cues.get("tags", [])),
                "yes" if cues.get("direction") else "no",
                (parsed.get("state") or "-") if parsed else "-",
            )
        cues_map[seg_id] = {"cues": cues, "state_after": state}
    return cues_map, stats


def _merge_annotation_stats(target: dict[str, int], chapter_stats: dict[str, int]) -> None:
    for key, value in chapter_stats.items():
        target[key] = target.get(key, 0) + value


# ---------------------------------------------------------------------------
# TTS generation
# ---------------------------------------------------------------------------


def get_device() -> str:
    """Return 'cuda' if available, else 'cpu' (reporting only — CosyVoice
    selects its device internally)."""
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except ImportError:
        pass
    return "cpu"


def resolve_model_dir() -> str:
    """Resolve the pinned CosyVoice 3 model directory.

    COSYVOICE_MODEL_DIR overrides the snapshot location (pre-warmed
    cache); otherwise the pinned HF revision downloads on first run.
    """
    model_dir = os.environ.get("COSYVOICE_MODEL_DIR", "").strip()
    if model_dir:
        return model_dir
    from huggingface_hub import snapshot_download

    log.info(
        "Resolving CosyVoice 3 weights %s@%s...",
        COSYVOICE_REPO,
        COSYVOICE_REVISION[:12],
    )
    start = time.time()
    model_dir = snapshot_download(COSYVOICE_REPO, revision=COSYVOICE_REVISION)
    log.info("Weights ready in %.1fs", time.time() - start)
    return model_dir


def load_tts(model_dir: str, flags: dict[str, bool]):
    """Load CosyVoice 3 with the #93 performance flags.

    AutoModel dispatches to the CosyVoice3 class from the model's config
    and moves to CUDA when torch sees a GPU. fp16 autocasts the LLM and
    the flow; load_trt swaps the flow estimator for a TensorRT engine
    built (once, then cached in the model dir) from the fp32 ONNX that
    ships in the weights snapshot.
    """
    from cosyvoice.cli.cosyvoice import AutoModel

    log.info(
        "Loading CosyVoice 3 from %s (fp16=%s, load_trt=%s)...",
        model_dir, flags["fp16"], flags["load_trt"],
    )
    start = time.time()
    model = AutoModel(model_dir=model_dir, fp16=flags["fp16"], load_trt=flags["load_trt"])
    log.info(
        "CosyVoice 3 loaded in %.1fs (sample_rate=%d)",
        time.time() - start,
        int(getattr(model, "sample_rate", TTS_SAMPLE_RATE)),
    )
    return model


def generate_chunk(model, text: str, output_wav: pathlib.Path, instruct: str, seed: int) -> None:
    """Synthesize a single text chunk to a WAV file.

    inference_instruct2 clones the reference voice (voices/Michael.wav)
    with no transcript needed, while the instruct text directs delivery.
    The seed is re-applied before every call so the whole take samples
    from one RNG state — a stable narrator and a reproducible take, and
    the property that makes chunk-level parallelism safe (#93): any
    worker process given the same seed produces the same chunk.
    """
    import torch
    import torchaudio

    log.info("TTS: %d chars -> %s", len(text), output_wav.name)
    torch.manual_seed(seed)
    chunks = list(model.inference_instruct2(text, instruct, str(VOICE_PATH), stream=False))
    pieces = [c["tts_speech"] for c in chunks if c.get("tts_speech") is not None]
    if not pieces:
        raise RuntimeError("CosyVoice produced no audio")
    wav = torch.cat([torch.as_tensor(p, dtype=torch.float32).reshape(-1) for p in pieces])
    torchaudio.save(
        str(output_wav),
        wav.unsqueeze(0),
        int(getattr(model, "sample_rate", TTS_SAMPLE_RATE)),
    )


# Pool worker state: each parallel process loads its own engine instance.
_POOL_MODEL = None


def _pool_init(model_dir: str, flags: dict[str, bool]) -> None:
    global _POOL_MODEL
    _POOL_MODEL = load_tts(model_dir, flags)


def _pool_task(task: tuple) -> tuple[int, str | None]:
    idx, text, out_path, instruct, seed = task
    try:
        generate_chunk(_POOL_MODEL, text, pathlib.Path(out_path), instruct, seed)
        return idx, None
    except Exception as exc:  # noqa: BLE001 - per-chunk failures fall back to silence
        return idx, str(exc)


def synthesize_all(
    tasks: list[tuple],
    model_dir: str,
    flags: dict[str, bool],
    n_parallel: int,
) -> dict[int, str | None]:
    """Synthesize (idx, text, out_path, instruct, seed) tasks (#93).

    Returns {idx: error_or_None}. n_parallel <= 1 (or a single task) runs
    in-process on one engine. n_parallel > 1 spawns worker processes that
    each load their own engine; the parent warm-loads once first so
    exactly-once work — the TensorRT plan build and any first-run cache
    fill — happens before workers start and no two workers race on the
    plan file. The warm copy is freed before the pool spawns.
    """
    if n_parallel <= 1 or len(tasks) < 2:
        model = load_tts(model_dir, flags)
        results: dict[int, str | None] = {}
        for task in tasks:
            idx, text, out_path, instruct, seed = task
            try:
                generate_chunk(model, text, pathlib.Path(out_path), instruct, seed)
                results[idx] = None
            except Exception as exc:  # noqa: BLE001 - per-chunk fallback
                results[idx] = str(exc)
        return results

    import torch

    log.info("Warm-up load (builds/caches the TRT plan exactly once)...")
    warm = load_tts(model_dir, flags)
    del warm
    torch.cuda.empty_cache()

    import multiprocessing as mp

    log.info("Synthesizing %d chunks on %d workers...", len(tasks), n_parallel)
    ctx = mp.get_context("spawn")
    with ctx.Pool(n_parallel, initializer=_pool_init, initargs=(model_dir, flags)) as pool:
        return {idx: err for idx, err in pool.map(_pool_task, tasks)}


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def load_script(input_dir: pathlib.Path) -> dict[str, Any]:
    """Load and validate the narrator script."""
    script_path = input_dir / "script.json"
    if not script_path.exists():
        raise FileNotFoundError(f"Missing {script_path}")
    with open(script_path) as f:
        script = json.load(f)
    if not script.get("chapters"):
        raise ValueError("script.json has no chapters")
    return script


def load_metadata(input_dir: pathlib.Path) -> dict[str, Any]:
    """Load book metadata, falling back to script fields."""
    meta_path = input_dir / "metadata.json"
    if meta_path.exists():
        with open(meta_path) as f:
            return json.load(f)
    return {}


def main() -> int:
    start_time = time.time()
    log.info("Recipe starting: input=%s output=%s", INPUT_DIR, OUTPUT_DIR)

    # Validate inputs
    if not INPUT_DIR.exists():
        log.error("Input directory %s does not exist", INPUT_DIR)
        return 1
    if not VOICE_PATH.exists():
        log.error("Voice file %s does not exist", VOICE_PATH)
        return 1

    # GPU or fail (#103): synthesis never runs CPU-only. A missing CUDA
    # device is a loud failure, not a silent ~27x slowdown — and the
    # engine would silently disable fp16/TRT on top of it.
    if get_device() != "cuda":
        log.error(
            "No CUDA device visible to the recipe — TTS is GPU-or-fail (#103). "
            "Check the worker's GPU device request (GPU_DEVICE)."
        )
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "m4b").mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "mp3").mkdir(parents=True, exist_ok=True)
    checkpoint_dir = OUTPUT_DIR / "checkpoint"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    # Load inputs
    script = load_script(INPUT_DIR)
    metadata = load_metadata(INPUT_DIR)
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
    flags = engine_flags()
    n_parallel = parallel_workers()

    log.info(
        "Book: %s by %s (%d chapters), style=%s, seed=%d, fp16=%s, trt=%s, workers=%d",
        title, author, len(script["chapters"]), style, seed,
        flags["fp16"], flags["load_trt"], n_parallel,
    )

    # Stage 1 setup: LLM annotation — the SYSTEM layer builds the prompt
    # from the engine's DECLARED capabilities (#112); this recipe only
    # declares the engine (recipe.yaml -> engine/capabilities.yaml) and
    # its styles. The style base is the instruct backbone; per-segment
    # directions layer on top of it.
    llm_settings = load_llm_settings()
    contract = AnnotationContract.from_capabilities(CAPABILITIES_PATH)
    style_desc = style_description(style)
    base_instruct = style_base(style)
    if contract.configured:
        default_instruct = compose_instruct(contract, base_instruct, None)
    else:
        # No declared contract: keep the engine's legacy static instruct.
        default_instruct = f"{SYSTEM_PROMPT} {base_instruct}{ENDOFPROMPT}"
    log.info("Instruct base (%d chars): %s", len(default_instruct), default_instruct[:120])
    annotation_cues: dict[str, dict[str, Any]] = {}
    annotation_stats: dict[str, int] = {}

    # Stage 2: plan every synthesis task first (annotation, hint strip,
    # chunking), then execute the whole set in one pass — serially on one
    # engine, or across worker processes (#93).
    temp_dir = tempfile.mkdtemp(prefix="recipe-")
    device = get_device()
    log.info("TTS device: %s", device)

    chapter_info: list[dict[str, Any]] = []
    existing_checkpoints: dict[int, pathlib.Path] = {}
    for f in checkpoint_dir.glob("chapter-*.wav"):
        try:
            ch_num = int(f.stem.split("-")[1])
            existing_checkpoints[ch_num] = f
        except (ValueError, IndexError):
            continue
    if existing_checkpoints:
        log.info("Loaded %d chapter checkpoints from %s", len(existing_checkpoints), checkpoint_dir)

    # Stage 1 execution: annotate every non-checkpointed chapter BEFORE
    # task planning — chapters in parallel bounded by the declared LLM
    # seats, segments serial within a chapter with state passing (docs:
    # recipe-schema.md Chunk Strategy). Cues are metadata keyed by
    # segment ID; the clean script stays the single text authority.
    if llm_settings and contract.configured:
        slots = max(1, int(llm_settings.get("slots") or 2))
        log.info(
            "LLM annotation enabled (engine=%s, declared tags=%s, seats=%d, model=%s)",
            contract.engine, sorted(contract.declared_tag_set), slots, llm_settings["model"],
        )
        chapters_to_annotate = [
            ch for ch in script["chapters"]
            if ch.get("segments") and ch.get("chapter") not in existing_checkpoints
        ]
        if chapters_to_annotate:
            if slots == 1 or len(chapters_to_annotate) == 1:
                for ch in chapters_to_annotate:
                    cues_map, stats = annotate_chapter(ch, contract, llm_settings, style, style_desc)
                    annotation_cues.update(cues_map)
                    _merge_annotation_stats(annotation_stats, stats)
            else:
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=min(slots, len(chapters_to_annotate))) as pool:
                    results = pool.map(
                        lambda ch: annotate_chapter(ch, contract, llm_settings, style, style_desc),
                        chapters_to_annotate,
                    )
                    for cues_map, stats in results:
                        annotation_cues.update(cues_map)
                        _merge_annotation_stats(annotation_stats, stats)
            log.info("Annotation pass complete: %s", annotation_stats)
            annotated_path = OUTPUT_DIR / "annotated-script.json"
            annotated_path.write_text(json.dumps(
                {
                    "engine": contract.engine,
                    "segments": {
                        seg_id: {"cues": entry.get("cues"), "state_after": entry.get("state_after")}
                        for seg_id, entry in annotation_cues.items()
                    },
                },
                indent=2,
            ))
            log.info("Annotated-script cues for %d segments -> %s", len(annotation_cues), annotated_path)
    elif llm_settings:
        log.info("LLM set but the engine declares no annotation contract — clean text")
    else:
        log.info("LLM annotation disabled (LLM_BASE_URL/LLM_MODEL not set), using clean text")

    tasks: list[tuple] = []
    chapter_wav_lists: dict[int, list[pathlib.Path]] = {}
    next_idx = 0

    for chapter in script["chapters"]:
        ch_num = chapter.get("chapter", len(chapter_info) + 1)
        ch_title = chapter.get("title", f"Chapter {ch_num}")
        segments = chapter.get("segments", [])
        if not segments:
            continue

        # Checkpointed chapters skip synthesis entirely.
        if ch_num in existing_checkpoints:
            cp_wav = existing_checkpoints[ch_num]
            chapter_info.append({"chapter": ch_num, "title": ch_title, "wav": cp_wav, "mastered": cp_wav})
            log.info("Chapter %d loaded from checkpoint", ch_num)
            continue

        ch_temp_dir = pathlib.Path(temp_dir) / f"ch{ch_num:03d}"
        ch_temp_dir.mkdir(parents=True, exist_ok=True)
        wav_list: list[pathlib.Path] = []

        for i, seg in enumerate(segments):
            seg_id = seg.get("id", f"seg-{i:03d}")
            text = seg.get("text", "").strip()
            if not text:
                continue

            # Engine input composed from the CLEAN text plus this
            # segment's cues (#112): declared inline tags only, never a
            # second text copy. No cues -> the clean text as written.
            entry = annotation_cues.get(seg_id) or {}
            cues = entry.get("cues") or {}
            tts_text = compose_tts_text(text, cues)

            # Per-segment instruct: style base plus the cue direction,
            # wrapped per the engine's declared instruct format.
            if contract.configured:
                seg_instruct = compose_instruct(contract, base_instruct, cues.get("direction"))
            else:
                seg_instruct = default_instruct

            # Chunk long text
            chunks = chunk_text(tts_text)
            for j, chunk in enumerate(chunks):
                wav_name = f"{seg_id}-{j:02d}.wav" if len(chunks) > 1 else f"{seg_id}.wav"
                wav_path = ch_temp_dir / wav_name
                tasks.append((next_idx, chunk, str(wav_path), seg_instruct, seed))
                wav_list.append(wav_path)
                next_idx += 1

        chapter_wav_lists[ch_num] = wav_list
        chapter_info.append({"chapter": ch_num, "title": ch_title, "temp_dir": ch_temp_dir})

    # Credits synthesize through the same task path.
    credits_dir = pathlib.Path(temp_dir) / "credits"
    credits_dir.mkdir(parents=True, exist_ok=True)
    opening_text = f"{title}. By {author}. An audiobook."
    closing_text = f"Thank you for listening to {title}."
    opening_wav = credits_dir / "opening.wav"
    closing_wav = credits_dir / "closing.wav"
    opening_idx = next_idx
    tasks.append((opening_idx, opening_text, str(opening_wav), default_instruct, seed))
    next_idx += 1
    closing_idx = next_idx
    tasks.append((closing_idx, closing_text, str(closing_wav), default_instruct, seed))
    next_idx += 1

    total_segments = next_idx - 2  # chapter chunks only; credits excluded

    if total_segments <= 0 and not existing_checkpoints:
        log.error("No chapters were generated")
        return 1

    # Stage 2 execution
    model_dir = resolve_model_dir()
    synth_start = time.time()
    errors = synthesize_all(tasks, model_dir, flags, n_parallel)
    synthesis_seconds = time.time() - synth_start

    failed_segments = 0
    for idx in range(total_segments):
        if errors.get(idx) is not None:
            failed_segments += 1
            out_path = pathlib.Path(tasks[idx][2])
            log.error("TTS failed for %s: %s", out_path.name, errors[idx])
            # Silence fallback keeps the pipeline moving (segment order preserved)
            make_silence_wav(out_path, 1.0, TTS_SAMPLE_RATE)

    for name, idx, wav in (
        ("Opening", opening_idx, opening_wav),
        ("Closing", closing_idx, closing_wav),
    ):
        if errors.get(idx) is not None:
            log.warning("%s credits TTS failed (%s), using silence", name, errors[idx])
            make_silence_wav(wav, 2.0, TTS_SAMPLE_RATE)

    # Assemble per-chapter WAVs in task order — deterministic regardless of
    # which worker synthesized which chunk.
    for ch in chapter_info:
        if "mastered" in ch:
            continue
        wav_list = chapter_wav_lists.get(ch["chapter"], [])
        ch_temp_dir = ch.pop("temp_dir")
        if not wav_list:
            continue
        ch_wav = ch_temp_dir / f"chapter-{ch['chapter']:03d}.wav"
        concat_wavs(wav_list, ch_wav)
        ch["wav"] = ch_wav
        log.info("Chapter %d complete", ch["chapter"])

    if not any("wav" in ch for ch in chapter_info):
        log.error("No chapters were generated")
        return 1

    # Stage 3: Mastering
    log.info("Mastering audio...")
    mastered_dir = pathlib.Path(temp_dir) / "mastered"
    mastered_dir.mkdir(parents=True, exist_ok=True)

    mastered_chapters: list[pathlib.Path] = []
    for ch in chapter_info:
        # Chapters loaded from checkpoint already have their mastered WAV
        if "mastered" in ch:
            mastered_chapters.append(ch["mastered"])
            continue
        mastered = mastered_dir / f"chapter-{ch['chapter']:03d}.wav"
        master_wav(ch["wav"], mastered)
        # Checkpoint the mastered WAV for resume
        cp_path = checkpoint_dir / f"chapter-{ch['chapter']:03d}.wav"
        tmp_path = cp_path.with_suffix(".tmp")
        shutil.copy2(mastered, tmp_path)
        tmp_path.rename(cp_path)
        mastered_chapters.append(mastered)
        ch["mastered"] = mastered
        log.info("Chapter %d mastered and checkpointed", ch["chapter"])

    mastered_opening = mastered_dir / "opening.wav"
    mastered_closing = mastered_dir / "closing.wav"
    master_wav(opening_wav, mastered_opening)
    master_wav(closing_wav, mastered_closing)

    # Stage 4: Packaging
    log.info("Packaging...")
    layout = output_layout()

    # Per-chapter MP3s
    for ch in chapter_info:
        mp3_path = layout["mp3_dir"] / chapter_filename(ch["chapter"])
        wav_to_mp3(ch["mastered"], mp3_path)

    # Credits MP3s
    wav_to_mp3(mastered_opening, layout["opening_credits"])
    wav_to_mp3(mastered_closing, layout["closing_credits"])

    # Full audiobook WAV (for M4B and retail sample)
    full_wav = mastered_dir / "full.wav"
    all_mastered = [mastered_opening] + mastered_chapters + [mastered_closing]
    concat_wavs(all_mastered, full_wav)

    # M4B with chapters
    full_duration = get_audio_duration(full_wav)
    m4b_chapters = []
    current_ms = 0
    # Opening credits chapter
    opening_dur_ms = int(get_audio_duration(mastered_opening) * 1000)
    m4b_chapters.append({"start_ms": 0, "end_ms": opening_dur_ms, "title": "Opening Credits"})
    current_ms = opening_dur_ms
    for ch in chapter_info:
        ch_dur_ms = int(get_audio_duration(ch["mastered"]) * 1000)
        m4b_chapters.append({"start_ms": current_ms, "end_ms": current_ms + ch_dur_ms, "title": ch["title"]})
        current_ms += ch_dur_ms
    closing_dur_ms = int(get_audio_duration(mastered_closing) * 1000)
    m4b_chapters.append({"start_ms": current_ms, "end_ms": current_ms + closing_dur_ms, "title": "Closing Credits"})

    build_m4b_from_wav(full_wav, layout["m4b"], title, author, cover_path, m4b_chapters)

    # Retail sample: first ~75 seconds
    trim_to_duration(full_wav, layout["retail_sample"].with_suffix(".wav"), RETAIL_SAMPLE_SECONDS)
    wav_to_mp3(layout["retail_sample"].with_suffix(".wav"), layout["retail_sample"])

    # Metadata
    output_metadata = {
        "title": title,
        "author": author,
        "narrator": "Michael (CosyVoice 3)",
        "recipe": "michael-cosyvoice",
        "recipe_version": recipe_version,
        "performance_style": style,
        "language": script.get("language", "en"),
        "total_chapters": len(chapter_info),
        "total_duration_seconds": round(full_duration, 1),
        "sample_rate": PACK_SAMPLE_RATE,
        "mp3_bitrate": MP3_BITRATE,
        "m4b_codec": "aac",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    layout["metadata"].write_text(json.dumps(output_metadata, indent=2))

    # QC report
    # QC: MEASURE the final audiobook against the documented ACX spec
    # (#113-class fix: the old report hardcoded acx_compliant=true).
    acx = acx_verdict(full_wav)
    log.info(
        "ACX measured: LUFS=%s peak=%s dB floor=%s dB -> compliant=%s",
        acx["measured"]["integrated_lufs"], acx["measured"]["true_peak_db"],
        acx["measured"]["noise_floor_db"], acx["acx_compliant"],
    )
    # Per-segment synthesis failures with their reasons (#111): the count
    # alone hid the cause; the recipe log dies with the container.
    segment_failures = {
        pathlib.Path(tasks[idx][2]).name: str(errors[idx])
        for idx in range(total_segments)
        if errors.get(idx) is not None
    }

    qc_report = {
        "acx_compliant": acx["acx_compliant"],
        "acx_measurements": acx["measured"],
        "acx_checks": acx["checks"],
        "segment_failures": segment_failures,
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
        "checks": {
            "m4b_ftyp": "M4B",
            "m4b_stik": 2,
            "mp3_bitrate": MP3_BITRATE,
            "sample_rate": PACK_SAMPLE_RATE,
            "loudness_target": f"I={MASTER_TARGET_LUFS:g} LUFS (ACX -23..-18)",
        },
        "engine": {
            "name": "cosyvoice3",
            "model_repo": COSYVOICE_REPO,
            "model_revision": COSYVOICE_REVISION,
            "mode": "inference_instruct2",
            "voice": "voices/Michael.wav",
            "seed": seed,
            "fp16": flags["fp16"],
            "trt_flow": flags["load_trt"],
            "parallel_workers": n_parallel,
            "synthesis_seconds": round(synthesis_seconds, 1),
            "synthesis_rtf": round(synthesis_seconds / full_duration, 3) if full_duration else None,
        },
        "llm_annotation": llm_settings is not None,
        "annotation": {
            "engine_contract": contract.engine if contract.configured else None,
            "stats": annotation_stats,
        },
        "device": device,
        "elapsed_seconds": round(time.time() - start_time, 1),
    }
    layout["qc_report"].write_text(json.dumps(qc_report, indent=2))

    # Cleanup
    shutil.rmtree(temp_dir, ignore_errors=True)

    log.info("Recipe complete in %.1fs: %s", time.time() - start_time, layout["m4b"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
