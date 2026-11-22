# Michael CosyVoice Voice Recipe

Voice recipe that converts a book package into an ACX-compliant audiobook
using CosyVoice 3 (`FunAudioLLM/Fun-CosyVoice3-0.5B-2512`, Apache-2.0) with
a Michael voice clone. Replaces the retired Chatterbox recipe (#88).

## Engine

CosyVoice 3 — Qwen2-LLM-based TTS, zero-shot voice cloning, natural-language
instruct channel. Synthesis mode is `inference_instruct2` (transcript-free
cloning): the reference clip `voices/Michael.wav` supplies the timbre, and
the style templates (`styles/*.j2`) are the instruct text, wrapped as
`You are a helpful assistant. <direction><|endofprompt|>`.

- Weights: `FunAudioLLM/Fun-CosyVoice3-0.5B-2512` pinned at revision
  `29e01c4e8d000f4bcd70751be16fa94bf3d85a18` (~5.4 GB).
- Engine code: `QwenAudio/CosyVoice` pinned at `074ca6dc` (with its
  Matcha-TTS submodule), built into the image.
- torch 2.7.0+cu128 — upstream's own requirements pin torch 2.3.1+cu121,
  which has no kernels for RTX 50-series (Blackwell, sm_120); this node's
  GPU 1 is an RTX 5060 Ti.
- Determinism: one fixed seed (default 1234, `RECIPE_SEED`), re-applied
  before every inference call so the take is reproducible and the
  narrator never rotates mid-book.

On first TTS call the recipe downloads the pinned weights from Hugging
Face via `snapshot_download` (network required once, or pre-warm
`HF_HOME`/`COSYVOICE_MODEL_DIR`). Subsequent runs reuse the cache.

## Known limits

- No text normalizer in the container: upstream's wetext downloads its
  data from ModelScope at model load, which breaks self-contained runs,
  so it is dropped. Numbers, dates and symbols are read as written —
  the prep stage's text normalization owns them.
- The MVP synthesis path feeds plain text: LLM annotation hints
  (`[pause]`, `[emphasis]`, ...) are stripped before TTS. CosyVoice's
  native markup (`[breath]`, `[laughter]`, instruct control) is
  documented in `engine/markup-examples.md` and lands when the
  annotation layer starts emitting engine markup.

## Layout

```
recipe/michael-cosyvoice/
├── recipe.yaml              ← Recipe manifest (model pin, styles, packaging)
├── run.py                   ← Entry point (annotation → TTS → mastering → packaging)
├── requirements.txt         ← Pinned engine dependencies (torch installed in the image)
├── voices/
│   └── Michael.wav          ← Reference voice clip (7.65 s)
├── engine/
│   ├── capabilities.yaml    ← CosyVoice 3 capabilities (instruct, tags, cloning)
│   ├── markup-examples.md   ← Engine markup reference
│   └── annotation-prompt.j2 ← LLM annotation prompt (punctuation-only, MVP)
├── styles/                  ← Performance styles; doubles as the instruct text
├── mastering-chain.yaml     ← ACX loudness/peak targets
├── packaging.yaml           ← M4B/MP3 packaging configuration
└── tests/                   ← Pure-helper unit tests (no torch required)
```

## Build

```bash
docker build -f deploy/recipe-michael-cosyvoice.Dockerfile \
    -t audiobook-recipe-michael-cosyvoice:local .
```

## Tests

```bash
cd recipe/michael-cosyvoice
PYTHONPATH=. python3 -m pytest tests/
```

Tests cover pure helpers (`chunk_text`, `validate_annotation`,
`strip_performance_hints`, `style_instruct`, ...) and do not require
torch or cosyvoice to be installed.
