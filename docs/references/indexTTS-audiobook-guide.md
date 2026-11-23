IndexTTS-2.5 + emo_text Mode for Audiobook Narration
Local GPU Server Setup & Usage Guide (Agent-Ready)
Overview
Use IndexTTS-2.5 (recommended) for high-quality zero-shot TTS with text-based emotion control. The `use_emo_text` mode (with optional explicit `emo_text`) lets you:
Automatically derive emotion from the manuscript text, or
Explicitly direct delivery independently of the words ("quiet and hollow", "urgent whisper", "triumphant", etc.).
Emotion is disentangled from speaker identity, so one clean narrator reference works for the whole book while you vary performance per scene. Target: NVIDIA GPU with ≥6–8 GB VRAM, CUDA Toolkit 12.8+.
1. Installation
```bash
git clone https://github.com/index-tts/index-tts.git && cd index-tts
pip install -U uv
uv sync --all-extras
# Optional China mirror if slow:
# uv sync --all-extras --default-index "https://mirrors.aliyun.com/pypi/simple"

uv tool install "huggingface-hub"
hf download IndexTeam/IndexTTS-2.5 --local-dir=checkpoints
# If HF is slow: export HF_ENDPOINT="https://hf-mirror.com"

uv run tools/gpu_check.py
```
2. WebUI (for testing)
```bash
uv run webui.py
```
Open http://127.0.0.1:7860. Enable BF16 for lower VRAM. Test voices and emotion controls here first.
3. Python Inference – Core emo_text Usage
Initialize once (IndexTTS-2.5):
```python
from indextts.infer_v2_5 import IndexTTS2

tts = IndexTTS2(
    cfg_path="checkpoints/config.yaml",
    model_dir="checkpoints",
    use_bf16=True,
    use_qwen_emo=True   # required for text emotion
)
```
Automatic emotion from text (good default for narration):
```python
tts.infer(
    spk_audio_prompt="voices/narrator.wav",   # 5–15s clean reference
    text="She realized, with a cold certainty, that she was completely alone.",
    lang="EN",
    output_path="scene_auto.wav",
    use_emo_text=True,
    emo_alpha=0.55,        # 0.5–0.6 recommended
    use_random=False
)
```
Explicit emotional direction (best for deliberate scenes):
```python
tts.infer(
    spk_audio_prompt="voices/narrator.wav",
    text="She realized, with a cold certainty, that she was completely alone.",
    emo_text="quiet, hollow, and deeply sorrowful",
    lang="EN",
    output_path="scene_directed.wav",
    use_emo_text=True,
    emo_alpha=0.5,
    use_random=False
)
```
Pacing control:
```python
# Slower: duration_factor=1.25
# Faster: duration_factor=0.8
tts.infer(..., duration_factor=1.15)
```
Other options (reference only):
Separate emotion audio: `emo_audio_prompt="voices/sad_example.wav", emo_alpha=0.7`
Direct vector: `emo_vector=[0, 0, 0.7, 0, 0, 0.2, 0, 0]`  
Order: [happy, angry, sad, afraid, disgusted, melancholic, surprised, calm]
4. Recommended Audiobook Workflow
Prepare one clean 5–15s narrator reference (same acoustic conditions preferred). Optional separate character voices.
Chunk the book into paragraphs or short scenes (100–400 words). Model auto-segments long text but prosody does not cross boundaries cleanly.
Emotion strategy:
Default: `use_emo_text=True`, `emo_alpha=0.5–0.6` (auto from text).
Key scenes / dialogue / peaks: supply explicit `emo_text`.
Keep the same `spk_audio_prompt` and stable parameters for consistency.
Batch script skeleton:
```python
import os
from indextts.infer_v2_5 import IndexTTS2

tts = IndexTTS2(
    cfg_path="checkpoints/config.yaml",
    model_dir="checkpoints",
    use_bf16=True,
    use_qwen_emo=True
)

narrator = "voices/narrator.wav"
output_dir = "audiobook_output"
os.makedirs(output_dir, exist_ok=True)

scenes = [
    {
        "id": "ch01_p01",
        "text": "The rain had not stopped for three days...",
        "emo_text": "calm, slightly weary narrative",  # or None/omit for auto
        "alpha": 0.5,
        "speed": 1.0
    },
    # add more scenes...
]

for s in scenes:
    out = os.path.join(output_dir, f"{s['id']}.wav")
    tts.infer(
        spk_audio_prompt=narrator,
        text=s["text"],
        emo_text=s.get("emo_text"),
        lang="EN",
        output_path=out,
        use_emo_text=True,
        emo_alpha=s.get("alpha", 0.55),
        duration_factor=s.get("speed", 1.0),
        use_random=False
    )
    print(f"Done: {out}")
```
Post-process: concatenate WAVs, normalize loudness (-23 to -18 LUFS), add room tone / chapter markers (ffmpeg, sox, or Audacity).
5. Tips & Limitations for Long-Form Audiobooks
emo_alpha: Start at 0.5–0.6. Higher values often sound exaggerated on long passages.
Long text is auto-segmented with short silences. Prosody does not carry across segment boundaries — keep chunks coherent.
Consistency: same reference voice + stable parameters across chapters. Re-test periodically.
VRAM: Prefer BF16. Unload model between very large jobs if needed.
Quality is excellent for expressive narration and drafts/indie use, but still benefits from light human review on critical scenes.
Languages: IndexTTS-2.5 supports EN, ZH, JA, ES, AR (set lang=).
6. Quick CLI (after pip install -e .)
```bash
indextts "Your text here" --voice voices/narrator.wav --model_dir checkpoints --config checkpoints/config.yaml --output out.wav
```
(Emotion controls are more complete in the Python API.)
Sources: Official IndexTTS repo (index-tts/index-tts) and IndexTTS-2.5 Hugging Face model card.  
Start with the WebUI + a few test scenes, then move to the batch Python workflow.