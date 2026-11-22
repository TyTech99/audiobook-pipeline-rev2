"""Engine-neutral text helpers — one definition, used by every recipe.

Moved from the CosyVoice recipe when the second engine arrived; the
CosyVoice run.py still carries its local copy pending the consolidation
issue (no logic fragmentation: the fix is to delete the per-copy, never
to edit it in place).
"""

from __future__ import annotations

import re


def chunk_text(text: str, max_chars: int = 300) -> list[str]:
    """Split text into chunks at sentence boundaries, respecting max_chars.

    If a single sentence exceeds max_chars, split at word boundaries.
    Returns non-empty strings that concatenate (with spaces) to
    approximately the original text.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if not current:
            current = sentence
        elif len(current) + 1 + len(sentence) <= max_chars:
            current += " " + sentence
        else:
            chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)

    final: list[str] = []
    for chunk in chunks:
        if len(chunk) <= max_chars:
            final.append(chunk)
            continue
        words = chunk.split()
        buf = ""
        for word in words:
            if not buf:
                buf = word
            elif len(buf) + 1 + len(word) <= max_chars:
                buf += " " + word
            else:
                final.append(buf)
                buf = word
        if buf:
            final.append(buf)
    return final


def split_sentences(text: str) -> list[str]:
    """Sentence fragments of a chunk, using chunk_text's own rule."""
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def normalize_for_comparison(text: str) -> str:
    """Word cores only: lowercase, strip punctuation, keep intra-word
    apostrophes — hint-added punctuation must not break the word check."""
    words = re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)*", text.lower())
    return " ".join(words)


def validate_annotation(source_text: str, annotated_text: str) -> bool:
    """Check that annotated text still contains all source words in order."""
    src_words = normalize_for_comparison(source_text).split()
    ann_words = normalize_for_comparison(annotated_text).split()
    if not src_words:
        return True
    it = iter(ann_words)
    return all(word in it for word in src_words)


def first_annotation_mismatch(source_text: str, annotated_text: str) -> str | None:
    """First source word the annotation does not preserve (for logging)."""
    src_words = normalize_for_comparison(source_text).split()
    ann_words = normalize_for_comparison(annotated_text).split()
    it = iter(ann_words)
    for word in src_words:
        if word not in it:
            return word
    return None
