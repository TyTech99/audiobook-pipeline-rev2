# TTS Worker

GPU worker that consumes book packages and produces audiobooks using voice recipes.

## Implementation Status

**Not yet implemented.** See design docs:

- [Architecture Overview](../../docs/architecture/overview.md)
- [Voice Recipe](../../docs/data-models/voice-recipe.md)

## Planned Structure

```
worker/tts/
├── worker.py            ← Main worker loop (Redis BRPOP)
├── config.py            ← Configuration loading
├── tts/
│   ├── base.py          ← TTS engine interface
│   ├── chatterbox.py    ← Chatterbox implementation
│   └── higgs.py         ← Higgs V3 implementation
├── recipe/
│   ├── loader.py        ← Voice recipe loader
│   └── validator.py     ← Recipe validation
├── audio/
│   ├── chunker.py       ← Audio chunking
│   ├── assembler.py     ← Final audiobook assembly
│   └── qc.py            ← Quality control
└── requirements.txt
```

## Dependencies

- Python 3.11+
- PyTorch (CUDA)
- TTS engines (Chatterbox, Higgs V3)
- ffmpeg
- redis-py
- boto3 (S3)
- PyYAML

## GPU Requirements

- NVIDIA GPU with CUDA support
- Minimum 8GB VRAM (varies by voice recipe)
- NVIDIA Container Toolkit for Docker GPU passthrough
