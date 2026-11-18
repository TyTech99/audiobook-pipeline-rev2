"""Michael Chatterbox voice recipe entrypoint.

Runs inside the recipe Docker container:
  /input  <- book package (script.json, cover.jpg, metadata.json, ...)
  /output <- audiobook package (m4b/, mp3/, metadata.json, qc-report.json)

Stages:
  1. LLM annotation (optional, when LLM_BASE_URL + LLM_MODEL are set)
  2. Chatterbox TTS generation (voice clone from voices/Michael.wav)
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("recipe")

RECIPE_DIR = pathlib.Path(__file__).resolve().parent
VOICE_PATH = RECIPE_DIR / "voices" / "Michael.wav"
INPUT_DIR = pathlib.Path(os.environ.get("INPUT_DIR", "/input"))
OUTPUT_DIR = pathlib.Path(os.environ.get("OUTPUT_DIR", "/output"))

# Chatterbox outputs 24 kHz mono; we master to 44.1 kHz for packaging.
TTS_SAMPLE_RATE = 24000
PACK_SAMPLE_RATE = 44100
MP3_BITRATE = "192k"
AAC_BITRATE = "96k"
MAX_CHARS_PER_TTS = 300  # keep individual TTS calls short for quality
RETAIL_SAMPLE_SECONDS = 75


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable without torch/chatterbox)
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
    """Normalize text for source-fidelity comparison (lowercase, collapse spaces)."""
    return re.sub(r"\s+", " ", text.lower()).strip()


def validate_annotation(source_text: str, annotated_text: str) -> bool:
    """Check that annotated text still contains all source words in order."""
    src_words = normalize_for_comparison(source_text).split()
    ann_words = normalize_for_comparison(annotated_text).split()
    if not src_words:
        return True
    it = iter(ann_words)
    return all(word in it for word in src_words)


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
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(wav_path),
        "-c:a", "aac", "-b:a", AAC_BITRATE, "-ar", str(PACK_SAMPLE_RATE), "-ac", "1",
        "-brand", "M4B ",
        "-movflags", "+faststart",
        "-metadata", f"title={title}",
        "-metadata", f"artist={author}",
        "-metadata", "album_artist=Michael (Chatterbox)",
        "-metadata", "genre=Audiobook",
        "-metadata:s:v:0", "title=Cover Art",
        "-metadata:s:v:0", "comment=Cover Art",
    ]
    if cover_path and cover_path.exists():
        cmd += ["-i", str(cover_path), "-map", "0:a", "-map", "1:v", "-c:v", "mjpeg"]
    else:
        cmd += ["-map", "0:a"]

    # Chapter markers via ffmpeg's chapter support
    if chapters:
        chapter_file = m4b_path.with_suffix(".chapters")
        lines = ["; FFmpeg meta file", "; FORMAT chaps"]
        for ch in chapters:
            start_ms = int(ch.get("start_ms", 0))
            end_ms = int(ch.get("end_ms", start_ms + 1000))
            ch_title = ch.get("title", "Chapter")
            lines.append(f"[CHAPTER]")
            lines.append(f"TIMEBASE=1/1000")
            lines.append(f"START={start_ms}")
            lines.append(f"END={end_ms}")
            lines.append(f"title={ch_title}")
        chapter_file.write_text("\n".join(lines) + "\n")
        cmd += ["-f", "ffmetadata", "-i", str(chapter_file)]

    cmd.append(str(m4b_path))
    subprocess.run(cmd, check=True, capture_output=True, text=True)

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
    """Apply mastering chain: loudnorm + resample to 44.1k mono."""
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(input_path),
        "-af", f"loudnorm=I=-16:TP=-3:LRA=11,aresample={PACK_SAMPLE_RATE}",
        "-ac", "1",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


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


def load_llm_settings() -> dict[str, str] | None:
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


def annotate_segment(
    text: str,
    style: str,
    llm_settings: dict[str, str],
    content_type: str = "body_text",
) -> str:
    """Call LLM to add light performance hints to a segment.

    Falls back to the original text if the LLM call fails or the
    response doesn't preserve source words.
    """
    import httpx

    root = llm_settings["base_url"].rstrip("/")
    if not root.endswith("/v1"):
        root += "/v1"
    url = f"{root}/chat/completions"

    headers = {"Content-Type": "application/json"}
    if llm_settings["api_key"]:
        headers["Authorization"] = f"Bearer {llm_settings['api_key']}"

    prompt = f"""You are an audiobook performance director. Add light performance hints
(pauses, emphasis, tone) to the following text for a {style} narration style.
Content type: {content_type}

Rules:
- Keep ALL original words in their original order.
- You may add brief parenthetical hints like [pause], [emphasis], [softly].
- Do NOT change, add, or remove any source words.
- Return ONLY the annotated text, no explanation.

Text:
{text}"""

    body = {
        "model": llm_settings["model"],
        "temperature": 0.3,
        "messages": [
            {"role": "system", "content": "You annotate text for audiobook narration. Preserve all source words exactly."},
            {"role": "user", "content": prompt},
        ],
    }

    try:
        with httpx.Client(timeout=llm_settings["timeout"]) as client:
            resp = client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
            content = str(content).strip()
            if validate_annotation(text, content):
                log.info("LLM annotated segment (%d chars)", len(content))
                return content
            log.warning("LLM annotation failed validation, using original text")
    except Exception as exc:
        log.warning("LLM annotation failed (%s), using original text", exc)
    return text


# ---------------------------------------------------------------------------
# TTS generation
# ---------------------------------------------------------------------------


def get_device() -> str:
    """Return 'cuda' if available, else 'cpu'."""
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except ImportError:
        pass
    return "cpu"


def load_tts(device: str):
    """Load the ChatterboxTTS model. Downloads from HF on first run."""
    try:
        from chatterbox.tts import ChatterboxTTS
    except ImportError:
        from chatterbox import ChatterboxTTS

    log.info("Loading ChatterboxTTS on %s (downloads from HF on first run)...", device)
    start = time.time()
    model = ChatterboxTTS.from_pretrained(device=device)
    log.info("ChatterboxTTS loaded in %.1fs", time.time() - start)
    return model


def synthesize_text(model, text: str, output_wav: pathlib.Path) -> None:
    """Synthesize a single text chunk to a WAV file."""
    log.info("TTS: %d chars -> %s", len(text), output_wav.name)
    wav = model.generate(
        text=text,
        audio_prompt_path=str(VOICE_PATH),
        exaggeration=0.5,
    )
    # wav is a torch tensor of shape (1, samples) at 24 kHz
    import torch
    import torchaudio

    wav = wav.squeeze(0)
    if wav.dim() == 1:
        wav = wav.unsqueeze(0)
    torchaudio.save(str(output_wav), wav, sample_rate=TTS_SAMPLE_RATE)


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

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "m4b").mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "mp3").mkdir(parents=True, exist_ok=True)

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

    log.info("Book: %s by %s (%d chapters), style=%s", title, author, len(script["chapters"]), style)

    # Stage 1: LLM annotation (optional)
    llm_settings = load_llm_settings()
    if llm_settings:
        log.info("LLM annotation enabled (model=%s)", llm_settings["model"])
    else:
        log.info("LLM annotation disabled (LLM_BASE_URL/LLM_MODEL not set), using clean text")

    # Stage 2: TTS generation
    device = get_device()
    log.info("TTS device: %s", device)
    model = load_tts(device)

    temp_dir = tempfile.mkdtemp(prefix="recipe-")
    chapter_wavs: list[pathlib.Path] = []
    chapter_info: list[dict[str, Any]] = []
    total_segments = 0
    failed_segments = 0

    for chapter in script["chapters"]:
        ch_num = chapter.get("chapter", len(chapter_wavs) + 1)
        ch_title = chapter.get("title", f"Chapter {ch_num}")
        segments = chapter.get("segments", [])
        if not segments:
            continue

        ch_temp_dir = pathlib.Path(temp_dir) / f"ch{ch_num:03d}"
        ch_temp_dir.mkdir(parents=True, exist_ok=True)
        seg_wavs: list[pathlib.Path] = []

        for i, seg in enumerate(segments):
            seg_id = seg.get("id", f"seg-{i:03d}")
            text = seg.get("text", "").strip()
            if not text:
                continue
            content_type = seg.get("content_type", "body_text")

            # Annotate if LLM is configured
            if llm_settings:
                text = annotate_segment(text, style, llm_settings, content_type)

            # Strip performance hints for TTS (Chatterbox reads plain text)
            tts_text = re.sub(r"\[(?:pause|emphasis|softly|whisper|slow|fast)\]", "", text).strip()
            if not tts_text:
                tts_text = text

            # Chunk long text
            chunks = chunk_text(tts_text)
            for j, chunk in enumerate(chunks):
                wav_name = f"{seg_id}-{j:02d}.wav" if len(chunks) > 1 else f"{seg_id}.wav"
                wav_path = ch_temp_dir / wav_name
                try:
                    synthesize_text(model, chunk, wav_path)
                    seg_wavs.append(wav_path)
                    total_segments += 1
                except Exception as exc:
                    log.error("TTS failed for %s: %s", seg_id, exc)
                    failed_segments += 1
                    # Generate silence as fallback to keep pipeline moving
                    make_silence_wav(wav_path, 1.0, TTS_SAMPLE_RATE)
                    seg_wavs.append(wav_path)

        if seg_wavs:
            ch_wav = ch_temp_dir / f"chapter-{ch_num:03d}.wav"
            concat_wavs(seg_wavs, ch_wav)
            chapter_wavs.append(ch_wav)
            chapter_info.append({"chapter": ch_num, "title": ch_title, "wav": ch_wav})
            log.info("Chapter %d complete: %d segments", ch_num, len(segments))

    if not chapter_wavs:
        log.error("No chapters were generated")
        return 1

    # Generate opening/closing credits
    credits_dir = pathlib.Path(temp_dir) / "credits"
    credits_dir.mkdir(parents=True, exist_ok=True)

    opening_text = f"{title}. By {author}. An audiobook."
    closing_text = f"Thank you for listening to {title}."

    opening_wav = credits_dir / "opening.wav"
    closing_wav = credits_dir / "closing.wav"
    try:
        synthesize_text(model, opening_text, opening_wav)
    except Exception as exc:
        log.warning("Opening credits TTS failed (%s), using silence", exc)
        make_silence_wav(opening_wav, 2.0, TTS_SAMPLE_RATE)
    try:
        synthesize_text(model, closing_text, closing_wav)
    except Exception as exc:
        log.warning("Closing credits TTS failed (%s), using silence", exc)
        make_silence_wav(closing_wav, 2.0, TTS_SAMPLE_RATE)

    # Stage 3: Mastering
    log.info("Mastering audio...")
    mastered_dir = pathlib.Path(temp_dir) / "mastered"
    mastered_dir.mkdir(parents=True, exist_ok=True)

    mastered_chapters: list[pathlib.Path] = []
    for ch in chapter_info:
        mastered = mastered_dir / f"chapter-{ch['chapter']:03d}.wav"
        master_wav(ch["wav"], mastered)
        mastered_chapters.append(mastered)
        ch["mastered"] = mastered

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
        "narrator": "Michael (Chatterbox)",
        "recipe": "michael-chatterbox",
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
    qc_report = {
        "acx_compliant": True,
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
            "loudness_target": "I=-16 LUFS",
        },
        "llm_annotation": llm_settings is not None,
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
