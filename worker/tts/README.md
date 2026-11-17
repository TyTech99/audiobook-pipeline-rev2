# TTS Worker

GPU worker that consumes book packages and produces finished, ACX-compliant audiobooks using voice recipes. The worker runs the full pipeline inside the recipe's Docker container: LLM annotation → TTS generation → mastering → packaging.

## Implementation Status

**Not yet implemented.** See design docs:

- [Architecture Overview](../../docs/architecture/overview.md)
- [Voice Recipe](../../docs/data-models/voice-recipe.md)
- [Audiobook Package](../../docs/data-models/audiobook-package.md)

## Pipeline Stages

The TTS worker runs four stages sequentially inside the recipe's Docker container:

### 1. LLM Annotation

- Loads the clean script from the book package
- Splits into chapters (parallel) → chunks (serial within chapter)
- For each chapter (parallel across chapters):
  - For each chunk (serial):
    - Assembles prompt: engine capabilities + markup examples + performance style prompt + previous chunk state + chunk text
    - LLM produces engine-specific markup
    - Validates chunk text against source (exact match required)
    - Captures ending state for next chunk
- Chapters are emotional reset points (state resets at chapter boundaries)
- Output: annotated script (engine-specific markup per chunk)

**Parallelism:**
- Cloud LLMs: unlimited parallel slots
- Local LLMs: seat count declared in worker registration
- The coordinator schedules chapter annotation based on available slots

### 2. TTS Generation

- Loads the annotated script
- Generates audio per chunk using the TTS engine
- Assembles chunks into chapters
- Chapters run in parallel; chunks within a chapter run serially

### 3. Mastering

- Applies the mastering chain from the voice recipe (HPF, EQ, compression, de-esser, noise reduction)
- True-peak limiting to the recipe ceiling
- Normalizes to target RMS loudness
- Inserts room tone at chapter boundaries
- Measures and verifies: RMS, peaks, noise floor
- Rejects chapters that fail QC (triggers retry)

### 4. Packaging

- Encodes chapters to final codecs (AAC for M4B, MP3 for submission)
- Assembles the M4B container with:
  - QuickTime chapter track (Apple Books)
  - Nero `chpl` atom (VLC/Android)
  - Metadata (title, author, narrator, genre, description)
  - Embedded cover art
  - `stik=2` audiobook flag
  - `M4B ` ftyp brand
- Assembles MP3 chapter files (192 kbps CBR, 44.1 kHz)
- Generates opening/closing credits
- Generates retail sample (1–5 minutes)
- Validates the final package (ffprobe, chapter count, metadata presence)

## Output Formats

**P1: Direct playback (user handoff)**
- Single M4B file for Apple Music, VLC, Google Play Books
- M4B provides: chapters, resume position, metadata, cover art in one file

**P2: Platform submission**
- Per-chapter MP3 files (192 kbps CBR, 44.1 kHz) for Spotify, ACX, aggregators
- Opening and closing credits (separate MP3 files)
- Retail sample (1-5 minutes)

## Planned Structure

```
worker/tts/
├── worker.py            ← Main worker loop (Redis BRPOP)
├── config.py            ← Configuration loading
├── annotation/
│   ├── chunker.py       ← Script chunking (paragraph/sentence)
│   ├── prompt.py        ← LLM prompt assembly
│   ├── state.py         ← Chunk state management
│   └── validator.py     ← Text validation per chunk
├── tts/
│   ├── base.py          ← TTS engine interface
│   ├── chatterbox.py    ← Chatterbox implementation
│   └── higgs.py         ← Higgs V3 implementation
├── recipe/
│   ├── loader.py        ← Voice recipe loader
│   └── validator.py     ← Recipe validation
├── audio/
│   ├── chunker.py       ← Audio chunking
│   ├── assembler.py     ← Chapter assembly
│   ├── mastering.py     ← Mastering chain (FFmpeg/DSP)
│   ├── packaging.py     ← M4B/MP3 packaging (FFmpeg/MP4 tools)
│   └── qc.py            ← Quality control (ACX spec check)
└── requirements.txt
```

## Dependencies

- Python 3.11+
- PyTorch (CUDA)
- TTS engines (Chatterbox, Higgs V3)
- LLM (for annotation) — cloud API or local model
- FFmpeg (mastering + packaging)
- redis-py
- boto3 (S3)
- PyYAML

## GPU Requirements

- NVIDIA GPU with CUDA support
- Minimum 8GB VRAM (varies by voice recipe)
- NVIDIA Container Toolkit for Docker GPU passthrough
- Sufficient CPU for mastering/packaging (any modern GPU workstation is adequate)

## LLM Slot Capabilities

- Cloud LLMs: unlimited parallel slots
- Local LLMs: seat count declared in worker registration
- The coordinator schedules chapter annotation based on available slots
