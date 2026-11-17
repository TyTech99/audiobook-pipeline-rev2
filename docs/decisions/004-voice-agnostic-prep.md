# ADR-004: Voice-Agnostic Prep Stage

## Status

Accepted

## Context

The pipeline has two stages: prep (PDF/EPUB → book package) and convert (book package → audio). A key design question is how much voice-specific information should be in the prep stage output.

Options:

1. **Voice-specific prep**: Prep stage knows the voice, adds voice-specific markup to the script
2. **Voice-agnostic prep**: Prep stage produces a generic script, voice is applied at TTS stage

## Decision

The prep stage is **voice-agnostic**. It produces a base narrator script with:

- Clean text, structured by chapter and segment
- Page/item references for traceability
- Content type labels (body text, dialogue, heading, etc.)
- **No** voice assignments, pacing instructions, or engine-specific markup

Voice-specific processing (narrator cues, character voices, pacing) is handled by the **voice recipe** at the TTS stage.

## Rationale

1. **Separation of concerns**: Prep is about content extraction, TTS is about audio generation
2. **Reusability**: The same book package can be converted with multiple voices
3. **Flexibility**: Voice recipes can be updated without reprocessing the book
4. **Simplicity**: Prep worker doesn't need to know about TTS engines or voices
5. **Customer workflow**: Voice selection is a customer decision that happens after prep

## Implications

### Book Package is Voice-Agnostic

The book package (EPUB + script + ledger) can be:

- Converted with any voice recipe
- Stored indefinitely until a voice is selected
- Sent to multiple TTS workers with different voices (for A/B testing)
- Reused if the customer wants a different voice later

### Voice Recipe is Self-Contained

Each voice recipe includes:

- The TTS model and harness (Docker image)
- Script preprocessing (adding narrator cues, character voices)
- TTS parameters (sample rate, bitrate, chunk size)
- Quality control settings

### MVP Simplification

For the MVP/POC, voice selection is **hardcoded** in the coordinator. The full voice recipe system (customer selection, sample clips, A/B testing) is a future feature.

## Alternatives Considered

### Voice-Specific Prep

- Prep stage adds voice-specific markup
- But: requires reprocessing for different voices, couples prep to TTS
- Rejected because it reduces flexibility

### Two-Phase Script (Base + Voice Layer)

- Prep produces base script, TTS worker adds voice layer
- But: the "voice layer" is effectively the voice recipe
- Rejected because it's the same as voice-agnostic prep with extra terminology

## Consequences

### Positive

- Clean separation between content and presentation
- Book packages are reusable across voices
- Voice recipes are independently versioned and deployable
- Prep workers are simpler (no TTS knowledge needed)

### Negative

- TTS stage is more complex (needs to handle script decoration)
- Slight latency (voice recipe processing happens at TTS time)
- Can't preview voice-specific formatting until TTS stage
