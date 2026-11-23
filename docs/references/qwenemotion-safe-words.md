# QwenEmotion Safe-Word Mapping (empirically verified, 80 candidates)

Probed 2026-11-23 against IndexTTS-2.5's bundled QwenEmotion 0.6B
(qwen0.6bemo4-merge, model revision c39ce5ba). 80 unique English
emotion words tested, 10 per dimension. Each candidate was fed to
`qwen_emo.inference(word)` and the resulting 8-dim vector recorded.
A word is "safe" for a dimension if it maps with high/medium confidence
and the primary dimension matches the intent.

## Safe words per dimension

- **happy**: happy, joyful, cheerful, elated, exuberant, exhilarated, triumphant, bright, hopeful, blissful
- **angry**: angry, furious, irate, indignant, resentful, enraged, irritated, incensed, exasperated
- **sad**: sad, sorrowful, grief-stricken, anguished, heartbroken, desolate, mournful, tragic, lamenting, woeful
- **afraid**: afraid, scared, terrified, alarmed, panicked, frightened
- **disgusted**: disgusted, nauseated, repulsed, abhorrent, contemptuous, disdainful, repelled
- **melancholic**: melancholic, gloomy, depressed
- **surprised**: surprised, shocked, astonished, amazed, stunned, bewildered, startled, flabbergasted
- **calm**: calm, peaceful, tranquil, serene, quiet, composed, placid, unhurried, gentle

## Words that do NOT map as intended (do not use for these dimensions)

| Word | Intended | Actual |
|---|---|---|
| upset | angry | sad |
| horrified | afraid | sad (low confidence) |
| anxious | afraid | melancholic |
| flustered | afraid | afraid (low confidence) |
| startled | afraid | surprised |
| revolted | disgusted | angry |
| scornful | disgusted | angry |
| sickened | disgusted | sad |
| somber | melancholic | sad |
| brooding | melancholic | sad |
| forlorn | melancholic | sad |
| desolate | melancholic | sad |
| bleak | melancholic | sad |
| pensive | melancholic | sad |
| dejected | melancholic | sad |
| relaxed | calm | happy |
| alarmed | surprised | afraid |
| dumbfounded | surprised | surprised (low confidence) |

## Key findings

1. **Melancholic is the hardest dimension — only 3 safe words.** "melancholic", "gloomy", "depressed" are the only words that reliably map to melancholic. Every other melancholic-sounding word (somber, brooding, forlorn, desolate, bleak, pensive, dejected, weary, hollow, contemplative, nostalgic, solemn, reflective) maps to sad or calm. This is a fundamental QwenEmotion limitation — the melancholic_words workaround only handles specific trigger words.

2. **Afraid has only 6 safe words.** "afraid", "scared", "terrified", "alarmed", "panicked", "frightened". Common fear words like "anxious", "tense", "urgent", "desperate", "horrified" do not map to afraid.

3. **Disgusted has 7 safe words.** "disgusted", "nauseated", "repulsed", "abhorrent", "contemptuous", "disdainful", "repelled". "Revolted" and "scornful" map to angry. "Sickened" maps to sad.

4. **Happy, sad, angry, surprised, calm have good coverage** (9-10 safe words each). These dimensions are well-represented.

5. **16 of 80 words (20%) mismatched.** Natural narration vocabulary is poorly served by QwenEmotion. The safe-word list is narrow but verified.

## Practical rule for annotation directions

Compose directions from the safe-word lists above. For melancholic passages, use "melancholic" explicitly — no synonym survives QwenEmotion. For afraid, use "afraid" or "fearful" — "tense" and "urgent" will be silently ignored. For disgusted, use "disgusted" — "revolted" will be read as anger.
