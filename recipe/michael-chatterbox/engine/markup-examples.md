# Chatterbox Markup Examples

Chatterbox TTS does **not** support inline prosody markup. It reads plain text
and generates speech based on the reference voice sample and natural language
punctuation.

## What the LLM annotation can do

Since Chatterbox has no markup language, the LLM annotation stage is limited
to subtle text adjustments that influence prosody through punctuation:

### Before (raw text)
```
The castle loomed against the gray sky. He paused at the gate.
```

### After (annotated for noir-mystery style)
```
The castle loomed... against the gray sky. He paused— at the gate.
```

The ellipsis adds a trailing pause; the em dash adds a dramatic break.

### Before (raw text)
```
She said she would never come back.
```

### After (annotated for emphasis)
```
She said she would *never* come back!
```

Note: asterisks are stripped before TTS; the exclamation mark carries the emphasis.

## What the LLM annotation must NOT do

- **Never change source words.** All original words must appear in order.
- **Never add new words** (except punctuation).
- **Never remove words.**
- **Never add markup tags** like `[pause]` or `<emphasis>` — these are not
  interpreted by Chatterbox and would be read aloud.

## Performance style influence

The performance style (neutral, childrens-story, noir-mystery) guides the LLM
on how aggressively to adjust punctuation:

| Style | Punctuation approach |
|-------|---------------------|
| neutral | Minimal changes, preserve original punctuation |
| childrens-story | More exclamation marks, playful ellipses |
| noir-mystery | More em dashes, ellipses, dramatic pauses |

## Chunking

Long text is split into chunks of ~300 characters at sentence boundaries
before being sent to Chatterbox. Each chunk is synthesized independently
and concatenated. The LLM annotation should be aware that a chunk may
start or end mid-paragraph.
