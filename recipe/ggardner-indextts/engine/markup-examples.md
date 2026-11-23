# IndexTTS-2.5 emotion markup examples

This engine has **no inline narrative tags** — the examples below are
the engine's own emotion-channel demonstrations (from the model card
and the Technical Report's emotion-weight tables), shown so the
annotator understands what its direction vocabulary achieves.

## Emotion vector control (the 8-dim vocabulary)

Ordered [happy, angry, sad, afraid, disgusted, melancholic, surprised, calm]; each value in [0, 1.2]; emo_alpha grades the strength.

```json
{ "emo_vector": [0, 0, 0.8, 0, 0, 0, 0, 0], "emo_alpha": 0.8 }
```

The report demonstrates graded intensity — the same sentence at Calm 0.5, 0.8, and 1.0 is audibly more expressive as the weight rises.

## Emotion text control (what this recipe's directions flow into)

```json
{ "use_emo_text": true, "emo_text": "somber, reflective, quietly resolute", "emo_alpha": 0.8 }
```

The bundled QwenEmotion model translates the description into the
8-dim vector. Short phrases over the controlled vocabulary work best:
"somber", "calm and steady", "melancholic, faintly hopeful",
"surprised, then wry".

## Disentanglement (report §4)

Timbre comes from the speaker reference; emotion comes from the channel
above — they can be different speakers, even different languages. In
this recipe: Gardner's clip carries the voice; the annotation's
per-segment directions carry the feeling.
