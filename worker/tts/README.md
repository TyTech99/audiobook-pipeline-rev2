# TTS Worker

GPU worker that consumes book packages and produces finished audiobooks using voice recipes.

## Purpose

The TTS worker claims jobs from the Redis `convert` queue, downloads the book package, and runs a voice-recipe Docker container. All audio generation (LLM annotation, TTS, mastering, packaging) happens inside the recipe container. The worker validates the output and reports completion.

## Implementation status

Implemented (local-file mode, CosyVoice 3 michael recipe). The worker loop claims convert jobs and runs the recipe container. The worker Dockerfile is at `deploy/worker-tts.Dockerfile` and is built at node install. The recipe Dockerfile is at `deploy/recipe-michael-cosyvoice.Dockerfile`. The recipe container gets `<storage_root>/models` mounted at `/models` with `HF_HOME=/models/huggingface`, so engine weights download once instead of per job. TTS is GPU-or-fail (#103): the worker refuses to launch a recipe container without a GPU device request and never retries a GPU-launch failure on CPU.

## How it runs

The supervisor JIT-starts the TTS worker container when there is work on the `convert` queue. The container runs:

```bash
python -m worker.tts.worker
```

The worker reads `config/worker-tts.yaml` (path overridable via `TTS_WORKER_CONFIG`). Key config values:

- `redis.*`: Redis connection for the job queue
- `coordinator.url` and `coordinator.api_key`: for registration, heartbeats, and completion reports
- `storage.root`: local storage root (default `/var/lib/audiobook`)
- `default_recipe_image`: fallback recipe image (default `audiobook-recipe-michael-cosyvoice:local`)
- `llm.*`: OpenAI-compatible LLM settings forwarded into the recipe container

The TTS container mounts `/var/run/docker.sock` so it can launch recipe containers.

## Pipeline stages (inside the recipe container)

1. **LLM annotation**: annotates the script with engine-specific markup
2. **TTS generation**: generates audio per chunk
3. **Mastering**: applies the mastering chain (EQ, compression, limiting, normalization)
4. **Packaging**: assembles M4B and MP3 files with chapters, metadata, and cover art

## File layout

```
worker/tts/
├── worker.py            ← Main worker loop (Redis BRPOP, recipe container runner)
├── output_validator.py  ← Output package validation
└── tests/
    └── test_tts.py      ← Unit tests
```

## Dependencies

- Python 3.11+
- Docker SDK for Python (to run recipe containers)
- redis-py
- httpx
- PyYAML

## GPU requirements

- NVIDIA GPU with CUDA support
- Minimum 8GB VRAM (varies by voice recipe)
- NVIDIA Container Toolkit for Docker GPU passthrough

## See also

- [Voice Recipe](../../docs/data-models/voice-recipe.md)
- [Audiobook Package](../../docs/data-models/audiobook-package.md)
- [Michael CosyVoice Recipe](../../recipe/michael-cosyvoice/README.md)
