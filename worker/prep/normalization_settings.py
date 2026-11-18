"""Normalization settings for text cleanup after OCR/extraction.

Thresholds are read from worker-prep.yaml (normalization section) with
per-job overrides via job metadata. Not hardcoded.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class NormalizationSettings:
    """Text normalization settings from config.

    These thresholds control how aggressively the cleanup pipeline
    detects and removes layout artifacts (headers, page numbers) and
    reconstructs paragraphs. Tune per-book if the defaults don't match
    the layout.
    """

    # Paragraph detection (geometric, in points)
    # A line starts a new paragraph if it's indented at least this much
    # relative to the previous line, or sits this much lower than normal
    # line spacing.
    indent_pt: float = 4.5
    gap_pt: float = 2.5

    # Line spacing bounds (in points). Gaps outside this range are treated
    # as column jumps or overlaps, not line spacing.
    pitch_min_pt: float = 6.0
    pitch_max_pt: float = 40.0

    # Mid-sentence paragraph merging: a paragraph that stops mid-sentence
    # (no end mark, at least this many words) is joined to the next one.
    mid_sentence_min_words: int = 6

    # Running header detection
    # A line that opens at least this many pages is considered a running header.
    running_header_min_pages: int = 3
    # Max words in a line to be considered a running header candidate.
    running_header_max_words: int = 12
    # Title similarity threshold (0-1) for header detection. A header line
    # that matches the book title above this ratio is removed.
    header_title_similarity: float = 0.9

    @classmethod
    def from_config(cls, config: dict) -> "NormalizationSettings":
        """Create settings from worker-prep.yaml normalization section."""
        norm = config.get("normalization", {})
        return cls(
            indent_pt=float(norm.get("indent_pt", 4.5)),
            gap_pt=float(norm.get("gap_pt", 2.5)),
            pitch_min_pt=float(norm.get("pitch_min_pt", 6.0)),
            pitch_max_pt=float(norm.get("pitch_max_pt", 40.0)),
            mid_sentence_min_words=int(norm.get("mid_sentence_min_words", 6)),
            running_header_min_pages=int(norm.get("running_header_min_pages", 3)),
            running_header_max_words=int(norm.get("running_header_max_words", 12)),
            header_title_similarity=float(norm.get("header_title_similarity", 0.9)),
        )

    def merge_job_overrides(self, overrides: dict) -> "NormalizationSettings":
        """Return a new settings instance with per-job overrides applied.

        Only keys present in overrides are changed; others keep their values.
        """
        if not overrides:
            return self
        data = self.__dict__.copy()
        for key, value in overrides.items():
            if key in data and value is not None:
                data[key] = type(data[key])(value)
        return NormalizationSettings(**data)
