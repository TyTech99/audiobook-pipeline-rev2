"""Recognize the non-text parts of a page: running header, page number, figure caption."""
import re
from difflib import SequenceMatcher

CAPTION_RE = re.compile(r"^\s*(LEFT|RIGHT|ABOVE|BELOW|OPPOSITE|FACING)\s*:\s*([A-Z][A-Z .,'\-]+?)\s*$")
_NUM_LEAD = re.compile(r"^\s*(\d{1,3})\s{2,}(\S.*)$")
_NUM_TRAIL = re.compile(r"^(.*\S)\s{2,}(\d{1,3})\s*$")
_NUM_ONLY = re.compile(r"^\s*(\d{1,3})\s*$")
# A lone lower-case roman page number (front matter: 'vii', 'viii'). Only well-formed numerals up to 399.
_ROMAN_ONLY = re.compile(r"^\s*((?=[ivxlc])c{0,3}(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3}))\s*$")
# A line of ornaments only ('*       *       *'): a scene break, not text and not part of a running header.
ORNAMENT_RE = re.compile(r"^\s*(?:[*\u2022\u00b7\u2042~]\s*){3,}$")


def norm(text):
    return re.sub(r"[^a-z]", "", text.lower())


def similar(a, b):
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def _mostly_upper(text):
    letters = [c for c in text if c.isalpha()]
    return (not letters) or sum(c.isupper() for c in letters) / len(letters) > 0.7


def header_zone(lines, title, extra_titles=(), similarity_threshold=0.9):
    """The running header at the top of a unit.
    Returns (first_line, last_line_exclusive, page_number or None) or None.
    The header starts at the first non-empty line and runs over the following lines while they are
    upper case (at most 3 lines in all), whether or not a blank line separates it from the text.
    A line with no letter or digit (an ornament line, '* * *') never belongs to it.
    It is a header if it carries a page number next to uppercase text, or reads as the book title or as
    the chapter title (extra_titles: the right-hand page repeats the chapter title, and its page number
    may be misread by the scan as a letter, so no number is found)."""
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines):
        return None
    j = i + 1
    while j < len(lines) and lines[j].strip() and j - i < 3 and _mostly_upper(lines[j]) and re.search(r"[A-Za-z0-9]", lines[j]):
        j += 1
    zone = lines[i:j]
    number, rest = None, []
    for ln in zone:
        m = _NUM_LEAD.match(ln) or None
        if m and number is None:
            number, rest = int(m.group(1)), rest + [m.group(2)]
            continue
        m = _NUM_TRAIL.match(ln)
        if m and number is None:
            number, rest = int(m.group(2)), rest + [m.group(1)]
            continue
        m = _NUM_ONLY.match(ln)
        if m and number is None:
            number = int(m.group(1))
            continue
        rest.append(ln)
    text = " ".join(rest)
    if number is not None and _mostly_upper(text) and len(zone) <= 3:
        return i, j, number
    for t in (title,) + tuple(extra_titles):
        if t and similar(text, t) >= similarity_threshold:
            return i, j, None
    if j > i + 1:                          # the header line alone, followed by an upper-case line of the text (a subheading)
        for t in (title,) + tuple(extra_titles):
            if t and similar(lines[i], t) >= similarity_threshold:
                return i, i + 1, None
    return None


def running_lines(units, min_units=3, max_words=12):
    """Lines that open at least `min_units` pages: running headers that are neither upper case nor numbered
    (for example the full book title in mixed case, which may differ from the title the book is filed under).
    Only the first non-empty line of each unit counts, and only if it is short. Returned as texts for header_zone."""
    seen = {}
    for u in units:
        for ln in u["text"].split("\n"):
            if ln.strip():
                k = norm(ln)
                if len(k) >= 4 and len(ln.split()) <= max_words:
                    seen.setdefault(k, [0, ln.strip()])[0] += 1
                break
    return tuple(t for n, t in seen.values() if n >= min_units)


def footer_number(lines):
    """A lone page number as the last non-empty line. Returns (line_index, number) or None."""
    k = len(lines) - 1
    while k >= 0 and not lines[k].strip():
        k -= 1
    if k >= 0 and _NUM_ONLY.match(lines[k]):
        return k, int(lines[k])
    return None


def footer_roman(lines):
    """A lone lower-case roman page number ('vii') as the last non-empty line. Returns (line_index, numeral) or None."""
    k = len(lines) - 1
    while k >= 0 and not lines[k].strip():
        k -= 1
    if k >= 0:
        m = _ROMAN_ONLY.match(lines[k])
        if m:
            return k, m.group(1)
    return None
