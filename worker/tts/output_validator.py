"""Output validator for TTS worker output."""

from __future__ import annotations

import pathlib
import struct


def validate_audiobook_output(output_dir: str) -> list[str]:
    """Validate TTS worker output. Returns a list of errors (empty if valid)."""
    errors = []
    out = pathlib.Path(output_dir)

    m4b_path = out / "m4b" / "audiobook.m4b"
    if not m4b_path.exists():
        errors.append("Missing m4b/audiobook.m4b")
    else:
        errors.extend(_validate_m4b(m4b_path))

    mp3_dir = out / "mp3"
    if not mp3_dir.exists():
        errors.append("Missing mp3/ directory")
    else:
        mp3_files = list(mp3_dir.glob("chapter-*.mp3"))
        if not mp3_files:
            errors.append("No chapter MP3 files found")
        if not (mp3_dir / "opening-credits.mp3").exists():
            errors.append("Missing opening-credits.mp3")
        if not (mp3_dir / "closing-credits.mp3").exists():
            errors.append("Missing closing-credits.mp3")
        if not (mp3_dir / "retail-sample.mp3").exists():
            errors.append("Missing retail-sample.mp3")

    if not (out / "metadata.json").exists():
        errors.append("Missing metadata.json")
    if not (out / "qc-report.json").exists():
        errors.append("Missing qc-report.json")

    return errors


def _validate_m4b(path: pathlib.Path) -> list[str]:
    errors = []
    data = path.read_bytes()
    if len(data) < 16:
        errors.append("M4B file too small")
        return errors

    ftyp = data[4:8]
    if ftyp != b"ftyp":
        errors.append("M4B missing ftyp box")
        return errors

    major_brand = data[8:12]
    if major_brand != b"M4B ":
        errors.append(f"M4B major brand is {major_brand!r}, expected b'M4B '")

    if b"stik" not in data:
        errors.append("M4B missing stik atom")
    else:
        idx = data.find(b"stik")
        if idx + 8 <= len(data):
            stik_value = struct.unpack(">I", data[idx + 4 : idx + 8])[0]
            if stik_value != 2:
                errors.append(f"M4B stik value is {stik_value}, expected 2")

    return errors
