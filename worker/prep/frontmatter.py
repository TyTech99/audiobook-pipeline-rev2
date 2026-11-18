"""Frontmatter and backmatter detection for page ledger.

Uses position + content + density heuristics to classify pages as:
- frontmatter-title: book title, author
- frontmatter-copyright: legal/copyright information
- frontmatter-toc: table of contents
- frontmatter-dedication: dedication page
- frontmatter-preface: author's preface (narratable)
- frontmatter-foreword: foreword by another author (narratable)
- frontmatter-introduction: introduction (narratable)
- frontmatter-acknowledgments: author's acknowledgments
- backmatter-index: index pages
- backmatter-bibliography: references/bibliography
- backmatter-glossary: glossary
- backmatter-about-author: about the author
- backmatter-publisher-ad: advertising content
- backmatter-colophon: production information

Pages not matching any of these are left as "unassigned" for the
structure detector to handle (chapters, etc.).
"""

from __future__ import annotations

import re


# Keywords that indicate a copyright page
COPYRIGHT_RE = re.compile(
    r"copyright|all rights reserved|isbn|printed in|published\s+by\s+\w|publisher's?",
    re.IGNORECASE,
)

# Keywords that indicate a table of contents
TOC_HEADING_RE = re.compile(
    r"^\s*(table\s+of\s+)?contents?\s*$",
    re.IGNORECASE,
)

# Dot leaders typical of TOC entries: "Title ............ 14"
TOC_DOT_LEADERS_RE = re.compile(r"\.{3,}|\u2026{2,}")

# Keywords that indicate a dedication
DEDICATION_RE = re.compile(
    r"^\s*(for\s+\w+|to\s+\w+|dedicated\s+to|in\s+memory\s+of|in\s+honor\s+of)",
    re.IGNORECASE,
)

# Keywords that indicate a preface
PREFACE_RE = re.compile(
    r"^\s*(preface|author's?\s+note|author's?\s+foreword)\s*$",
    re.IGNORECASE,
)

# Keywords that indicate a foreword
FOREWORD_RE = re.compile(
    r"^\s*foreword\s*$",
    re.IGNORECASE,
)

# Keywords that indicate an introduction
INTRODUCTION_RE = re.compile(
    r"^\s*(introduction|introductory\s+note|note\s+to\s+the\s+reader)\s*$",
    re.IGNORECASE,
)

# Keywords that indicate acknowledgments
ACKNOWLEDGMENTS_RE = re.compile(
    r"^\s*(acknowledgments?|acknowledgements?)\s*$",
    re.IGNORECASE,
)

# Keywords that indicate backmatter sections
BACKMATTER_RE = {
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

# Maximum number of lines on a title page (title + author + maybe publisher)
TITLE_PAGE_MAX_LINES = 8

# Maximum character count on a title page
TITLE_PAGE_MAX_CHARS = 300

# Maximum number of lines on a dedication page
DEDICATION_MAX_LINES = 6

# Frontmatter is expected in the first N pages
FRONTMATTER_MAX_PAGE = 15

# Backmatter is expected in the last N pages
BACKMATTER_MAX_PAGE_FROM_END = 30


def _line_stats(text: str) -> dict:
    """Compute basic statistics about a page's text."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    non_empty_lines = len(lines)
    total_chars = sum(len(l) for l in lines)
    max_line_len = max((len(l) for l in lines), default=0)
    avg_line_len = total_chars / non_empty_lines if non_empty_lines else 0
    # Centering heuristic: lines that are much shorter than the max are likely centered
    centered_lines = sum(1 for l in lines if len(l) < max_line_len * 0.7) if lines else 0
    centering_ratio = centered_lines / non_empty_lines if non_empty_lines else 0

    return {
        "non_empty_lines": non_empty_lines,
        "total_chars": total_chars,
        "max_line_len": max_line_len,
        "avg_line_len": avg_line_len,
        "centering_ratio": centering_ratio,
        "lines": lines,
    }


def _has_page_number(text: str) -> bool:
    """Check if a page has a visible page number (in header or footer)."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return False
    # Check first and last lines for standalone numbers
    for line in [lines[0], lines[-1]]:
        if re.fullmatch(r"\d{1,4}", line):
            return True
        # Roman numerals (common in frontmatter)
        if re.fullmatch(r"[ivxlcdm]+", line, re.IGNORECASE):
            return True
    return False


def _is_title_page(page_num: int, text: str, stats: dict) -> bool:
    """Check if a page looks like a title page."""
    # Title pages are almost always page 1 (or 2 if page 1 is blank/half-title)
    if page_num > 2:
        return False
    if stats["non_empty_lines"] == 0 or stats["non_empty_lines"] > TITLE_PAGE_MAX_LINES:
        return False
    if stats["total_chars"] > TITLE_PAGE_MAX_CHARS:
        return False
    # Title pages are usually centered and don't have page numbers
    if _has_page_number(text):
        return False
    # Title pages don't contain backmatter keywords
    for pattern in BACKMATTER_RE.values():
        if _match_section_heading(text, pattern):
            return False
    # Title pages typically have short, centered lines
    return stats["centering_ratio"] > 0.3


def _is_copyright_page(page_num: int, text: str) -> bool:
    """Check if a page looks like a copyright page."""
    if page_num > 6:
        return False
    return bool(COPYRIGHT_RE.search(text))


def _is_toc_page(page_num: int, text: str, stats: dict) -> bool:
    """Check if a page looks like a table of contents."""
    if page_num > FRONTMATTER_MAX_PAGE:
        return False
    lines = stats["lines"]
    # Must have a "contents" heading
    has_heading = any(TOC_HEADING_RE.match(l) for l in lines[:3])
    if not has_heading:
        return False
    # Must have dot leaders (typical of TOC entries)
    has_leaders = any(TOC_DOT_LEADERS_RE.search(l) for l in lines)
    return has_leaders


def _is_dedication_page(page_num: int, text: str, stats: dict) -> bool:
    """Check if a page looks like a dedication."""
    if page_num > 5:
        return False
    if stats["non_empty_lines"] == 0 or stats["non_empty_lines"] > DEDICATION_MAX_LINES:
        return False
    if stats["total_chars"] > 200:
        return False
    # Check for dedication keywords or very short centered text
    if DEDICATION_RE.search(text):
        return True
    # Short, centered page in the first few pages is likely a dedication
    if stats["centering_ratio"] > 0.5 and stats["non_empty_lines"] <= 3:
        return True
    return False


def _match_section_heading(text: str, pattern: re.Pattern) -> bool:
    """Check if any line in the text matches a section heading pattern."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    return any(pattern.match(l) for l in lines[:5])


def _is_backmatter_page(page_num: int, total_pages: int, text: str) -> str | None:
    """Check if a page is backmatter. Returns the section label or None."""
    # Backmatter is expected in the last N pages
    if page_num < total_pages - BACKMATTER_MAX_PAGE_FROM_END:
        return None
    for label, pattern in BACKMATTER_RE.items():
        if _match_section_heading(text, pattern):
            return label
    return None


def classify_frontmatter(pages: list[dict]) -> dict[int, str]:
    """Classify pages as frontmatter/backmatter sections.

    Args:
        pages: List of page dicts with 'page' (1-based number) and 'text' keys.

    Returns:
        Dict mapping page number (1-based) to section label.
        Pages not classified as frontmatter/backmatter are not in the dict.
    """
    total_pages = len(pages)
    result: dict[int, str] = {}
    frontmatter_done = False

    for page in pages:
        page_num = page["page"]
        text = page["text"]
        stats = _line_stats(text)

        # Skip if no text
        if stats["non_empty_lines"] == 0:
            continue

        # Frontmatter detection (only in the first N pages)
        if not frontmatter_done and page_num <= FRONTMATTER_MAX_PAGE:
            # Copyright first (more specific than title page)
            if _is_copyright_page(page_num, text):
                result[page_num] = "frontmatter-copyright"
                continue
            if _is_title_page(page_num, text, stats):
                result[page_num] = "frontmatter-title"
                continue
            if _is_toc_page(page_num, text, stats):
                result[page_num] = "frontmatter-toc"
                continue
            if _is_dedication_page(page_num, text, stats):
                result[page_num] = "frontmatter-dedication"
                continue
            if _match_section_heading(text, PREFACE_RE):
                result[page_num] = "frontmatter-preface"
                continue
            if _match_section_heading(text, FOREWORD_RE):
                result[page_num] = "frontmatter-foreword"
                continue
            if _match_section_heading(text, INTRODUCTION_RE):
                result[page_num] = "frontmatter-introduction"
                continue
            if _match_section_heading(text, ACKNOWLEDGMENTS_RE):
                result[page_num] = "frontmatter-acknowledgments"
                continue

            # Once we hit a page that's clearly body text (long, dense),
            # stop looking for frontmatter
            if stats["total_chars"] > 500 and stats["non_empty_lines"] > 10:
                frontmatter_done = True

        # Backmatter detection (only in the last N pages)
        backmatter = _is_backmatter_page(page_num, total_pages, text)
        if backmatter:
            result[page_num] = backmatter

    return result


def get_frontmatter_reason(section: str) -> str:
    """Get a human-readable reason for a frontmatter/backmatter section."""
    reasons = {
        "frontmatter-title": "title page",
        "frontmatter-copyright": "copyright",
        "frontmatter-toc": "table of contents",
        "frontmatter-dedication": "dedication",
        "frontmatter-preface": "preface",
        "frontmatter-foreword": "foreword",
        "frontmatter-introduction": "introduction",
        "frontmatter-acknowledgments": "acknowledgments",
        "backmatter-index": "index",
        "backmatter-bibliography": "bibliography",
        "backmatter-glossary": "glossary",
        "backmatter-about-author": "about the author",
        "backmatter-colophon": "colophon",
        "backmatter-publisher-ad": "publisher ad",
    }
    return reasons.get(section, "frontmatter")
