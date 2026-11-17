# Voice Recipe

A voice recipe is a **self-contained bundle** that defines everything needed to produce a finished audiobook for a specific voice. It covers LLM annotation, TTS generation, audio mastering, and final packaging. It is deployable to any worker meeting the minimum hardware requirements and produces consistent, reproducible output.

## Purpose

- Ensure any worker produces identical output for the same input + recipe
- Encapsulate voice-specific parameters (model, engine, preprocessing)
- Encapsulate engine capabilities and annotation markup examples
- Encapsulate performance style prompts
- Encapsulate mastering parameters (EQ, compression, loudness, noise floor)
- Encapsulate packaging parameters (formats, codec, chapters, metadata, cover art)
- Enable versioning and rollback
- Support multiple TTS stacks (Chatterbox, Higgs V3, Piper, etc.)

## Storage

Voice recipes live in a **separate git repo** from the pipeline code. This enables both systems to iterate independently.

The recipe **schema** is fixed and versioned. The schema is defined in this repo's docs. Individual recipes conform to the schema and are versioned independently in their own repo.

| Asset | Location |
|-------|----------|
| Recipe source (yaml, scripts, Dockerfile) | Separate git repo |
| Built Docker images | Docker registry (pinned by digest) |
| Model weights | S3 or HuggingFace (checksummed) |
| base.wav samples | With recipe (Git LFS or S3) |

## Structure

```
voice-recipe/
├── recipe.yaml              ← Metadata + instructions
├── base.wav                 ← Reference voice sample (if needed)
├── model/                   ← Model weights (or pointer)
│   ├── model.bin
│   └── config.json
├── engine/
│   ├── capabilities.yaml    ← Engine capabilities (prosody, emotion, pauses, emphasis)
│   ├── markup-examples.md   ← Examples of annotation markup for this engine
│   └── annotation-prompt.j2 ← LLM prompt template for annotation
├── styles/
│   ├── neutral.j2           ← Performance style prompt: neutral narration
│   ├── childrens-story.j2   ← Performance style prompt: children's story
│   ├── noir-mystery.j2      ← Performance style prompt: noir mystery
│   └── action-thriller.j2   ← Performance style prompt: action thriller
├── mastering-chain.yaml     ← DSP chain configuration
├── packaging.yaml           ← M4B/MP3 packaging configuration
├── Dockerfile               ← Exact runtime environment (TTS + LLM + FFmpeg + tools)
└── checksums.sha256         ← Verify integrity
```

## Recipe YAML

```yaml
name: "narrator-deep-male-v2"
version: "2.1.0"
stack: "chatterbox"          # TTS engine: chatterbox, higgs-v3, piper, etc.

# Hardware requirements
min_hardware:
  gpu: true
  vram_gb: 8
  ram_gb: 16
  disk_gb: 20

# Model source
model:
  source: "huggingface"
  repo: "myorg/deep-male-voice"
  revision: "abc123def"
  files:
    - "model.bin"
    - "config.json"
  checksum: "sha256:abc123..."

# Engine capabilities and annotation
engine:
  capabilities: "engine/capabilities.yaml"
  markup_examples: "engine/markup-examples.md"
  annotation_prompt: "engine/annotation-prompt.j2"
  chunk_size: "paragraph"    # paragraph, or sentence_count with value

# Performance styles
styles:
  - name: "neutral"
    description: "Even delivery, neutral tone, conversational register"
    prompt: "styles/neutral.j2"
    
  - name: "childrens-story"
    description: "Bright, energetic, playful tone, light mood"
    prompt: "styles/childrens-story.j2"
    
  - name: "noir-mystery"
    description: "Slow, gravelly, ominous tone, dark mood, elevated register"
    prompt: "styles/noir-mystery.j2"
    
  - name: "action-thriller"
    description: "Fast-paced, intense, urgent tone, high energy"
    prompt: "styles/action-thriller.j2"

# TTS parameters
tts:
  sample_rate: 44100
  bitrate: "192k"
  format: "mp3"
  chunk_size: 300           # Characters per TTS call
  overlap_ms: 200           # Crossfade between chunks

# Mastering chain (applied to TTS output before packaging)
mastering:
  config: "mastering-chain.yaml"
  target_rms_db: -20.0      # ACX target: -23 to -18 dB
  max_peak_db: -3.5         # True peak ceiling (safety margin below -3 dB)
  max_noise_floor_db: -60   # ACX requirement: ≤ -60 dB RMS
  room_tone_ms:
    head: 1000              # 1–5 seconds of room tone at chapter start
    tail: 3000              # 1–5 seconds of room tone at chapter end

# Packaging (final audiobook assembly)
packaging:
  config: "packaging.yaml"
  formats:
    - type: "m4b"           # For direct playback (Apple Music, VLC, Google)
      codec: "aac"
      bitrate: "96k"
      chapters:
        style: "both"       # quicktime (Apple Books), nero (VLC/Android), or both
        read_header: true   # Read chapter title aloud at start of each chapter
    - type: "mp3"           # For platform submission (Spotify, ACX)
      codec: "mp3"
      bitrate: "192k"
      cbr: true
  metadata:
    template: "metadata-template.j2"  # Jinja2 template for metadata
  cover_art:
    required: true
    min_size: 600           # Minimum square pixels
    max_size: 1400
    format: "jpeg"

# Quality control
qc:
  reference_hash: "sha256:abc123..."
  test_sentence: "The quick brown fox jumps over the lazy dog."
  max_distortion: 0.05
  acx_compliant: true       # Run full ACX spec check before accepting

# Docker image (pre-built, versioned)
image:
  registry: "registry.example.com"
  repo: "audiobook/voice-deep-male-v2"
  tag: "2.1.0"
  digest: "sha256:def456..."
```

## Field Definitions

### Top Level

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Human-readable voice name |
| `version` | string | Yes | Semantic version |
| `stack` | string | Yes | TTS engine identifier |

### min_hardware

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `gpu` | bool | Yes | Requires GPU |
| `vram_gb` | int | Yes | Minimum GPU VRAM |
| `ram_gb` | int | Yes | Minimum system RAM |
| `disk_gb` | int | Yes | Minimum disk space |

### model

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `source` | string | Yes | `huggingface`, `s3`, `url` |
| `repo` | string | Yes | Repository/URL |
| `revision` | string | No | Specific revision/commit |
| `files` | array | Yes | Files to download |
| `checksum` | string | Yes | SHA-256 of model files |

### engine

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `capabilities` | string | Yes | Path to engine capabilities YAML |
| `markup_examples` | string | Yes | Path to markup examples document |
| `annotation_prompt` | string | Yes | Path to LLM annotation prompt template |
| `chunk_size` | string | Yes | Chunking strategy: `paragraph` or `sentence_count` |

### styles

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Style identifier (e.g., `neutral`, `noir-mystery`) |
| `description` | string | Yes | Human-readable description |
| `prompt` | string | Yes | Path to performance style prompt template |

### tts

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `sample_rate` | int | Yes | Audio sample rate (Hz) |
| `bitrate` | string | Yes | Audio bitrate |
| `format` | string | Yes | Output format: `mp3`, `wav`, `m4a` |
| `chunk_size` | int | Yes | Characters per TTS call |
| `overlap_ms` | int | No | Crossfade between chunks |

### mastering

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `config` | string | Yes | Path to mastering chain YAML (DSP parameters) |
| `target_rms_db` | float | Yes | Target RMS loudness in dB (ACX: -23 to -18) |
| `max_peak_db` | float | Yes | True peak ceiling in dB (ACX: ≤ -3) |
| `max_noise_floor_db` | float | Yes | Maximum noise floor in dB RMS (ACX: ≤ -60) |
| `room_tone_ms.head` | int | Yes | Room tone duration at chapter start (ms) |
| `room_tone_ms.tail` | int | Yes | Room tone duration at chapter end (ms) |

### packaging

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `config` | string | Yes | Path to packaging YAML (container parameters) |
| `formats` | array | Yes | List of output formats (m4b, mp3) |
| `formats[].type` | string | Yes | Format type: `m4b`, `mp3` |
| `formats[].codec` | string | Yes | Audio codec: `aac`, `mp3` |
| `formats[].bitrate` | string | Yes | Codec bitrate |
| `formats[].cbr` | bool | No | Constant bit rate (for MP3) |
| `formats[].chapters.style` | string | No | Chapter marker style: `quicktime`, `nero`, `both` |
| `formats[].chapters.read_header` | bool | No | Read chapter title aloud at start |
| `metadata.template` | string | Yes | Jinja2 template for metadata |
| `cover_art.required` | bool | Yes | Whether cover art is mandatory |
| `cover_art.min_size` | int | No | Minimum cover art size in pixels |
| `cover_art.max_size` | int | No | Maximum cover art size in pixels |
| `cover_art.format` | string | No | Cover art format: `jpeg`, `png` |

### image

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `registry` | string | Yes | Docker registry URL |
| `repo` | string | Yes | Image repository |
| `tag` | string | Yes | Image tag |
| `digest` | string | Yes | Image digest (pinned) |

## Worker Execution Flow

The TTS worker runs the full pipeline inside the recipe's Docker container: LLM annotation → TTS generation → mastering → packaging.

```python
def process_with_recipe(job, recipe):
    # 1. Pull the exact Docker image (pinned by digest)
    docker.pull(f"{recipe['image']['registry']}/{recipe['image']['repo']}@{recipe['image']['digest']}")
    
    # 2. Verify model checksum
    download_model(recipe['model'], '/models/')
    verify_checksum('/models/', recipe['model']['checksum'])
    
    # 3. Run the container
    #    - Mounts the book package + cover art
    #    - Container has everything: TTS model, LLM, engine docs, FFmpeg, mastering tools
    #    - Runs: LLM annotation → TTS generation → mastering → packaging
    docker.run(
        image=f"{recipe['image']['registry']}/{recipe['image']['repo']}@{recipe['image']['digest']}",
        volumes={
            '/jobs/input': {'bind': '/input', 'mode': 'ro'},
            '/jobs/output': {'bind': '/output', 'mode': 'rw'}
        },
        environment={
            'RECIPE_VERSION': recipe['version'],
            'BASE_WAV': '/input/base.wav',
            'COVER_ART': '/input/cover.jpg',
            'PERFORMANCE_STYLE': job['performance_style']
        },
        devices=['/dev/nvidia0'] if recipe['min_hardware']['gpu'] else []
    )
    
    # 4. Quality check (ACX compliance)
    verify_qc('/jobs/output/', recipe['qc'])
    
    # 5. Report success
```

### Internal Container Flow

```
1. LLM Annotation (GPU/CPU, LLM)
   - Load clean script from book package
   - Split into chapters (parallel) → chunks (serial within chapter)
   - For each chapter (parallel across chapters):
     - For each chunk (serial):
       - Assemble prompt: engine capabilities + markup examples + style prompt + previous chunk state + chunk text
       - LLM produces engine-specific markup
       - Validate chunk text against source (exact match required)
       - Capture ending state for next chunk
   - Chapters are emotional reset points (state resets at chapter boundaries)
   - Output: annotated script (engine-specific markup per chunk)

2. TTS Generation (GPU)
   - Load annotated script
   - Generate audio per chunk using the TTS engine
   - Assemble chunks into chapters
   - Chapters run in parallel; chunks within a chapter run serially

3. Mastering (CPU, FFmpeg/DSP)
   - Apply mastering chain (HPF, EQ, compression, de-esser, noise reduction)
   - True-peak limiting to recipe ceiling
   - Normalize to target RMS
   - Insert room tone at chapter boundaries
   - Measure and verify: RMS, peaks, noise floor
   - Reject chapters that fail QC

4. Packaging (CPU, FFmpeg/MP4 tools)
   - Encode chapters to final codecs (AAC for M4B, MP3 for submission)
   - Assemble M4B container with:
     - QuickTime chapter track (Apple Books)
     - Nero chpl atom (VLC/Android)
     - Metadata (title, author, narrator, genre, description)
     - Cover art (embedded JPEG)
     - stik=2 (audiobook flag)
     - M4B ftyp brand
   - Assemble MP3 chapter files (192 kbps CBR, 44.1 kHz)
   - Generate opening/closing credits
   - Generate retail sample (1–5 min)
   - Validate final package (ffprobe, chapter count, metadata)
```

## Consistency Guarantees

1. **Pinned Docker image** — Same OS, libraries, TTS harness, LLM, FFmpeg version
2. **Pinned model weights** — Checksum verified, no drift
3. **Deterministic preprocessing** — Script decoration is code, not manual
4. **Fixed TTS parameters** — Sample rate, bitrate, chunk size all in recipe
5. **Fixed mastering chain** — DSP parameters in recipe, not worker-dependent
6. **Fixed packaging** — Format, codec, chapter style, metadata template all in recipe
7. **Quality check** — Worker verifies output against ACX spec before accepting

**Any worker, anywhere, with sufficient hardware, produces byte-identical output** for the same input + recipe.

## Versioning

- Recipes use semantic versioning (MAJOR.MINOR.PATCH)
- MAJOR: Incompatible voice change (different voice actor, different style)
- MINOR: Improvement (better model, fixed artifacts, new performance style)
- PATCH: Bug fix (no audible change)

## MVP Note

For the MVP/POC, voice selection is **hardcoded** in the coordinator. The voice recipe system is designed but not yet implemented as a customer-facing feature. The POC uses a single voice recipe for all conversions.
