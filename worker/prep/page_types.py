"""Canonical keyword tables and the shared page/item text classifier.

One home for the classification vocabulary so the PDF and EPUB paths
cannot drift apart (issue #42): pdf_parser.classify_page and
epub_parser.classify_epub_item are thin wrappers over
classify_text_page(), and every keyword table that used to exist in two
or three files is defined exactly once here.

What deliberately does NOT live here:
- frontmatter.COPYRIGHT_RE — a broader page-level copyright detector
  ('printed in', publisher lines); a different heuristic, not a copy of
  COPYRIGHT_KEYWORDS_RE.
- structure.UNNUMBERED_SECTION_RE — heading boundary detection, used
  once, a different concern from page-action classification.

The two backmatter tables DO live here, side by side, because they are
two consumers of one vocabulary:
- BACKMATTER_EXCLUSION_RE — structure.py: where the last chapter ends
  and narration must stop ('Publishers' Announcement', 'Also by ...').
- BACKMATTER_LABEL_RES — frontmatter.py: which page gets which
  backmatter section label (index, bibliography, ...).
"""

from __future__ import annotations

import re

# Keyword presence anywhere on the page/item.
COPYRIGHT_KEYWORDS_RE = re.compile(r"copyright|all rights reserved|isbn", re.IGNORECASE)

# Frontmatter headings sit at the top of a page/item; a mid-text line
# ("For events outstripped me.") must not reclassify it.
TOP_LINES = 6

TOC_HEADING_LINE_RE = re.compile(r"^\s*(table\s+of\s+contents|contents)\s*$", re.IGNORECASE)
DEDICATION_HEADING_RE = re.compile(r"^\s*dedication\b", re.IGNORECASE)
ACKNOWLEDGMENTS_HEADING_RE = re.compile(r"^\s*(acknowledgments?|acknowledgements?)\s*$", re.IGNORECASE)
BACKMATTER_HEADING_RE = re.compile(r"^\s*(index|bibliography|references|glossary)\s*$", re.IGNORECASE)

# Words that introduce a numbered section heading.
NUMBERED_SECTION_WORDS = "chapter|part|book|prologue|epilogue|preface|foreword|introduction"
# Heading at the very start of the page/item text ('chapter 1 ...').
NUMBERED_HEADING_START_RE = re.compile(r"^\s*(?:" + NUMBERED_SECTION_WORDS + r")\s+\d+", re.IGNORECASE)
# The naive fallback's line form: '<word> <number> [title]'.
NUMBERED_HEADING_LINE_RE = re.compile(
    r"^\s*(" + NUMBERED_SECTION_WORDS + r")\s+(\d+|[ivxlcdm]+)\.?\s*(.*)$", re.IGNORECASE
)

# Back matter that ends the last chapter: the chapter stops before the
# unit whose first body line matches this, and it is not narrated.
BACKMATTER_EXCLUSION_RE = re.compile(
    r"^\s*(bibliography|index|general index|colophon|advertisements?|publishers?\W{0,2} announcements?"
    r"|(?:other )?books by the same author|also by [A-Za-z .]{2,40}|other books by [A-Za-z .]{2,40})\s*[.:]?\s*$",
    re.I,
)

# Back matter page labels (label semantics, unlike the exclusion table above).
BACKMATTER_LABEL_RES = {
    "backmatter-index": re.compile(r"^\s*(index|general\s+index|subject\s+index|name\s+index)\s*$", re.IGNORECASE),
    "backmatter-bibliography": re.compile(r"^\s*(bibliography|references|works\s+cited|further\s+reading)\s*$", re.IGNORECASE),
    "backmatter-glossary": re.compile(r"^\s*glossary\s*$", re.IGNORECASE),
    "backmatter-about-author": re.compile(r"^\s*(about\s+the\s+author|about\s+the\s+narrator)\s*$", re.IGNORECASE),
    "backmatter-colophon": re.compile(r"^\s*colophon\s*$", re.IGNORECASE),
    "backmatter-publisher-ad": re.compile(
        r"^\s*(also\s+by|other\s+books|publishers?'\s*announcements?|advertisements?)\s*$",
        re.IGNORECASE,
    ),
}


def classify_text_page(text: str, images: int = 0) -> tuple[str, str]:
    """Classify one page (PDF) or spine item (EPUB) of text.

    Returns (action, reason). The single classifier behind
    pdf_parser.classify_page and epub_parser.classify_epub_item —
    both formats see identical rules.
    """
    if not text or len(text) < 10:
        if images > 0:
            return "skipped", "illustration only"
        return "skipped", "blank"

    lower = text.lower()
    if COPYRIGHT_KEYWORDS_RE.search(lower):
        return "kept", "copyright"

    # Frontmatter headings sit at the top; only a literal DEDICATION
    # heading counts ('For <word>' matches ordinary sentence openings).
    # True dedication pages are labeled by frontmatter.classify_frontmatter,
    # which has length guards.
    top = "\n".join(text.split("\n")[:TOP_LINES])
    if TOC_HEADING_LINE_RE.search(top):
        return "kept", "table of contents"
    if DEDICATION_HEADING_RE.search(top):
        return "kept", "dedication"
    if ACKNOWLEDGMENTS_HEADING_RE.search(top):
        return "kept", "acknowledgments"
    if BACKMATTER_HEADING_RE.search(top):
        return "kept", "backmatter"

    if NUMBERED_HEADING_START_RE.match(lower):
        return "kept", "chapter heading"

    return "kept", "body text"
