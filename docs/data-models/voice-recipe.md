# Voice Recipe

A voice recipe is a **self-contained bundle** that defines everything needed to produce audio for a specific voice. It is deployable to any worker meeting the minimum hardware requirements and produces consistent, reproducible audio.

## Purpose

- Ensure any worker produces identical audio for the same input + recipe
- Encapsulate voice-specific parameters (model, harness, preprocessing)
- Enable versioning and rollback
- Support multiple TTS stacks (Chatterbox, Higgs V3, Piper, etc.)

## Structure

```
voice-recipe/
├── recipe.yaml          ← Metadata + instructions
├── base.wav             ← Reference voice sample (if needed)
├── model/               ← Model weights (or pointer)
│   ├── model.bin
│   └── config.json
├── script-template.j2   ← Jinja2 template for script decoration
├── preprocess.py        ← Stack-specific script processing
├── Dockerfile           ← Exact runtime environment
└── checksums.sha256     ← Verify integrity
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

# Script processing
script:
  template: "script-template.j2"
  preprocessor: "preprocess.py"
  narrator_cues:
    - cue: "[pause]"
      replacement: "<break time='500ms'/>"
    - cue: "[whisper]"
      replacement: "<speak rate='slow' volume='soft'>"
    - cue: "[emphasis]"
      replacement: "<prosody emphasis='strong'>"

# TTS parameters
tts:
  sample_rate: 44100
  bitrate: "192k"
  format: "mp3"
  chunk_size: 300           # Characters per TTS call
  overlap_ms: 200           # Crossfade between chunks

# Quality control
qc:
  reference_hash: "sha256:abc123..."
  test_sentence: "The quick brown fox jumps over the lazy dog."
  max_distortion: 0.05

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

### tts

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `sample_rate` | int | Yes | Audio sample rate (Hz) |
| `bitrate` | string | Yes | Audio bitrate |
| `format` | string | Yes | Output format: `mp3`, `wav`, `m4a` |
| `chunk_size` | int | Yes | Characters per TTS call |
| `overlap_ms` | int | No | Crossfade between chunks |

### image

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `registry` | string | Yes | Docker registry URL |
| `repo` | string | Yes | Image repository |
| `tag` | string | Yes | Image tag |
| `digest` | string | Yes | Image digest (pinned) |

## Worker Execution Flow

```python
def process_with_recipe(job, recipe):
    # 1. Pull the exact Docker image (pinned by digest)
    docker.pull(f"{recipe['image']['registry']}/{recipe['image']['repo']}@{recipe['image']['digest']}")
    
    # 2. Verify model checksum
    download_model(recipe['model'], '/models/')
    verify_checksum('/models/', recipe['model']['checksum'])
    
    # 3. Run the container
    #    - Mounts the book package
    #    - Container has everything: model, harness, preprocessing
    #    - Produces audio chunks
    docker.run(
        image=f"{recipe['image']['registry']}/{recipe['image']['repo']}@{recipe['image']['digest']}",
        volumes={
            '/jobs/input': {'bind': '/input', 'mode': 'ro'},
            '/jobs/output': {'bind': '/output', 'mode': 'rw'}
        },
        environment={
            'RECIPE_VERSION': recipe['version'],
            'BASE_WAV': '/input/base.wav'
        },
        devices=['/dev/nvidia0'] if recipe['min_hardware']['gpu'] else []
    )
    
    # 4. Quality check
    verify_qc('/jobs/output/', recipe['qc'])
    
    # 5. Report success
```

## Consistency Guarantees

1. **Pinned Docker image** — Same OS, libraries, TTS harness version
2. **Pinned model weights** — Checksum verified, no drift
3. **Deterministic preprocessing** — Script decoration is code, not manual
4. **Fixed TTS parameters** — Sample rate, bitrate, chunk size all in recipe
5. **Quality check** — Worker verifies output against reference before accepting

**Any worker, anywhere, with sufficient hardware, produces byte-identical audio** for the same input + recipe.

## Versioning

- Recipes use semantic versioning (MAJOR.MINOR.PATCH)
- MAJOR: Incompatible voice change (different voice actor, different style)
- MINOR: Improvement (better model, fixed artifacts)
- PATCH: Bug fix (no audible change)

## Storage

| Asset | Location |
|-------|----------|
| Recipe source (yaml, scripts, Dockerfile) | Git repository |
| Built Docker images | Docker registry (pinned by digest) |
| Model weights | S3 or HuggingFace (checksummed) |
| base.wav samples | With recipe (Git LFS or S3) |

## MVP Note

For the MVP/POC, voice selection is **hardcoded** in the coordinator. The voice recipe system is designed but not yet implemented as a customer-facing feature. The POC uses a single voice recipe for all conversions.
