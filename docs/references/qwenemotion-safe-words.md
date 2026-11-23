# QwenEmotion Safe-Word Mapping (empirically verified)

Probed 2026-11-23 against IndexTTS-2.5's bundled QwenEmotion 0.6B
(qwen0.6bemo4-merge, model revision c39ce5ba). Each candidate word was
fed to `qwen_emo.inference(text)` and the resulting 8-dim vector recorded.
A word is "safe" for a dimension if it maps with high confidence (one
dimension > 0.5, all others < 0.2) and matches the intended dimension.

## Safe words per dimension

These words reliably map to their dimension. Use them in annotation
directions for predictable emotion control.

- **happy**: happy, joyful, cheerful, elated, delighted, triumphant, bright, hopeful
- **angry**: angry, furious, irate, enraged, hostile, harsh, sharp
- **sad**: sad, sorrowful, grief, mournful, heartbroken
- **afraid**: afraid, fearful, terrified, alarmed
- **disgusted**: disgusted, repulsed, nauseated
- **melancholic**: melancholic, gloomy, depressed
- **surprised**: surprised, startled, astonished, shocked
- **calm**: calm, peaceful, serene, tranquil, quiet, steady, gentle, soft

## Words that do NOT map as intended (do not use for these dimensions)

These words were tested but QwenEmotion mapped them to a different
dimension. Using them will produce unintended emotion.

| Word/phrase | Intended | Actual | Note |
|---|---|---|---|
| anxious | afraid | melancholic | QwenEmotion reads anxiety as low mood |
| tense | afraid | calm | No fear signal detected |
| urgent | afraid | calm | No fear signal detected |
| urgent whisper | afraid | calm | No fear signal detected |
| desperate | afraid | sad | Reads as sorrow, not fear |
| weary | melancholic | sad | Reads as sorrow, not low mood |
| somber | melancholic | sad | Reads as sorrow |
| resigned | melancholic | sad | Reads as sorrow |
| hollow | melancholic | calm | No melancholic signal |
| contemplative | melancholic | calm | No melancholic signal |
| nostalgic | melancholic | sad | Reads as sorrow |
| solemn | melancholic | calm | No melancholic signal |
| reflective | melancholic | calm | No melancholic signal |
| cold | melancholic | sad | Reads as sorrow |
| dark | melancholic | sad | Reads as sorrow |
| heavy | melancholic | sad | Reads as sorrow |
| quiet and hollow | melancholic | sad | Phrase maps to sad, not melancholic |
| calm, weary, resigned | melancholic | sad | Phrase maps to sad |
| still, heavy with unspoken grief | melancholic | sad | Phrase maps to sad |
| warm | calm | happy | Reads as positive, not calm |
| light | happy | calm | Reads as neutral |
| revolted | disgusted | angry | Reads as anger |
| anxious | afraid | melancholic | (duplicate, listed for clarity) |

## Key findings

1. **Melancholic is the hardest dimension to hit.** Only "melancholic", "gloomy", "depressed", and the exact phrase "quiet, melancholic, steady" map to it. Most melancholic-sounding words (weary, somber, resigned, hollow, contemplative, nostalgic, solemn, reflective) map to "sad" or "calm" instead. The melancholic_words workaround in QwenEmotion only handles the sad/melancholic swap for specific trigger words.

2. **Afraid is also hard.** Only "afraid", "fearful", "terrified", "alarmed" work. "Tense", "urgent", "desperate" all map to calm or sad.

3. **Disgusted is very narrow.** Only "disgusted", "repulsed", "nauseated". "Revolted" maps to angry.

4. **Multi-word phrases are unreliable.** "quiet, melancholic, steady" works (because "melancholic" is in it), but "calm, weary, resigned" maps to sad (because "weary" and "resigned" override). Phrases should include at least one safe word from the target dimension.

5. **The 8 dimension names are the only guaranteed safe inputs.** Everything else is the model's best guess.

## Practical rule for annotation directions

Compose directions from the safe-word lists above. For melancholic passages, use the word "melancholic" explicitly — do not rely on synonyms. For afraid, use "afraid" or "fearful" — do not use "tense" or "urgent". For calm, "calm" or "quiet" or "steady" work reliably.

The guide's suggested phrases ("quiet and hollow", "urgent whisper", "triumphant") are partially unreliable: "triumphant" works (→ happy), but "quiet and hollow" → sad and "urgent whisper" → calm. Use them with caution or replace with safe words.
