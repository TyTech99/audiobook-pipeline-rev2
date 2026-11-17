# Audiobook Package

The audiobook package is the **final output** of the pipeline. It contains multi-format audio files, metadata, and quality control reports. It is ACX-compliant and ready for distribution on Spotify, Apple Books, Google Play Books, and other platforms.

## Destination Goals

**P1: Direct playback (user handoff)**
- Single M4B file for Apple Music, VLC, Google Play Books
- M4B provides: chapters, resume position, metadata, cover art in one file

**P2: Platform submission**
- Per-chapter MP3 files (192 kbps CBR, 44.1 kHz) for Spotify, ACX, aggregators
- Opening and closing credits (separate MP3 files)
- Retail sample (1-5 minutes)

## File Structure

```
s3://audiobook-pipeline/audio/{job_id}/
├── m4b/
│   └── audiobook.m4b          ← Final M4B file (P1: direct playback)
├── mp3/
│   ├── opening-credits.mp3    ← Opening credits (title, author, narrator)
│   ├── chapter-001.mp3        ← Individual chapters (P2: platform submission)
│   ├── chapter-002.mp3
│   ├── ...
│   ├── closing-credits.mp3    ← Closing credits ("The End")
│   └── retail-sample.mp3      ← Retail sample (1–5 minutes)
├── metadata.json              ← Package metadata
└── qc-report.json             ← Quality control report
```

## M4B Container Specification

The M4B file is an MPEG-4 Part 14 (ISO Base Media File Format) container with audiobook-specific branding and metadata.

### Container Branding

| Field | Value | Description |
|-------|-------|-------------|
| `ftyp` major brand | `M4B ` | Audiobook brand (trailing space) |
| `ftyp` compatible brands | `M4A `, `isom`, `mp42` | Compatibility list |
| `stik` atom | `2` | Media type: audiobook |

The `stik=2` flag tells Apple Books and compatible players to treat the file as an audiobook (resume position, chapter navigation, file under Books instead of Music).

### Audio Codec

| Parameter | Value | Description |
|-----------|-------|-------------|
| Codec | AAC-LC | Advanced Audio Coding, Low Complexity |
| Bitrate | 32–128 kbps | Speech doesn't need high bitrate |
| Sample rate | 44.1 kHz | Standard |
| Channels | Mono (preferred) | Speech doesn't need stereo |

### Metadata Atoms

Metadata lives in `moov` → `udta` → `meta` → `ilst`. Atoms use 4-character codes (© is 0xA9).

| Atom | Field | Description | Example |
|------|-------|-------------|---------|
| `©nam` | Title | Book title | "The Castle" |
| `©ART` | Artist | Author name | "Jane Doe" |
| `aART` | Album Artist | Author name (for library display) | "Jane Doe" |
| `©alb` | Album | Book title or series | "The Castle" |
| `©wrt` | Composer | Narrator name | "John Smith" |
| `©nrt` | Narrator | Narrator name (alternative) | "John Smith" |
| `©gen` | Genre | Genre | "Fiction" |
| `desc` | Description | Book summary | "A tale of mystery and intrigue..." |
| `covr` | Cover Art | Embedded JPEG/PNG (square) | 600–1400 px |
| `©day` | Year | Publication year | "2026" |
| `stik` | Media Type | Audiobook flag | `2` |

### Chapter Markers

M4B files support two incompatible chapter formats. The pipeline writes **both** for maximum compatibility.

#### 1. QuickTime Chapter Track (mandatory for Apple Books)

Documented in Apple Technical Note TN2174. This is a disabled text track referenced from the audio track.

- Separate track with handler type `text`
- Media information uses `nmhd` (Null Media Header)
- Sample entry: MPEG-4 Timed Text (`TextSampleEntry`)
- Sample format: 16-bit unsigned integer (byte length of title) followed by UTF-8 title string
- Track header: `track_enabled` flag = 0 (disabled)
- Track reference: From the audio track, a `tref` box of type `chap` pointing to the chapter track's ID

#### 2. Nero `chpl` Atom (for VLC, Android, other players)

A simple list inside `moov` → `udta` → `chpl`:

- FullBox with version, flags, unknown (usually 0), chapter count
- Per chapter: 64-bit timestamp (in 100-nanosecond units from start), 8-bit title length, UTF-8 title

### Chapter Requirements

- One chapter per book chapter (from the narrator script)
- Each chapter starts with the chapter title read aloud (if `chapters.read_header` is true in the recipe)
- Chapter titles must match the narrator script exactly
- Chapter timestamps must be accurate (within 100ms)
- No chapter may exceed 120 minutes (split longer chapters)

## MP3 Chapter Specification

The MP3 chapter files are for platform submission (Spotify, ACX, aggregators).

### Audio Codec

| Parameter | Value | Description |
|-----------|-------|-------------|
| Codec | MP3 | MPEG Layer III |
| Bitrate | 192 kbps | ACX requirement |
| Bit rate mode | CBR | Constant bit rate (VBR is rejected) |
| Sample rate | 44.1 kHz | Standard |
| Channels | Mono (preferred) | Consistent across all files |

### File Structure

- One file per chapter
- Opening credits: separate file (title, author, narrator)
- Closing credits: separate file ("The End")
- Retail sample: 1–5 minutes from the book
- Each file ≤ 120 minutes

### Room Tone

- 0.5–1 second of actual room tone at the start of each file
- 1–5 seconds of actual room tone at the end of each file

## ACX/Spotify Compliance

The audiobook package must meet ACX (Audiobook Creation Exchange) and Spotify technical requirements.

### Audio Levels (per chapter file)

| Parameter | ACX Requirement | Spotify Requirement | Target |
|-----------|-----------------|---------------------|--------|
| RMS loudness | -23 dB to -18 dB | -24 dB to -14 dB | -20 dB |
| True peak | ≤ -3 dB | — | -3.5 dB |
| Noise floor | ≤ -60 dB RMS | ≤ -60 dB | ≤ -65 dB |

### Format

- Individual chapter files: MP3, 192 kbps CBR, 44.1 kHz
- Final M4B: AAC-LC, 32–128 kbps, 44.1 kHz

## Metadata JSON

The `metadata.json` file alongside the audio files contains the full package metadata for the coordinator and for API responses.

```json
{
  "package_id": "uuid",
  "job_id": "uuid",
  "created_at": "2026-11-17T10:30:00Z",
  "voice_recipe": {
    "name": "narrator-deep-male-v2",
    "version": "2.1.0"
  },
  "performance_style": "noir-mystery",
  "book": {
    "title": "The Castle",
    "author": "Jane Doe",
    "narrator": "John Smith",
    "language": "en",
    "genre": "Fiction",
    "description": "A tale of mystery and intrigue..."
  },
  "audio": {
    "m4b": {
      "format": "m4b",
      "codec": "aac",
      "bitrate": "96k",
      "sample_rate": 44100,
      "channels": 1,
      "duration_seconds": 24600,
      "duration_hours": 6.83
    },
    "mp3": {
      "format": "mp3",
      "codec": "mp3",
      "bitrate": "192k",
      "cbr": true,
      "sample_rate": 44100,
      "channels": 1
    }
  },
  "chapters": [
    {
      "number": 1,
      "title": "The Arrival",
      "start_time_ms": 0,
      "end_time_ms": 1800000,
      "duration_ms": 1800000
    },
    {
      "number": 2,
      "title": "The Gate",
      "start_time_ms": 1800000,
      "end_time_ms": 3600000,
      "duration_ms": 1800000
    }
  ],
  "cover_art": {
    "url": "s3://audiobook-pipeline/audio/{job_id}/cover.jpg",
    "size": [1200, 1200],
    "format": "jpeg"
  },
  "files": {
    "m4b": "m4b/audiobook.m4b",
    "mp3": {
      "opening_credits": "mp3/opening-credits.mp3",
      "chapters": "mp3/chapter-*.mp3",
      "closing_credits": "mp3/closing-credits.mp3",
      "retail_sample": "mp3/retail-sample.mp3"
    }
  },
  "qc": {
    "acx_compliant": true,
    "rms_db": -20.1,
    "peak_db": -3.8,
    "noise_floor_db": -67.2,
    "checked_at": "2026-11-17T10:30:00Z"
  }
}
```

## QC Report JSON

The `qc-report.json` file contains the detailed quality control results.

```json
{
  "job_id": "uuid",
  "checked_at": "2026-11-17T10:30:00Z",
  "acx_compliant": true,
  "checks": {
    "rms_loudness": {
      "pass": true,
      "value_db": -20.1,
      "required": "[-23, -18]"
    },
    "true_peak": {
      "pass": true,
      "value_db": -3.8,
      "required": "<= -3"
    },
    "noise_floor": {
      "pass": true,
      "value_db": -67.2,
      "required": "<= -60"
    },
    "sample_rate": {
      "pass": true,
      "value": 44100,
      "required": 44100
    },
    "bitrate": {
      "pass": true,
      "value": "192k",
      "required": ">= 192k"
    },
    "channels": {
      "pass": true,
      "value": 1,
      "required": "consistent (mono or stereo)"
    },
    "chapter_markers": {
      "pass": true,
      "quicktime_track": true,
      "nero_chpl": true,
      "chapter_count": 12
    },
    "metadata": {
      "pass": true,
      "title": true,
      "author": true,
      "narrator": true,
      "cover_art": true,
      "stik_flag": true
    },
    "room_tone": {
      "pass": true,
      "head_ms": 1000,
      "tail_ms": 3000
    }
  },
  "errors": [],
  "warnings": []
}
```

## Validation Rules

An audiobook package is valid if and only if:

1. **M4B container**: `ftyp` brand is `M4B `, `stik` atom is `2`
2. **M4B audio**: AAC-LC, 44.1 kHz, consistent channels
3. **MP3 chapters**: MP3, 192 kbps CBR, 44.1 kHz, consistent channels
4. **Levels**: RMS -23 to -18 dB, peak ≤ -3 dB, noise floor ≤ -60 dB
5. **Chapters**: QuickTime chapter track present, Nero `chpl` present, timestamps accurate
6. **Metadata**: Title, author, narrator, cover art present
7. **Room tone**: 0.5–5 seconds at head and tail of each chapter
8. **Duration**: No chapter exceeds 120 minutes
9. **Credits**: Opening and closing credits present
10. **Sample**: Retail sample present (1–5 minutes)

If any validation fails, the job is marked `convert_failed` with detailed error messages in the QC report.
