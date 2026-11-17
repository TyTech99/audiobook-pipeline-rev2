# Task: Define the Voice Recipe Schema

## Status

Open — to be detailed in a future session.

## Context

The voice recipe is a self-contained bundle that defines everything needed to produce a finished audiobook for a specific voice. It covers:

1. **Voice model** — The TTS model, weights, and engine
2. **Engine capabilities** — How the engine expresses prosody, emotion, pauses, emphasis
3. **Annotation markup examples** — Examples of the engine's annotation markup
4. **Performance styles** — A list of supported performance styles with prompt templates
5. **Annotation prompt** — The LLM prompt template for script annotation
6. **TTS parameters** — Sample rate, bitrate, chunk size, overlap
7. **Mastering chain** — DSP parameters (EQ, compression, loudness, noise floor, room tone)
8. **Packaging** — M4B/MP3 container parameters (format, codec, chapters, metadata, cover art)
9. **Quality control** — ACX compliance checks

## Key Design Decisions (from discussion)

- The recipe is selected at Phase 2 job submission time
- The performance style is a parameter of the job, validated against the recipe's supported styles
- The annotation step is engine-specific and style-specific
- The annotation is chunked: chapters run in parallel, chunks within a chapter run serially with state passing
- Chapters are emotional reset points (state resets at chapter boundaries)
- The text is immutable: the clean script is the single source of truth, and each chunk is validated against the source
- The recipe package pre-supplies all engine capabilities and examples of annotation markup
- The schema is engine-agnostic: it accommodates the core elements (prosody, emotion, pauses, emphasis) that any TTS engine needs to express
- Voice recipes live in a separate git repo from the pipeline code

## Immutable Requirement

**Not one of the author's words can be changed. Not one.**

- The annotated script references clean script segments by ID
- The text is always read from the clean script. There is no second copy to drift.
- Annotations are metadata only — they describe performance, never modify text.
- The LLM is instructed to preserve the text exactly.
- Each chunk returned from the LLM is validated against the source text before it's accepted. If the text differs from the source, the chunk is rejected and retried.

This constraint applies to the LLM prompt, the annotation format, and the QC validation.

## Performance Style Concept

In audiobook narration, performance is multi-dimensional:

- **Tone**: Attitude toward the subject (serious, playful, ominous)
- **Mood**: Atmosphere (dark, light, tense, calm)
- **Register**: Formality and vocal quality (conversational, elevated, gravelly)
- **Delivery**: Pacing, rhythm, tempo, emphasis
- **Style**: Overall approach (straight read, fully voiced, cinematic)

The term **"performance style"** encompasses all of these. A performance style is a named combination of tone, mood, register, and delivery instructions.

Examples:
- "Neutral narration" — even delivery, neutral tone, conversational register
- "Children's story" — bright, energetic, playful tone, light mood
- "Noir mystery" — slow, gravelly, ominous tone, dark mood, elevated register
- "Action thriller" — fast-paced, intense, urgent tone, high energy

The performance style influences:
- Annotation (LLM prompt for performance direction)
- Mastering (EQ, pacing, room tone can be style-specific)

## Engine-Agnostic Schema Design

The schema must be engine-agnostic. Chatterbox and HiggsV3 are examples among countless engines, with more emerging regularly. The core elements the schema accommodates are:

- How to express prosody (speed, pitch, volume)
- How to express emotion
- How to express pauses
- How to express emphasis
- How to express character voice changes (if supported)
- Generation parameters (temperature, exaggeration, etc.)

The recipe package pre-supplies all engine capabilities and examples of annotation markup. The engine's syntax is provided in the prompt. The recipe's Docker image includes the TTS engine and its documentation.

## Chunk Strategy

- **Chunk size**: One paragraph or 2-3 sentences. Small enough for the LLM to handle well, large enough to maintain context.
- **Chapters run in parallel** (one LLM session per chapter)
- **Chunks within a chapter run serially** (state dependency)
- **Chapters are emotional reset points** (state resets at chapter boundaries)
- **Each chunk's LLM call receives the previous chunk's ending state** (emotion, pacing, tension, active characters)

**Parallelism depends on model slot capabilities:**
- Cloud LLMs: unlimited parallel slots
- Local LLMs: seat count declared in the worker registration
- The coordinator schedules chapter annotation based on available slots

## Text Validation Per Chunk

- The clean script is the single source of truth for text
- The annotated script references clean script segments by ID
- The LLM is instructed to preserve the text exactly
- Each chunk returned from the LLM is validated against the source text before it's accepted. If the text differs from the source, the chunk is rejected and retried.

## Open Questions

- How are performance styles structured in the recipe YAML?
- How are annotation prompts versioned and stored?
- How does the recipe declare which TTS engine it uses, and how does that affect the annotation markup?
- How are mastering parameters overridden by performance style (if at all)?
- How is the recipe's Docker image built and versioned?
- How does the coordinator validate that a requested performance style is supported by a recipe?
- What is the exact format of the engine capabilities YAML?
- What is the exact format of the markup examples document?
- How is the LLM annotation prompt template structured?
- How is the chunk state (emotion, pacing, tension) represented and passed between chunks?

## References

- [Voice Recipe (current draft)](../docs/data-models/voice-recipe.md)
- [Audiobook Package](../docs/data-models/audiobook-package.md)
- [Narrator Script](../docs/data-models/narrator-script.md)
- [Architecture Overview](../docs/architecture/overview.md)
