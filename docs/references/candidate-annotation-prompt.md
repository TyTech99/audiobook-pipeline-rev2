# Candidate Annotation Prompt — Safe-Word Directions

This is a candidate prompt design for issue #122. It adds the verified
safe-word list to the annotation prompt so the LLM composes directions
from words that QwenEmotion reliably parses.

## Candidate capabilities.yaml change

Add `safe_direction_words` to the `annotation_contract.instruct_channel` block:

```yaml
annotation_contract:
  inline_tags: []
  instruct_channel:
    format: "{direction}"
    controls: [happy, angry, sad, afraid, disgusted, melancholic, surprised, calm]
    max_direction_words: 1
    safe_words:
      - happy: [happy, joyful, cheerful, elated, exuberant, exhilarated, triumphant, bright, hopeful, blissful]
      - angry: [angry, furious, irate, indignant, resentful, enraged, irritated, incensed, exasperated]
      - sad: [sad, sorrowful, grief-stricken, anguished, heartbroken, desolate, mournful, tragic, lamenting, woeful]
      - afraid: [afraid, scared, terrified, alarmed, panicked, frightened]
      - disgusted: [disgusted, nauseated, repulsed, abhorrent, contemptuous, disdainful, repelled]
      - melancholic: [melancholic, gloomy, depressed]
      - surprised: [surprised, shocked, astonished, amazed, stunned, bewildered, startled, flabbergasted]
      - calm: [calm, peaceful, tranquil, serene, quiet, composed, placid, unhurried, gentle]
    note: >
      Choose ONE word from the safe list for the segment's dominant emotion.
      Do not combine words from different dimensions. Do not use words
      outside the safe list — QwenEmotion cannot reliably parse them.
  punctuation_rules: []
  retries: 2
  direction_granularity: segment
```

## Candidate annotation-prompt.j2 change

Add a safe-word section to the prompt template, after the engine vocabulary:

```
## Safe delivery words (USE ONLY THESE)

The engine's emotion classifier (QwenEmotion) only reliably parses
these words. Choose ONE word from this list for your DIRECTION. Do not
use any word not on this list — it will be silently ignored.

happy: happy, joyful, cheerful, elated, exuberant, exhilarated, triumphant, bright, hopeful, blissful
angry: angry, furious, irate, indignant, resentful, enraged, irritated, incensed, exasperated
sad: sad, sorrowful, grief-stricken, anguished, heartbroken, desolate, mournful, tragic, lamenting, woeful
afraid: afraid, scared, terrified, alarmed, panicked, frightened
disgusted: disgusted, nauseated, repulsed, abhorrent, contemptuous, disdainful, repelled
melancholic: melancholic, gloomy, depressed
surprised: surprised, shocked, astonished, amazed, stunned, bewildered, startled, flabbergasted
calm: calm, peaceful, tranquil, serene, quiet, composed, placid, unhurried, gentle

Rules:
1. Choose exactly ONE word from the list above.
2. The word must match the segment's dominant emotional tone.
3. Do not combine words from different dimensions.
4. Do not use words outside the list.
```

## LLM parameters

- Model: Qwen3.8-27B-Uncensored-Heretic-MTP-UD-IQ4_XS (GPU0, port 8081)
- Temperature: 0.3 (hardcoded in annotate_exec.py _llm_chat)
- Top-p: not set (server default)
- Max tokens: server default (80k context)
- Timeout: 120s per call
- Retries: 2 (contract.retries)
- Direction granularity: segment

## TTS parameters (current steady state)

- Engine: IndexTTS-2.5, Gardner reference voice
- emo_alpha: 0.55
- emotion_mode: emo_text
- chaining: false
- segment_tokens: 80
- interval_silence_ms: 300
- seam_pause_ms: 300
- seed: 1234
- duration_factor: 1.0

## Implementation notes

To apply this candidate:
1. Add `safe_words` to capabilities.yaml annotation_contract.instruct_channel
2. Update annotation.py prompt_vocabulary() to include safe words in the prompt
3. Update sanitize_direction to validate against safe_words (optional — could reject non-safe words)
4. Update capabilities test assertion (direction_granularity stays segment)
5. Deploy via cli node update
6. Run a convert job on a multi-segment passage
7. Check annotated-script.json for per-segment directions
8. Listen to the retail sample
9. Record results in issue #122 comments
