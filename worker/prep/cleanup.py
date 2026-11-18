"""Stage 2, clean-up: turn the text layer of a chapter into clean paragraphs.
Every rule is deterministic. Every change and every word the dictionary does not know goes to warnings.log.
Author words are never rewritten, only layout artifacts are removed or repaired."""
import json
import os
import re

try:
    from spellchecker import SpellChecker
    _dict = SpellChecker()
except ImportError:
    _dict = None

from worker.prep import bookio_old as bookio
from worker.prep import dicts
from worker.prep import pageparts
from worker.prep import pdftext_old as pdftext
from worker.prep import structure

WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_language = {"code": "eng"}      # the book being cleaned; set by run()


def set_language(code):
    """English keeps the stage 1 dictionary (pyspellchecker). German and Spanish use their word lists (dicts.py)."""
    _language["code"] = code or "eng"


def known(word):
    if _dict is None:
        return True  # Can't check without spellchecker; assume known
    if _language["code"] == "eng":
        return bool(_dict.known([word.lower()]))
    return word.lower() in dicts.words(_language["code"])


def word_re():
    return WORD_RE if _language["code"] == "eng" else dicts.WORD_RE


def vocabulary(units):
    """Words and hyphenated words that occur inline (not split by a line break) anywhere in the book."""
    inline = set()
    for u in units:
        for ln in u["text"].split("\n"):
            toks = re.findall(r"[A-Za-z]+(?:-[A-Za-z]+)*", ln)
            if ln.rstrip().endswith("-") and toks:
                toks = toks[:-1]
            inline.update(t.lower() for t in toks)
    return inline


def parts_known(word, inline):
    return all(known(p) or p.lower() in inline for p in word.split("-") if p)


def join_hyphen(left, frag, inline):
    """Decide how to join 'left-' + 'frag'. Returns (joined_word, reason, resolved)."""
    joined, hyph = left + frag, left + "-" + frag
    if hyph.lower() in inline:
        return hyph, "hyphenated form occurs elsewhere in the book", True
    if parts_known(joined, inline) and (known(joined.split("-")[-1]) or joined.lower() in inline):
        return joined, "joined word is a known word", True
    if known(left.split("-")[-1]) and known(frag):
        return hyph, "both fragments are words and the joined form is unknown, hyphen kept", True
    return joined, "neither form is known, joined by default", False


def split_dropcap(lines, i):
    """A lone capital letter followed by a line that starts with the rest of the first word."""
    if re.fullmatch(r"[A-Z]", lines[i]["text"].strip()) and i + 1 < len(lines):
        nxt = lines[i + 1]["text"].strip()
        if re.match(r"[A-Z]{2,}\b", nxt) or re.match(r"[A-Z]{2,}$", nxt):
            return True
    return False


def smallcaps_lead(text):
    """'ALL MY LIFE I have' -> 'All my life I have' (leading run of capitals after a drop cap)."""
    toks = text.split(" ")
    n = 0
    while n < len(toks) and toks[n].strip(".,;:!?\"'").isupper():
        n += 1
    out = []
    for k, t in enumerate(toks):
        if k < n and len(t.strip(".,;:!?\"'")) > 1:
            t = t.lower()
        if k == 0:
            t = t[:1].upper() + t[1:]
        out.append(t)
    return " ".join(out)


def unit_lines(unit, title, heading, first_unit, notes, running=(), start_line=0, end_line=None):
    """Body lines of one unit as dicts {text, indent, where}; removed parts are noted.
    running: lines that open many pages of the book (pageparts.running_lines), also treated as running headers.
    start_line / end_line: line slice on this unit when a treatise section starts or ends mid-page (0/None for standard books)."""
    raw = [l.rstrip() for l in unit["text"].split("\n")]
    geom = unit.get("geom") or [None] * len(raw)
    where = "pdf p.%d%s" % (unit["pdf_page"], unit["half"] or "")
    drop = set()
    if start_line > 0:
        drop.update(range(0, start_line))
    if end_line is not None:
        drop.update(range(end_line, len(raw)))
    z = pageparts.header_zone(raw, title, ([heading["title"]] if heading else []) + list(running))
    if z:
        drop.update(range(z[0], z[1]))
        if start_line == 0:
            what = "page number %d" % z[2] if z[2] is not None else "no page number"
            notes.append(("header-removed", where, "running header '%s' (%s)" % (" ".join(l.strip() for l in raw[z[0]:z[1]]), what)))
    f = pageparts.footer_number(raw)
    if f:
        drop.add(f[0])
        if end_line is None:
            notes.append(("page-number-removed", where, "footer page number %d" % f[1]))
    else:
        r = pageparts.footer_roman(raw)
        if r:
            drop.add(r[0])
            if end_line is None:
                notes.append(("page-number-removed", where, "footer page number %s (roman)" % r[1]))
    if first_unit and heading and not heading.get("synthetic"):
        drop.update(range(0, heading["title_end"]))
        if heading.get("treatise"):
            notes.append(("heading-removed", where, "section heading '%s' (spoken header is added in the script)" % heading["title"]))
        elif heading.get("label"):
            notes.append(("heading-removed", where, "lesson heading '%s / %s' (spoken header is added in the script)" % (heading["label"], heading["title"])))
        else:
            notes.append(("heading-removed", where, "chapter heading 'CHAPTER %d / %s' (spoken header is added in the script)" % (heading["number"], heading["title"])))
    # last line of running text: the lines after it (all capitals only) are picture captions, not headings
    body = [i for i, l in enumerate(raw) if i not in drop and any(c.islower() for c in l)]
    last_body = body[-1] if body else -1
    out = []
    for i, ln in enumerate(raw):
        if i in drop or not ln.strip():
            continue
        if pageparts.ORNAMENT_RE.match(ln):
            notes.append(("section-break", where, "ornament line '%s' not read; the text after it starts a new paragraph" % " ".join(ln.split())))
            out.append({"text": "", "ornament": True, "indent": 0, "where": where, "unit": (unit["pdf_page"], unit["half"]), "x": None, "y": None})
            continue
        if pageparts.CAPTION_RE.match(ln):
            notes.append(("caption-removed", where, "figure caption '%s' (listed in figures/skipped_figures.md)" % ln.strip()))
            continue
        if CAPTION_MIXED_RE.match(ln):
            notes.append(("caption-removed", where, "figure caption '%s' (marker not in capitals)" % ln.strip()))
            continue
        letters = [c for c in ln if c.isalpha()]
        if len(letters) >= 3 and not any(c.islower() for c in letters):
            if i > last_body:
                notes.append(("caption-removed", where, "all-capitals line '%s' below the text (picture caption or label)" % ln.strip()))
                continue
            spoken = bookio.title_case(" ".join(ln.split()))
            notes.append(("heading-kept", where, "all-capitals line above the text kept as a section heading: '%s' -> '%s'" % (ln.strip(), spoken)))
            ln = " " * (len(ln) - len(ln.lstrip())) + spoken
        g = geom[i] if i < len(geom) else None
        out.append({"text": ln.strip(), "indent": len(ln) - len(ln.lstrip()), "where": where,
                    "unit": (unit["pdf_page"], unit["half"]), "x": g["x"] if g else None, "y": g["y"] if g else None})
    return out


INDENT_PT = 4.5    # a paragraph's first line sits at least this many points right of the line before it (default; overridden by NormalizationSettings)
GAP_PT = 2.5       # or this many points lower than the page's normal line spacing (default)
SAMELINE_CAP = re.compile(r"^([A-Z])\s{2,}([A-Z]{2,})\b")
SAMELINE_CAP_LOWER = re.compile(r"^([A-Z])\s+([a-z]+)\b")     # 'T      he Western', 'I n this': a drop cap before the rest of a lower-case word


def lower_dropcap(text):
    """The SAMELINE_CAP_LOWER match if the letter and the fragment are really one word: the joined form is a word, and the
    line is not an ordinary 'A word' / 'I word' (where the fragment is itself a word). Else None."""
    m = SAMELINE_CAP_LOWER.match(text)
    if not m:
        return None
    letter, frag = m.group(1), m.group(2)
    if known(letter + frag) and not (letter in "AI" and len(frag) > 1 and known(frag)):    # 'I n' -> 'In'; 'A man' stays
        return m
    return None


PITCH_MIN_PT, PITCH_MAX_PT = 6, 40    # line gaps outside this range are column jumps or overlaps, never line spacing (defaults)


def line_pitch(gaps, pitch_min=PITCH_MIN_PT, pitch_max=PITCH_MAX_PT):
    """The page's normal line spacing: the most common gap between consecutive lines (rounded to 0.5 pt; a tie goes to
    the smaller gap). Inside a paragraph every gap is the line spacing, so it is the commonest gap on any page with
    text, whatever the type size. None when the page has no usable gap."""
    counts = {}
    for g in gaps:
        if pitch_min < g < pitch_max:
            r = round(g * 2) / 2
            counts[r] = counts.get(r, 0) + 1
    if not counts:
        return None
    return min(counts, key=lambda r: (-counts[r], r))


def unit_stats(lines, settings=None):
    """Per page unit: normal line pitch and the local left margin, read from line positions in points."""
    pitch_min = settings.pitch_min_pt if settings else PITCH_MIN_PT
    pitch_max = settings.pitch_max_pt if settings else PITCH_MAX_PT
    stats = {}
    for u in {l["unit"] for l in lines}:
        mine = [l for l in lines if l["unit"] == u and l["x"] is not None]
        pitch = line_pitch([b["y"] - a["y"] for a, b in zip(mine, mine[1:])], pitch_min=pitch_min, pitch_max=pitch_max)
        stats[u] = {"pitch": pitch if pitch is not None else 13.0, "lines": mine}
    return stats


def local_margin(stats, ln):
    """Left margin near the first line of a unit: the leftmost of its next few lines (the page may be skewed)."""
    near = [l["x"] for l in stats[ln["unit"]]["lines"] if l is not ln and 0 < l["y"] - ln["y"] < 90]
    return min(near) if near else ln["x"]


def starts_paragraph(prev, ln, stats, settings=None):
    """True if the line opens a paragraph. Evidence is geometric: indented relative to the line before it, or
    set lower than normal line spacing. A line with no position, or one that begins in lower case, never starts one."""
    indent_pt = settings.indent_pt if settings else INDENT_PT
    gap_pt = settings.gap_pt if settings else GAP_PT
    if ln["x"] is None or ln["text"][:1].islower():
        return False
    if prev is not None and prev["unit"] == ln["unit"] and prev["x"] is not None:
        return ln["x"] - prev["x"] >= indent_pt or ln["y"] - prev["y"] >= stats[ln["unit"]]["pitch"] + gap_pt
    return ln["x"] - local_margin(stats, ln) >= indent_pt


def paragraphs_from(lines, inline, notes, settings=None):
    """Group lines into paragraphs and repair drop caps. With line positions (the normal case) a paragraph break is
    an indent or a vertical gap; without them (older callers) it is the layout indent of the first line."""
    indent_pt = settings.indent_pt if settings else INDENT_PT
    kept, breaks = [], set()                    # a section break (ornament line) ends the paragraph before it
    for l in lines:
        if l.get("ornament"):
            breaks.add(len(kept))               # index of the first line after the break
        else:
            kept.append(l)
    lines = kept
    if not lines:
        return []
    geometric = any(l.get("x") is not None for l in lines)
    stats = unit_stats(lines, settings=settings) if geometric else None
    base = min(l["indent"] for l in lines)
    i, paras, cur, cap_indent, wrap, prev = 0, [], None, None, False, None
    while i < len(lines):
        if i in breaks:
            cur, wrap, prev = None, False, None
        ln = dict(lines[i])
        is_cap = split_dropcap(lines, i)
        same = (SAMELINE_CAP.match(ln["text"]) or lower_dropcap(ln["text"])) if not is_cap else None
        if geometric:
            if wrap and ln["x"] is not None and ln["x"] - local_margin(stats, ln) >= indent_pt and not same:
                is_start = False           # still beside the drop cap
            else:
                wrap = False
                is_start = cur is None or starts_paragraph(prev, ln, stats, settings=settings)
        else:
            is_start = ln["indent"] - base >= 1 and not (cap_indent is not None and ln["indent"] == cap_indent)
        wide = same is not None and same.group(2).islower() and re.match(r"[A-Z]\s{2,}", ln["text"]) is not None
        if same and (cur is None or is_start or wide):     # a wide-set drop cap opens a paragraph even right under a subheading
            letter, rest = same.group(1), ln["text"][same.end(1):].strip()
            first = same.group(2)
            if first.islower() or known(letter + first) or not (letter in "AI" and known(first)):
                merged, how = letter + rest, "joined"
            else:
                merged, how = letter + " " + rest, "kept as its own word"
            notes.append(("dropcap", ln["where"], "drop cap '%s' + '%s' on one line: %s" % (letter, rest[:12], how)))
            ln.update(text=merged, indent=base + 1, smallcaps=True)
            cur = [ln]
            paras.append(cur)
            wrap, prev = True, lines[i]
            i += 1
            continue
        if is_cap:
            letter, frag = ln["text"], lines[i + 1]
            first = re.match(r"[A-Z]+", frag["text"]).group(0)
            if known(letter + first) or not (letter in "AI" and known(first)):
                merged = letter + frag["text"]
                how = "joined"
            else:
                merged = letter + " " + frag["text"]
                how = "kept as its own word"
            notes.append(("dropcap", ln["where"], "drop cap '%s' + '%s': %s" % (letter, frag["text"][:12], how)))
            ln = dict(frag, text=merged, indent=base + 1, where=ln["where"], smallcaps=True)
            cap_indent = frag["indent"]
            i += 2
            cur = [ln]
            paras.append(cur)
            wrap, prev = True, frag
            continue
        if ln["indent"] != cap_indent:
            cap_indent = None
        if is_start or cur is None:
            cur = [ln]
            paras.append(cur)
        else:
            cur.append(ln)
        prev = lines[i]
        i += 1
    return paras


def is_table(text):
    """A Markdown table from the vision model: every line is a row that starts with '|'."""
    return text.startswith("|") and all(ln.strip().startswith("|") for ln in text.split("\n"))


FOOTNOTE_DEF_RE = re.compile(r"^\[\^[^\]\s]+\]:")


def join_lines(para, inline, notes):
    if all(ln["text"].startswith("|") for ln in para):
        return "\n".join(re.sub(r"\s+", " ", ln["text"]).strip() for ln in para)     # table rows stay rows
    text = para[0]["text"]
    for ln in para[1:]:
        nxt = ln["text"]
        if text.endswith(pdftext.SOFT_HYPHEN):
            text = text[:-1] + nxt                   # a soft hyphen marks a word break: the halves are one word
            continue
        m = re.search(r"([A-Za-z]+(?:-[A-Za-z]+)*)-$", text)
        f = re.match(r"([a-z]+)(.*)$", nxt)
        if m and f:
            joined, reason, ok = join_hyphen(m.group(1), f.group(1), inline)
            notes.append(("hyphen-joined" if ok else "hyphen-unresolved", ln["where"],
                          "'%s-' + '%s' -> '%s' (%s)" % (m.group(1), f.group(1), joined, reason)))
            text = text[:m.start()] + joined + f.group(2)
        else:
            text = text + " " + nxt
    return re.sub(r"\s+", " ", text.replace(pdftext.SOFT_HYPHEN, "")).strip()


ELISION = {"Tis", "Twas", "Twere", "Twill", "Twould"}
CAPTION_MIXED_RE = re.compile(r"^\s*(?:Left|Right|Above|Below|Opposite|Facing)\s*:\s*[A-Z][A-Z .,'\-]+\s*$")
MIDLINE_HYPHEN_RE = re.compile(r"\b([A-Za-z]+)- ([a-z]+)\b")
LABEL_RE = re.compile(r"\s\d\.\s+[A-Z]{3,}(?:\s+[A-Z]{3,})*(?=\s+[a-z])")
DIGIT_ONE_RE = re.compile(r"(?<![\d.,$#/-])\b1 (?=[a-z]+\b)")


def strip_inline_labels(text, where, notes):
    """The book sets '5. JOY' style labels into the middle of a sentence (a typeset inset, not words of the sentence).
    Left in, they would be read aloud inside the sentence, so they are removed and logged."""
    def drop(m):
        notes.append(("inline-label-removed", where, "typeset label '%s' sat inside a sentence" % m.group(0).strip()))
        return ""
    return LABEL_RE.sub(drop, text)


def fix_midline_hyphens(text, where, notes, inline):
    """'worth- while' inside a line: the scan kept a line-end hyphen after the line was re-flowed.
    Joined only when the answer is certain (the hyphenated form occurs elsewhere in the book, or the joined
    word is a dictionary word). Otherwise logged and left as it is."""
    def fix(m):
        joined, reason, ok = join_hyphen(m.group(1), m.group(2), inline)
        certain = ok and (reason.startswith("hyphenated form") or reason.startswith("joined word"))
        notes.append(("hyphen-joined" if certain else "hyphen-unresolved", where,
                      "mid-line '%s- %s' -> %s (%s)" % (m.group(1), m.group(2), "'%s'" % joined if certain else "not changed", reason)))
        return joined if certain else m.group(0)
    return MIDLINE_HYPHEN_RE.sub(fix, text)


def merge_orphan_quotes(paras, chapter, notes):
    """[(text, where)] -> same, where a paragraph that opens with a lone quote mark and a lower-case word is
    joined to the paragraph before it. No paragraph starts like that: the page broke the line after an opening
    quoted word, as in: They "milked'  /  " every operation. The stray ' closing the quoted word is made a '"'."""
    out = []
    for text, where in paras:
        if out and re.match(r'"\s+[a-z]', text):
            prev, prev_where = out[-1]
            fixed = prev
            if prev.count('"') % 2 == 1 and re.search(r"[A-Za-z]'$", prev):
                fixed = prev[:-1] + '"'
            out[-1] = (fixed + " " + text[1:].strip(), prev_where)
            notes.append(("paragraph-joined", where, "paragraph began with a lone quote mark and a lower-case word, "
                          "so it continues the paragraph before it ('...%s' + '%s...')" % (prev[-14:], text[:14])))
        else:
            out.append((text, where))
    return out


SENTENCE_END_RE = re.compile('[.!?:;][\'"\u2018\u2019\u201c\u201d)\]]*$')
OPEN_END_RE = re.compile(r"[a-z,\-—]$")
MID_SENTENCE_MIN_WORDS = 6     # shorter lines without an end mark are headings, titles or labels (default)


def is_title_case(text):
    """'The Nag Hammadi Library', 'Translated by Frederik Wisse': most words start with a capital."""
    words = [w for w in re.findall(r"[^\W\d_]+", text) if len(w) > 3]
    return bool(words) and sum(w[0].isupper() for w in words) * 2 > len(words)


def merge_mid_sentence(paras, notes, min_words=None):
    """[(text, where)] -> same, where a paragraph that stops in the middle of a sentence is joined to the next one.
    A paragraph never ends inside a sentence: when the one before has no end mark, has at least min_words
    words and ends in a comma, a hyphen or a dash, or in a lower-case letter while not being a title, the break was a layout gap
    (line spacing, a page or column turn), not the author's. Every join is logged."""
    if min_words is None:
        min_words = MID_SENTENCE_MIN_WORDS
    out = []
    for text, where in paras:
        if out and not is_table(text) and not FOOTNOTE_DEF_RE.match(text) and not is_table(out[-1][0]):
            prev, prev_where = out[-1]
            if (not SENTENCE_END_RE.search(prev) and OPEN_END_RE.search(prev)
                    and len(prev.split()) >= min_words
                    and (prev[-1] in ",\u2014" or not is_title_case(prev))):
                sep = "" if prev.endswith(("-", "—")) else " "
                out[-1] = (prev + sep + text, prev_where)
                notes.append(("paragraph-joined", where, "paragraph before it stopped mid-sentence, so this one continues "
                              "it ('...%s' + '%s...')" % (prev[-20:], text[:20])))
                continue
        out.append((text, where))
    return out


def fix_quotes(text, where, notes):
    """Quote marks the scan got wrong. Fixed only when the rule is certain; otherwise the paragraph is logged.
      - an unclosed double quote that ends in punctuation + ' : the ' is a misread closing quote.
      - a paragraph that opens with ' + capital letter (not an elision such as 'Tis): a misread opening
        double quote, as at the start of each paragraph of a long speech.
      - a closing double quote (after punctuation) with no opening quote before it in the paragraph is logged only."""
    if text.count('"') % 2 == 1 and re.search(r"[.?!]'$", text):
        notes.append(("quote-fixed", where, "closing quote misread as apostrophe at '...%s'" % text[-12:]))
        text = text[:-1] + '"'
    if re.match(r"\u2022[A-Z]", text):
        notes.append(("quote-fixed", where, "opening quote misread as a bullet at '%s...'" % text[:16]))
        text = '"' + text[1:]
    m = re.match(r"'([A-Z][a-z]*)", text)
    if m and m.group(1) not in ELISION:
        notes.append(("quote-fixed", where, "opening quote misread as apostrophe at '%s...'" % text[:16]))
        text = '"' + text[1:]
    opened = False
    for q in re.finditer('"', text):
        before = text[q.start() - 1] if q.start() else ""
        if before in ("", " ", "(", "\u2014"):
            opened = True
        elif before == "'":
            opened = not opened
        elif opened:
            opened = False
        else:
            notes.append(("quote-unmatched", where, "closing quote with no opening quote before it: '...%s' (not changed)" % text[max(0, q.start() - 14):q.start() + 1]))
    for m in DIGIT_ONE_RE.finditer(text):
        notes.append(("digit-one", where, "'1' before a lowercase word, probably the letter I misread: '...%s' (not changed)" % text[max(0, m.start() - 8):m.end() + 8]))
    return text


def odd_words(text, where, notes):
    for w in word_re().findall(text):
        core = re.sub(r"'s$", "", w)
        if len(core) > 1 and not known(core):
            notes.append(("odd-word", where, "'%s' is not in the dictionary%s" % (w, " (capitalised: probably a name)" if w[0].isupper() else "")))


class Where(str):
    """A paragraph's place label ('pdf p.3'), which also carries its unit (page, half) and first-line y position."""

    def __new__(cls, label, unit=None, y=None):
        obj = super().__new__(cls, label)
        obj.unit, obj.y = unit, y
        return obj


def run(book_dir, book, pdf, chapter_numbers):
    """Writes text/chapter-NN.txt (and text/chapter-NN.json, where each paragraph starts) for the chosen chapters. Returns warnings."""
    set_language(book.get("language"))
    units = pdftext.units(pdf)
    is_treatise = book.get("type") == "treatise"
    if is_treatise:
        heads = {c["number"]: dict(c, treatise=True) for c in book["chapters"]}
    else:
        heads = {h["number"]: h for h in structure.find_headings(units)}
    running = pageparts.running_lines(units)
    inline = vocabulary(units)
    os.makedirs(os.path.join(book_dir, "text"), exist_ok=True)
    notes = []
    for ch in book["chapters"]:
        if ch["number"] not in chapter_numbers:
            continue
        lines = []
        for ui in range(ch["first_unit"], ch["last_unit"] + 1):
            if is_treatise:
                s_line = ch.get("first_line", 0) if ui == ch["first_unit"] else 0
                e_line = ch.get("last_line") if ui == ch["last_unit"] else None
                lines += unit_lines(units[ui], book["title"], heads[ch["number"]], ui == ch["first_unit"], notes,
                                    running, start_line=s_line, end_line=e_line)
            else:
                lines += unit_lines(units[ui], book["title"], heads[ch["number"]], ui == ch["first_unit"], notes, running)
        normalized = {}
        for ui in range(ch["first_unit"], ch["last_unit"] + 1):
            for c, n in (units[ui].get("normalized") or {}).items():
                normalized[c] = normalized.get(c, 0) + n
        if normalized:
            notes.append(("characters-normalized", "chapter %d" % ch["number"], ", ".join(
                "%d x U+%04X -> %r" % (n, ord(c), pdftext.CHAR_MAP[c]) for c, n in sorted(normalized.items()))))
        paras = paragraphs_from(lines, inline, notes)
        joined = []
        for p in paras:
            t = join_lines(p, inline, notes)
            if p[0].get("smallcaps"):
                before, t = t, smallcaps_lead(t)
                if before != t:
                    notes.append(("smallcaps", p[0]["where"], "'%s...' -> '%s...'" % (before[:14], t[:14])))
            joined.append((t, Where(p[0]["where"], p[0].get("unit"), p[0].get("y"))))
        joined = merge_orphan_quotes(joined, ch["number"], notes)
        joined = merge_mid_sentence(joined, notes)
        texts, places = [], []
        for n, (t, w) in enumerate(joined, start=1):
            places.append({"pdf_page": w.unit[0] if w.unit else None, "half": w.unit[1] if w.unit else None, "y": w.y})
            where = "ch%d para %d" % (ch["number"], n)
            if is_table(t):
                texts.append(t)
                continue
            t = strip_inline_labels(t, where, notes)
            t = fix_midline_hyphens(t, where, notes, inline)
            t = fix_quotes(t, where, notes)
            odd_words(t, where, notes)
            texts.append(t)
        name = "chapter-%02d.txt" % ch["number"]
        with open(os.path.join(book_dir, "text", name), "w", encoding="utf-8") as f:
            f.write("\n\n".join(texts) + "\n")
        with open(os.path.join(book_dir, "text", name[:-4] + ".json"), "w", encoding="utf-8") as f:
            json.dump({"paragraphs": places}, f)     # where each paragraph starts on the page (EPUB figure placement)
            f.write("\n")
        ch["text_file"] = "text/" + name
        ch["words"] = sum(len(t.split()) for t in texts)
        ch["paragraphs"] = len(texts)
    return notes


def normalize_pages(pages: list[dict], title: str = "", author: str = "", settings=None) -> tuple[list[dict], list[tuple]]:
    """Normalize parsed pages: strip headers/footers, repair hyphenations, reconstruct paragraphs.

    Takes pages from parse_pdf() (each with 'page', 'text', 'images', 'ocr_quality') and
    returns (normalized_pages, notes) where each page's 'text' is cleaned and 'paragraphs'
    is a list of clean paragraph strings.

    Args:
        pages: Parsed pages from parse_pdf().
        title: Book title (for header detection).
        author: Book author (unused currently).
        settings: NormalizationSettings instance. If None, uses defaults.

    This is the key function that brings the old pipeline's text normalization into rev2.
    """
    from worker.prep.normalization_settings import NormalizationSettings

    if settings is None:
        settings = NormalizationSettings()

    set_language("eng")

    # Convert pages to units (the old pipeline's format)
    units = []
    for p in pages:
        units.append({
            "pdf_page": p["page"],
            "half": None,
            "text": p["text"],
            "geom": None,
            "normalized": {},
        })

    # Build vocabulary and running headers from all units
    inline = vocabulary(units)
    running = pageparts.running_lines(units, min_units=settings.running_header_min_pages, max_words=settings.running_header_max_words)

    notes = []
    normalized_pages = []

    for unit in units:
        raw = [l.rstrip() for l in unit["text"].split("\n")]
        where = "pdf p.%d" % unit["pdf_page"]

        # Detect and remove running header
        drop = set()
        z = pageparts.header_zone(raw, title, list(running), similarity_threshold=settings.header_title_similarity)
        if z:
            drop.update(range(z[0], z[1]))
            notes.append(("header-removed", where,
                          "running header '%s'" % " ".join(l.strip() for l in raw[z[0]:z[1]])))

        # Detect and remove footer page number
        f = pageparts.footer_number(raw)
        if f:
            drop.add(f[0])
            notes.append(("page-number-removed", where, "footer page number %d" % f[1]))
        else:
            r = pageparts.footer_roman(raw)
            if r:
                drop.add(r[0])
                notes.append(("page-number-removed", where, "footer page number %s (roman)" % r[1]))

        # Build clean lines (skip dropped lines and ornaments)
        lines = []
        for i, ln in enumerate(raw):
            if i in drop or not ln.strip():
                continue
            if pageparts.ORNAMENT_RE.match(ln):
                lines.append({"text": "", "ornament": True, "indent": 0, "where": where,
                              "unit": (unit["pdf_page"], None), "x": None, "y": None})
                continue
            lines.append({"text": ln.strip(), "indent": len(ln) - len(ln.lstrip()), "where": where,
                          "unit": (unit["pdf_page"], None), "x": None, "y": None})

        # Group into paragraphs (uses settings for indent/gap thresholds)
        paras = paragraphs_from(lines, inline, notes, settings=settings)

        # Join lines within each paragraph (handles hyphenation)
        paragraphs = []
        for p in paras:
            t = join_lines(p, inline, notes)
            t = fix_midline_hyphens(t, where, notes, inline)
            t = fix_quotes(t, where, notes)
            t = re.sub(r"\s+", " ", t).strip()
            if t:
                paragraphs.append(t)

        # Normalize characters (soft hyphens, etc.)
        clean_text, changed = pdftext.normalize_chars("\n".join(paragraphs))
        if changed:
            notes.append(("characters-normalized", where,
                          ", ".join("%d x U+%04X" % (n, ord(c)) for c, n in sorted(changed.items()))))

        normalized_pages.append({
            "page": unit["pdf_page"],
            "text": clean_text,
            "paragraphs": paragraphs,
            "images": 0,  # will be filled from original
        })

    # Copy over non-text fields from original pages
    for orig, norm in zip(pages, normalized_pages):
        norm["images"] = orig.get("images", 0)
        norm["ocr_quality"] = orig.get("ocr_quality")
        norm["action"] = orig.get("action")
        norm["reason"] = orig.get("reason")

    return normalized_pages, notes
