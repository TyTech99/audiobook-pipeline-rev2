# QwenEmotion Safe-Word Mapping (empirically verified)

Probed 2026-11-23 (164 candidates) against IndexTTS-2.5's bundled
QwenEmotion 0.6B (qwen0.6bemo4-merge, model revision c39ce5ba).
Each candidate was fed to `qwen_emo.inference(text)` and the resulting
8-dim vector recorded. A word is "safe" for a dimension if it maps with
high/medium confidence and the primary dimension matches the intent.

## Safe words per dimension (English)

- **happy**: happy, joyful, cheerful, elated, delighted, triumphant, bright, hopeful, upbeat, exuberant, radiant, gleeful, jubilant, pleased, optimistic, lighthearted
- **angry**: angry, furious, irate, enraged, hostile, harsh, sharp, indignant, resentful, bitter, irritated, annoyed, outraged, vehement, aggressive, combative
- **sad**: sad, sorrowful, grief, mournful, heartbroken, dejected, forlorn, despondent, wistful, lamenting, grieving, sorrow, woeful, doleful, plaintive
- **afraid**: afraid, fearful, terrified, alarmed, apprehensive, dread, panicked, frightened, scared
- **disgusted**: disgusted, repulsed, nauseated, appalled
- **melancholic**: melancholic, gloomy, depressed
- **surprised**: surprised, startled, astonished, shocked, amazed, bewildered, stunned, flabbergasted
- **calm**: calm, peaceful, serene, tranquil, quiet, steady, gentle, soft, still, composed, placid, unhurried

## Safe words per dimension (Chinese — more reliable than English)

Chinese emotion words map more reliably because QwenEmotion was trained
on Chinese emotion classification. Use these when the annotation LLM
can generate Chinese directions.

- **happy (高兴)**: 高兴, 快乐, 喜悦, 欢快
- **angry (愤怒)**: 愤怒, 生气, 暴怒, 恼怒
- **sad (悲伤)**: 悲伤, 忧伤, 哀伤, 痛苦
- **afraid (恐惧)**: 恐惧, 害怕, 惊恐
- **disgusted (反感)**: 反感, 厌恶, 恶心
- **melancholic (低落)**: 低落, 低沉
- **surprised (惊讶)**: 惊讶, 震惊, 诧异
- **calm (自然)**: 自然, 平静, 安宁

## Words that do NOT map as intended (do not use)

| Word | Intended | Actual |
|---|---|---|
| anxious | afraid | melancholic |
| nervous | afraid | calm |
| uneasy | afraid | sad |
| worried | afraid | sad |
| 担忧 (worried) | afraid | sad |
| tense | afraid | calm |
| urgent | afraid | calm |
| urgent whisper | afraid | calm |
| desperate | afraid | sad |
| revolted | disgusted | angry |
| horrified | disgusted | sad |
| sickened | disgusted | sad |
| weary | melancholic | sad |
| somber | melancholic | sad |
| resigned | melancholic | sad |
| hollow | melancholic | calm |
| contemplative | melancholic | calm |
| nostalgic | melancholic | sad |
| solemn | melancholic | calm |
| reflective | melancholic | calm |
| pensive | melancholic | sad |
| wistful | melancholic | sad |
| brooding | melancholic | sad |
| mournful | melancholic | sad |
| melancholy | sad | melancholic |
| 忧郁 | melancholic | sad |
| 消沉 | melancholic | sad |
| cold | melancholic | sad |
| dark | melancholic | sad |
| heavy | melancholic | sad |
| warm | calm | happy |
| relaxed | calm | happy |
| soothing | calm | happy |
| warm and gentle | calm | happy |
| light | happy | calm |
| content | happy | sad |
| quiet and hollow | melancholic | sad |
| calm, weary, resigned | melancholic | sad |
| still, heavy with unspoken grief | melancholic | sad |
| dark and somber | melancholic | sad |

## Key findings

1. **Chinese words are more reliable than English synonyms.** QwenEmotion was trained on Chinese emotion classification. Chinese emotion words map with higher confidence than their English equivalents. The annotation LLM could generate Chinese emotion descriptors for more reliable mapping.

2. **Melancholic is the hardest dimension in both languages.** Only "melancholic", "gloomy", "depressed" (English) and "低落", "低沉" (Chinese) map to it. Most melancholic-sounding words (weary, somber, resigned, hollow, contemplative, nostalgic, solemn, reflective, 忧郁, 消沉) map to sad or calm. The melancholic_words workaround only handles the sad/melancholic swap for specific trigger words.

3. **Afraid is also hard.** "Tense", "urgent", "desperate", "anxious", "nervous", "worried" all map to calm or sad, not afraid. Only "afraid", "fearful", "terrified", "alarmed" (and Chinese 害怕, 恐惧, 惊恐) work.

4. **Disgusted is very narrow.** Only "disgusted", "repulsed", "nauseated" (and Chinese 厌恶, 反感, 恶心). "Revolted" and "horrified" map to angry and sad.

5. **Multi-word phrases are unreliable** unless they contain a safe word from the target dimension. "quiet, melancholic, steady" works because "melancholic" is in it.

6. **The guide's suggested phrases are partially unreliable.** "triumphant" works (happy). "quiet and hollow" maps to sad. "urgent whisper" maps to calm.

## Practical rule for annotation directions

Compose directions from the safe-word lists above. For melancholic passages, use "melancholic" explicitly — synonyms won't survive QwenEmotion. For afraid, use "afraid" or "fearful" — "tense" and "urgent" will be silently ignored. Consider having the annotation LLM generate Chinese emotion words for more reliable mapping, since QwenEmotion was trained on Chinese.
