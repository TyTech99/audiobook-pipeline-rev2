"""Deterministic text rules for the narration script. No model calls here.

  - code checks on every written part (summaries, table takeaways, figure sentences, book description, translations):
    sentence count, length, no new names, no first-person "I", no Markdown or symbols, translations keep paragraphs;
  - tables: parsed from the Markdown form the pipeline writes; large = more than 4 rows or more than 3 columns; a small
   table is read row by row as sentences;
  - figure references in the text ("see Figure 3", "Fig. 2", "Plate IV").
"""
import re

from worker.prep import structure

# The check limits per job. Sentence counts and word limits are the instruction files' own (prompts/*.md).
LIMITS = {
    "chapter_preview": {"min_sentences": 2, "max_sentences": 2, "max_words": 70},
    "concept_summary": {"min_sentences": 2, "max_sentences": 2, "max_words": 70},
    "chapter_summary": {"min_sentences": 3, "max_sentences": 3, "max_words": 100},
    "table_takeaway": {"min_sentences": 1, "max_sentences": 2, "max_words": 60},
    "figure_sentence": {"min_sentences": 1, "max_sentences": 1, "max_words": 40},
    "book_description": {"min_sentences": 3, "max_sentences": 5, "max_words": 120},
}
# Large table: above 4 rows or above 3 columns. Rows count the body rows, not the header.
TABLE_MAX_ROWS = 4
TABLE_MAX_COLUMNS = 3

FIRST_PERSON = re.compile(r"\b(I|I'm|I've|I'd|I'll|me|my|mine|myself)\b")
MARKDOWN = re.compile(r"(^\s*[-*+]\s|^\s*\d+[.)]\s|^#|\*\*|__|`|^\s*\||\[.*\]\(.*\))", re.M)
SYMBOLS = re.compile(r"[#*_|<>{}\[\]~^=+@\\/%&$]")
ABBREV = re.compile(r"\b(Mr|Mrs|Dr|St|Prof|Rev|Vol|No|vs|etc|e\.g|i\.e|Fig)\.")


def sentences(text):
    t = ABBREV.sub(lambda m: m.group(1) + "<dot>", text.strip())
    return [s.replace("<dot>", ".") for s in re.split(r"(?<=[.!?])[\"')\]\u201d\u2019]*\s+(?=[\"'(\[\u201c\u2018]?[A-Z0-9])", t) if s.strip()]


def capitalized(text):
    return set(re.findall(r"\b[A-Z][a-z]{2,}\b", text))


def new_names(out, source, allowed=()):
    """Capitalized words in `out` that the source does not contain, leaving out a sentence's first word when it is
    an ordinary word (lower case somewhere in the source or in the answer itself)."""
    low = source.lower()
    found = set()
    for s in sentences(out):
        first = re.match(r"\W*([A-Za-z]+)", s)
        for w in capitalized(s):
            if first and w == first.group(1):
                continue
            found.add(w)
    allowed = {a.lower() for a in allowed}
    return sorted(w for w in found if w.lower() not in low and w.lower() not in allowed)


def check(job, out, source, allowed=()):
    """{name: value} plus 'pass' and 'failed' (the names of the failed checks)."""
    lim = LIMITS[job]
    sents = sentences(out)
    names = new_names(out, source, allowed)
    c = {"sentences": len(sents), "words": len(out.split()), "new_names": names,
         "sentences_ok": lim["min_sentences"] <= len(sents) <= lim["max_sentences"],
         "length_ok": len(out.split()) <= lim["max_words"],
         "no_first_person": not FIRST_PERSON.search(out), "no_markdown": not MARKDOWN.search(out),
         "no_symbols": not SYMBOLS.search(out), "single_paragraph": "\n" not in out.strip(), "no_new_names": not names}
    c["failed"] = [k for k, v in c.items() if (k.endswith("_ok") or k.startswith("no_") or k == "single_paragraph") and not v]
    c["pass"] = not c["failed"]
    return c


def paragraphs(text):
    return [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]


def check_translation(out, source):
    """A translated chunk keeps every paragraph and has no Markdown."""
    n_in, n_out = len(paragraphs(source)), len(paragraphs(out))
    c = {"paragraphs_in": n_in, "paragraphs_out": n_out, "paragraphs_ok": n_in == n_out,
         "no_markdown": not re.search(r"(^#|\*\*|`|\[.*\]\(.*\))", out, re.M)}
    c["failed"] = [k for k in ("paragraphs_ok", "no_markdown") if not c[k]]
    c["pass"] = not c["failed"]
    return c


# ---------- tables ----------

def is_table(para):
    lines = [l for l in para.split("\n") if l.strip()]
    return len(lines) >= 2 and all(l.strip().startswith("|") for l in lines)


def parse_table(para):
    """(head, body): lists of rows of cells from a Markdown table (head empty when there is no rule line)."""
    rows = [[c.strip() for c in l.strip().strip("|").split("|")] for l in para.split("\n") if l.strip()]
    rule = next((i for i, r in enumerate(rows) if r and all(re.fullmatch(r":?-{2,}:?", c) for c in r if c)), None)
    if rule is None:
        return [], rows
    return rows[:rule], rows[rule + 1:]


def table_size(para):
    head, body = parse_table(para)
    cols = max([len(r) for r in head + body] or [0])
    return len(body), cols


def is_large(para):
    rows, cols = table_size(para)
    return rows > TABLE_MAX_ROWS or cols > TABLE_MAX_COLUMNS


def read_table(para):
    """A small table read row by row: 'Header one: cell, header two: cell.' (no header: the cells joined)."""
    head, body = parse_table(para)
    names = head[-1] if head else []
    out = []
    for r in body:
        cells = [c for c in r]
        if names and len(names) == len(cells):
            parts = ["%s: %s" % (n, c) if n else c for n, c in zip(names, cells) if c]
        else:
            parts = [c for c in cells if c]
        if parts:
            out.append(", ".join(parts).rstrip(".") + ".")
    return " ".join(out)


# ---------- figures ----------

FIGURE_REF = re.compile(r"\b(?:see\s+)?(Fig(?:ure|\.)?|Plate|Illustration|Diagram)\s+([0-9]+|[IVXLC]+)\b", re.I)


def figure_refs(text):
    """{'figure 3', 'plate iv', ...} referred to in the text."""
    kind = lambda k: "figure" if k.lower().startswith("fig") else k.lower()
    return {"%s %s" % (kind(m.group(1)), m.group(2).lower()) for m in FIGURE_REF.finditer(text)}


def figure_label(caption):
    """'figure 3' from a caption that starts 'Fig. 3.' or 'Figure 3:' (None when the caption has no number)."""
    m = FIGURE_REF.match((caption or "").strip())
    if not m:
        return None
    return next(iter(figure_refs(m.group(0))))


# ---------- spoken numbers ----------

def number_word(n):
    return structure.WORDS[n - 1].title() if 1 <= n <= len(structure.WORDS) else str(n)
