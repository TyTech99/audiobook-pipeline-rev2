"""Engine-neutral packaging: M4B container, chapter MP3s, output layout.

One definition (consolidation issue tracks the CosyVoice recipe's older
local copy). All container parameters are passed in; nothing reads a
recipe.yaml here.
"""

from __future__ import annotations

import logging
import pathlib
import shutil
import struct
import subprocess
from typing import Any

log = logging.getLogger("recipe.pkg")


def chapter_filename(chapter_number: int) -> str:
    return f"chapter-{chapter_number:03d}.mp3"


def output_layout(output_dir: pathlib.Path) -> dict[str, pathlib.Path]:
    return {
        "m4b": output_dir / "m4b" / "audiobook.m4b",
        "mp3_dir": output_dir / "mp3",
        "opening_credits": output_dir / "mp3" / "opening-credits.mp3",
        "closing_credits": output_dir / "mp3" / "closing-credits.mp3",
        "retail_sample": output_dir / "mp3" / "retail-sample.mp3",
        "metadata": output_dir / "metadata.json",
        "qc_report": output_dir / "qc-report.json",
    }


def build_m4b_from_wav(
    wav_path: pathlib.Path,
    m4b_path: pathlib.Path,
    title: str,
    author: str,
    narrator: str,
    aac_bitrate: str,
    pack_rate: int,
    cover_path: pathlib.Path | None = None,
    chapters: list[dict[str, Any]] | None = None,
) -> None:
    """Encode a WAV to M4B with ftyp=M4B, metadata, and chapters via ffmpeg.

    All inputs first, then output options (#109). The mp4 muxer is forced
    because the .m4b-extension default (ipod muxer) rejects mjpeg covers.
    stik=2 is spliced in afterwards (_patch_stik) and the Nero chpl atom
    alongside the QuickTime track (_patch_nero_chapters).
    """
    m4b_path.parent.mkdir(parents=True, exist_ok=True)
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
            lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={start_ms}", f"END={end_ms}", f"title={ch_title}"]
        chapter_file.write_text("\n".join(lines) + "\n")
        cmd += ["-f", "ffmetadata", "-i", str(chapter_file)]

    cmd += ["-f", "mp4", "-brand", "M4B "]
    if has_cover:
        cmd += ["-map", "0:a", "-map", "1:v", "-c:v", "mjpeg", "-disposition:v:0", "attached_pic"]
    else:
        cmd += ["-map", "0:a"]
    cmd += [
        "-c:a", "aac", "-b:a", aac_bitrate, "-ar", str(pack_rate), "-ac", "1",
        "-movflags", "+faststart",
        "-metadata", f"title={title}",
        "-metadata", f"artist={author}",
        "-metadata", f"album={title}",
        "-metadata", f"album_artist={narrator}",
        "-metadata", f"composer={narrator}",
        "-metadata", "genre=Audiobook",
    ]
    if has_cover:
        cmd += ["-metadata:s:v:0", "title=Cover Art", "-metadata:s:v:0", "comment=Cover Art"]
    cmd.append(str(m4b_path))
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"M4B ffmpeg failed ({exc.returncode}): {(exc.stderr or '').strip()[:800]}") from exc

    _patch_stik(m4b_path)
    _patch_nero_chapters(m4b_path, chapters or [])


def _patch_stik(m4b_path: pathlib.Path) -> None:
    """Insert a stik=2 atom if not present (audiobook media flag)."""
    data = m4b_path.read_bytes()
    if b"stik" in data:
        idx = data.find(b"stik")
        if idx + 8 <= len(data):
            val = struct.unpack(">I", data[idx + 4 : idx + 8])[0]
            if val == 2:
                return
    stik_box = struct.pack(">I", 8) + b"stik" + struct.pack(">I", 2)
    m4b_path.write_bytes(data + stik_box)


def _patch_nero_chapters(m4b_path: pathlib.Path, chapters: list[dict[str, Any]]) -> None:
    """Insert a Nero chpl atom (VLC/Android chapters) into moov/udta."""
    if not chapters:
        return
    data = m4b_path.read_bytes()
    if b"chpl" in data:
        return
    payload = struct.pack(">B3s", 1, b"\x00\x00\x00") + struct.pack(">I", len(chapters))
    for ch in chapters:
        start_ms = int(ch.get("start_ms", 0))
        title = str(ch.get("title", ""))[:255].encode("utf-8")
        payload += struct.pack(">Q", start_ms * 10000)
        payload += struct.pack(">H", len(title)) + title
    chpl_box = struct.pack(">I", 8 + len(payload)) + b"chpl" + payload

    moov_idx = data.find(b"moov")
    if moov_idx < 4:
        log.warning("no moov box found; skipping Nero chapters")
        return
    udta_idx = data.find(b"udta", moov_idx)
    if udta_idx < 4:
        log.warning("no udta box found; skipping Nero chapters")
        return
    moov_size = struct.unpack(">I", data[moov_idx - 4 : moov_idx])[0]
    udta_size = struct.unpack(">I", data[udta_idx - 4 : udta_idx])[0]
    insert_at = udta_idx + udta_size

    out = bytearray(data[:insert_at]) + bytearray(chpl_box) + bytearray(data[insert_at:])
    struct.pack_into(">I", out, udta_idx - 4, udta_size + len(chpl_box))
    struct.pack_into(">I", out, moov_idx - 4, moov_size + len(chpl_box))
    m4b_path.write_bytes(bytes(out))
    log.info("Nero chapters written: %d markers (chpl atom)", len(chapters))
