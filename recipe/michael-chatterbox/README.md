# Michael Chatterbox Voice Recipe

Voice recipe that converts a book package into an ACX-compliant audiobook using the Chatterbox TTS engine with a Michael voice clone.

## Voice

Clones **Michael** from `voices/Michael.wav` (24 kHz reference sample). The voice file is copied into the Docker image at build time.

## Build

```bash
docker build -f deploy/recipe-michael.Dockerfile -t audiobook-recipe-michael:local .
```

The image uses `python:3.11-slim` with CPU torch by default. For GPU builds, swap the base to an NVIDIA CUDA image and install torch from the CUDA index (see the comment in the Dockerfile).

## First run: Hugging Face download

On first TTS call, `ChatterboxTTS.from_pretrained()` downloads the model (~2 GB) from Hugging Face (`ResembleAI/chatterbox`). Subsequent runs use the local cache. Ensure the container has network access on first run, or pre-warm the HF cache.

## LLM annotation (optional)

Set these environment variables to enable LLM-based performance annotation before TTS:

- `LLM_BASE_URL`: OpenAI-compatible endpoint (e.g. `http://llm:8080/v1`)
- `LLM_API_KEY`: bearer token (optional)
- `LLM_MODEL`: model name
- `LLM_TIMEOUT_SECONDS`: request timeout (default 120)

If unset, the recipe uses clean text without annotation.

## Inputs (`/input`)

- `script.json`: narrator script (required)
- `metadata.json`: book metadata (optional)
- `cover.jpg`: cover art (optional)

## Outputs (`/output`)

- `m4b/audiobook.m4b`: full audiobook (M4B, stik=2, chapters)
- `mp3/chapter-NNN.mp3`: per-chapter MP3s
- `mp3/opening-credits.mp3`, `mp3/closing-credits.mp3`
- `mp3/retail-sample.mp3`: ~75 s sample
- `metadata.json`: output metadata
- `qc-report.json`: QC report

## File layout

```
recipe/michael-chatterbox/
├── run.py               ← Entry point (annotation → TTS → mastering → packaging)
├── recipe.yaml          ← Recipe metadata and capabilities
├── mastering-chain.yaml ← Audio mastering chain
├── packaging.yaml       ← Packaging configuration
├── engine/
│   ├── capabilities.yaml← Engine capabilities
│   ├── annotation-prompt.j2 ← LLM annotation prompt template
│   └── markup-examples.md   ← Markup examples for the LLM
├── styles/
│   ├── neutral.j2       ← Neutral performance style
│   ├── noir-mystery.j2  ← Noir mystery style
│   └── childrens-story.j2← Children's story style
├── voices/
│   └── Michael.wav      ← Voice reference sample
└── tests/
    └── test_recipe_helpers.py
```

## Tests

```bash
cd recipe/michael-chatterbox
python -m pytest tests/ -q
```

Tests cover pure helpers (`chunk_text`, `validate_annotation`, etc.) and do not require torch or chatterbox to be installed.
