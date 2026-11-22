"""Engine-neutral audio measurement, joins, and mastering — one definition.

All knobs are parameters; recipes pass their declared values, so no
module here reads any recipe.yaml. Moved from the CosyVoice recipe when
the second engine arrived (consolidation issue tracks the old copy).
"""

from __future__ import annotations

import logging
import pathlib
import shutil
import subprocess

log = logging.getLogger("recipe.audio")


def get_audio_duration(path: pathlib.Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def make_silence_wav(path: pathlib.Path, seconds: float, sample_rate: int) -> None:
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"anullsrc=r={sample_rate}:cl=mono",
        "-t", str(seconds), "-ar", str(sample_rate), "-ac", "1", str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def crossfade_join(wav_paths: list[pathlib.Path], output_path: pathlib.Path, overlap_ms: int) -> None:
    """Join chunk WAVs with an acrossfade per the docs' tts.overlap_ms.

    HAZARD (measured 2026-11-22, issue #120 arc): the recipe image's
    ffmpeg 7.1.5 truncates the accumulated stream when a NEXT input is
    sub-second — a 0.3s gap file collapsed a ~190s chain to ~11s with
    every call exiting 0. Never feed short clips into this chain; pad
    pauses INTO the chunk wavs instead (see prepend_seam_pauses in the
    ggardner recipe)."""
    if not wav_paths:
        raise ValueError("no wavs to join")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if len(wav_paths) == 1:
        shutil.copyfile(wav_paths[0], output_path)
        return
    d = max(1, overlap_ms) / 1000.0
    current = wav_paths[0]
    for i, nxt in enumerate(wav_paths[1:]):
        joined = output_path.with_suffix(f".xf{i:03d}.tmp.wav")
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(current), "-i", str(nxt),
            "-filter_complex", f"acrossfade=d={d:.3f}:c1=tri:c2=tri", str(joined),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError:
            log.warning("crossfade join failed at pair %d; plain concat", i)
            concat_wavs([current, nxt], joined)
        if current != wav_paths[0]:
            current.unlink(missing_ok=True)
        current = joined
    shutil.move(str(current), str(output_path))


def concat_wavs(inputs: list[pathlib.Path], output: pathlib.Path) -> None:
    if not inputs:
        raise ValueError("No input files to concatenate")
    if len(inputs) == 1:
        shutil.copy2(inputs[0], output)
        return
    list_file = output.with_suffix(".txt")
    list_file.write_text("\n".join(f"file '{p}'" for p in inputs))
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(output),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    list_file.unlink(missing_ok=True)


def master_wav(
    input_path: pathlib.Path,
    output_path: pathlib.Path,
    target_lufs: float = -18.0,
    pack_rate: int = 44100,
    room_tone_af: str = "",
) -> None:
    """Loudnorm to the declared target, resample to the pack rate, and
    append the declared room tone (adelay/apad filter chain)."""
    af = f"loudnorm=I={target_lufs}:TP=-3:LRA=11,aresample={pack_rate}"
    if room_tone_af:
        af += f",{room_tone_af}"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(input_path), "-af", af, "-ac", "1", str(output_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"mastering ffmpeg failed ({exc.returncode}): {(exc.stderr or '').strip()[:600]}") from exc


def room_tone_af(head_ms: int, tail_ms: int) -> str:
    parts = []
    if head_ms:
        parts.append(f"adelay={head_ms}:all=1")
    if tail_ms:
        parts.append(f"apad=pad_dur={tail_ms / 1000.0:.3f}")
    return ",".join(parts)


def measure_loudness(wav_path: pathlib.Path) -> dict[str, float | None]:
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(wav_path),
           "-af", "loudnorm=print_format=summary", "-f", "null", "-"]
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
    """Minimum per-window RMS across the file — the noise floor (dB)."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(wav_path),
           "-af", "astats=metadata=1:reset=1:length=0.1,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
           "-f", "null", "-"]
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


def acx_verdict(full_wav: pathlib.Path) -> dict:
    """Measure the final audiobook against the documented ACX spec:
    -23..-18 LUFS, true peak <= -3 dB, noise floor <= -60 dB. Measured,
    never asserted."""
    loud = measure_loudness(full_wav)
    floor = measure_noise_floor(full_wav)
    lufs, peak = loud.get("integrated_lufs"), loud.get("true_peak_db")
    ok_loudness = lufs is not None and -23.0 <= lufs <= -18.0
    ok_peak = peak is not None and peak <= -3.0
    ok_floor = floor is not None and floor <= -60.0
    return {
        "measured": {"integrated_lufs": lufs, "true_peak_db": peak, "noise_floor_db": floor},
        "checks": {"loudness_-23_-18": ok_loudness, "true_peak_<=_-3": ok_peak, "noise_floor_<=_-60": ok_floor},
        "acx_compliant": bool(ok_loudness and ok_peak and ok_floor),
    }


def wav_to_mp3(input_path: pathlib.Path, output_path: pathlib.Path, bitrate: str, pack_rate: int) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(input_path),
        "-c:a", "libmp3lame", "-b:a", bitrate, "-ar", str(pack_rate), "-ac", "1", str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def trim_to_duration(input_path: pathlib.Path, output_path: pathlib.Path, seconds: float) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(input_path), "-t", str(seconds), "-c", "copy", str(output_path)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
