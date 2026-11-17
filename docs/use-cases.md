# Use Cases

This document describes the use cases enabled by the high-quality audiobook pipeline.

## 1. ACX-Ready Audiobook Production

**Description:** Produce audiobooks that meet ACX (Audiobook Creation Exchange) technical requirements for submission to Audible, Amazon, and Apple Books.

**Workflow:**
1. Author/publisher uploads a PDF or EPUB
2. Prep worker produces a book package (clean EPUB, narrator script, page ledger, cover art, metadata, LLM analysis)
3. User selects a voice recipe and performance style (based on LLM recommendations)
4. TTS worker produces an ACX-compliant audiobook package (MP3 chapters + M4B)
5. User submits the MP3 chapters to ACX

**Value:**
- The page ledger provides an audit trail that every page of the source document is accounted for
- The QC report verifies ACX compliance (RMS, peaks, noise floor, room tone, chapter structure)
- The M4B file provides a professional listening experience with chapters, metadata, and cover art

## 2. Self-Publishing Audiobook Service

**Description:** Indie authors upload a PDF/EPUB and get back a finished, professional audiobook package.

**Workflow:**
1. Author uploads a PDF or EPUB
2. Prep worker produces a book package with LLM analysis (subject, tone, style recommendations)
3. Author selects a voice recipe and performance style from the recommendations
4. TTS worker produces the audiobook package
5. Author downloads the M4B file for direct playback or the MP3 chapters for platform submission

**Value:**
- Minimum submission resistance: the author uploads a PDF, and the pipeline handles the rest
- LLM analysis helps the author choose the right voice and performance style
- The output is professional quality, ready for any platform

## 3. Audiobook Localization

**Description:** Produce audiobooks in multiple languages from a single source document.

**Workflow:**
1. Source document is processed into a book package (Phase 1)
2. The book package is translated into the target language
3. The translated book package is processed with a voice recipe for the target language (Phase 2)
4. Each language has its own voice recipe, performance style, and mastering settings

**Value:**
- The voice-agnostic book package enables fan-out to multiple languages
- Each language can use the optimal voice recipe and performance style
- The page ledger ensures the translation covers the full book

## 4. Accessible Audiobooks

**Description:** Produce audiobooks for visually impaired readers with proper accessibility features.

**Workflow:**
1. Source document is processed into a book package (Phase 1)
2. TTS worker produces an audiobook package with proper chapter navigation, consistent loudness, and clean audio (Phase 2)

**Value:**
- Proper chapter navigation (QuickTime chapter track)
- Consistent loudness (no volume adjustments needed)
- Clean audio (no artifacts that confuse screen readers)
- Proper metadata (title, author, narrator for library integration)

## 5. Corporate/Training Audiobooks

**Description:** Produce audiobooks for corporate training materials, compliance documents, and onboarding guides.

**Workflow:**
1. Training document is uploaded as a PDF (Phase 1)
2. TTS worker produces an audiobook package with the company's brand voice (Phase 2)

**Value:**
- The page parity guarantee ensures every page of the source document is accounted for
- The voice recipe system lets companies use their own brand voice
- The M4B packaging with chapters makes the content navigable
- The QC report provides an audit trail for compliance

## 6. Audiobook Restoration

**Description:** Take an old, low-quality audiobook (MP3s with inconsistent levels, no chapters, bad metadata) and produce a modern audiobook package.

**Workflow:**
1. Old audiobook files are uploaded
2. Prep worker analyzes the audio and produces a book package (metadata, chapter structure)
3. TTS worker masters the audio and produces a modern audiobook package (M4B + MP3 chapters)

**Value:**
- The mastering chain normalizes loudness and removes noise
- The packaging stage adds proper chapter markers, metadata, and cover art
- The output is a professional, modern audiobook package

## 7. Multi-Voice Audiobooks

**Description:** Produce audiobooks with different voices for different characters.

**Workflow:**
1. Source document is processed into a book package (Phase 1)
2. The narrator script identifies dialogue segments and assigns character identifiers
3. TTS worker uses multiple voice recipes, one per character (Phase 2)
4. The packaging stage handles voice transitions between characters

**Value:**
- The segment-level granularity in the narrator script supports character-specific voices
- The voice recipe system supports multiple recipes per job
- The output is a fully-cast audiobook with distinct character voices
