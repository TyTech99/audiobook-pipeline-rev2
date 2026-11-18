"""Narration script. One JSON file per audio file in script/, next to the book's EPUB:
opening-credits, front-matter (when the book has some), chapter-NN (or chapter-NN-part-K for a chapter over the
length limit), closing-credits, retail-sample. book.json "audio_files" lists them in play order and "audio_titles"
gives each its title (file names and MP3 tags, layout.py).

A script is a list of segments {kind, added, text}. The author's text is verbatim (added=false); a small table read row
by row is the author's table in sentences (kind 'table', added=false). Everything else is added=true:
  credits, headers and labels;
  summaries (preview, mid-chapter concept summary, chapter-end summary), each switched on per settings, book or
  chapter, and wrapped in cue chime-start, label, text, cue chime-end;
  figure sentences (a figure the text refers to) and table takeaways (a large table): no chime.
Figures the text does not refer to, large tables and parts whose model answer failed its checks are listed in
figures/skipped_figures.md.

A book with no chapter headings (structure.py 'synthetic'): no chapter is invented. Its spoken header is the title
alone. Front matter (a translator's note, a preface) is its own labelled section and file. Text longer than
cfg script.max_minutes is split at paragraph breaks into near-equal parts, spoken as 'Part One' and so on."""
import json
import math
import os
import re

from pipeline.book import bookio
from pipeline.voice import spoken
from pipeline.text import textjobs
from pipeline.text import textmodel

LABELS = {"chapter_preview": "Chapter preview", "chapter_summary": "Chapter summary", "concept_summary": "Concept summary"}
SUMMARIES = ("chapter_preview", "concept_summary", "chapter_summary")
# A front-matter heading as the first paragraph of a book with no chapter headings.
FRONT_RE = re.compile(r"^(translator'?s?'? (note|introduction|preface)|preface|foreword|introduction|editor'?s? note|"
                      r"introductory note|note)\.?$", re.I)
# A heading-like paragraph that ends the front matter: short, no sentence end, mostly capitalized words.
HEADING_MAX_WORDS = 10


class ScriptError(Exception):
    kind = "permanent"


def switch(cfg, book, chapter, name):
    """Chapter switch beats book switch beats settings default."""
    for source in ((chapter or {}).get("switches", {}), book.get("summaries", {})):
        if source.get(name) is not None:
            return bool(source[name])
    return bool(cfg["summaries"][name])


def seg(kind, text, added=True):
    return {"kind": kind, "added": added, "text": text}


def inserted(kind, text):
    return [seg("cue", "chime-start"), seg("label", LABELS[kind]), seg(kind, text), seg("cue", "chime-end")]


def chapter_title(book, ch):
    """The chapter's title as spoken and as its track title."""
    if ch.get("synthetic"):
        return book["title"]
    if book.get("type") == "treatise" and ch.get("toc_page") is not None:
        key = ch["title"].casefold()
        if sum(1 for c in book.get("chapters", []) if c["title"].casefold() == key) > 1:
            return "%s (p. %d)" % (ch["title"], ch["toc_page"])
    return ch["title"]


def spoken_header(book, ch, part=None):
    if ch.get("synthetic"):                   # no chapter headings: the title alone, never an invented chapter
        head = "%s." % book["title"].rstrip(".")
    elif book.get("type") == "treatise":      # treatise section: spoken as printed, never 'Chapter N'
        t = ch["title"].strip()
        head = t if t.endswith((".", "?", "!")) else "%s." % t
    elif ch.get("label"):                     # the book's own heading, e.g. 'The First Lesson. The Coming of the Master.'
        head = "%s. %s." % (ch["label"], ch["title"].rstrip("."))
    else:
        head = "Chapter %s. %s." % (spoken.number_word(ch["number"]), ch["title"])
    if part:
        head += " Part %s." % spoken.number_word(part)
    return head


def credits(book, cfg, closing):
    by = "written by %s" % book["author"]
    if cfg.get("narrator"):
        by += ", narrated by %s" % cfg["narrator"]
    text = ("This is the end of %s, %s." if closing else "This is %s, %s.") % (book["title"], by)
    if not closing and (book.get("translation") or {}).get("machine"):
        text += " This is a machine translation into English."
    return [seg("credits", text)]


# ---------- the chapter content ----------

def chapter_blocks(cfg, book_dir, book, pdf, notes):
    """{chapter number: blocks} (epub.py's content model: p, table, figure, footnote), with figures in place."""
    from pipeline.book import epub
    chs = epub.epub_source_chapters(book_dir, book) if book.get("source_epub") else epub.pdf_chapters(book_dir, book, pdf, notes)
    return {c["number"]: c["blocks"] for c in chs}


def table_text(b):
    rows = ["| " + " | ".join(r) + " |" for r in b["head"]]
    if b["head"]:
        rows.append("| " + " | ".join("---" for _ in b["head"][-1]) + " |")
    rows += ["| " + " | ".join(r) + " |" for r in b["body"]]
    return "\n".join(rows)


def block_words(b):
    if b["type"] in ("p", "footnote"):
        return len(b["text"].split())
    if b["type"] == "table":
        return len(spoken.read_table(table_text(b)).split())
    return 0


class Ask:
    """The model jobs one script run makes, on one session (made on first use: a book with nothing to ask starts
    no model). required=False parts are skipped and listed when the model settings are not complete."""

    def __init__(self, cfg, book_dir, session):
        self.cfg, self.book_dir, self.session, self.own = cfg, book_dir, session, False

    def ses(self):
        if self.session is None:
            self.session = textmodel.Session(self.cfg, self.book_dir)
            self.own = True
        return self.session

    def close(self):
        if self.own and self.session is not None:
            self.session.close()


def figure_part(ask, ch, blocks, i, refs, where, notes, skipped):
    b = blocks[i]
    label = spoken.figure_label(b.get("caption"))
    if label is None or label not in refs:
        skipped.append((where, "figure", b.get("caption") or "no caption", "decorative: the text does not refer to it"))
        return []
    near = [x["text"] for x in blocks[max(0, i - 2):i + 3] if x["type"] == "p"]
    try:
        out = textjobs.figure_sentence(ask.ses(), b["data"], b["media"], b.get("caption"), "\n\n".join(near), where, notes)
    except textmodel.ModelSettingsError as e:
        skipped.append((where, "figure", b.get("caption"), "model settings not complete: %s" % e))
        return []
    if out is None:
        skipped.append((where, "figure", b.get("caption"), "the figure sentence failed its checks (warnings.log)"))
        return []
    return [seg("figure_sentence", out)]


def table_part(ask, b, where, notes, skipped):
    text = table_text(b)
    rows, cols = len(b["body"]), max([len(r) for r in b["head"] + b["body"]] or [0])
    if not spoken.is_large(text):
        return [seg("table", spoken.read_table(text), added=False)]
    reason = "large table (%d rows, %d columns): 1 to 2 takeaway sentences spoken instead" % (rows, cols)
    try:
        out = textjobs.table_takeaway(ask.ses(), text, where, notes)
    except textmodel.ModelSettingsError as e:
        out, reason = None, "large table (%d rows, %d columns); model settings not complete: %s" % (rows, cols, e)
    if out is None and "settings" not in reason:
        reason = "large table (%d rows, %d columns); the takeaway failed its checks (warnings.log)" % (rows, cols)
    skipped.append((where, "table", "%d rows, %d columns" % (rows, cols), reason))
    return [seg("table_takeaway", out)] if out else []


def body_segments(ask, ch, blocks, notes, skipped):
    """[(segments for one block, words)] in reading order, plus the paragraphs (model input)."""
    refs = spoken.figure_refs(" ".join(b["text"] for b in blocks if b["type"] == "p"))
    out, paras = [], []
    nfig = ntab = 0
    for i, b in enumerate(blocks):
        if b["type"] in ("p", "footnote"):
            out.append(([seg("author", b["text"], added=False)], block_words(b)))
            paras.append(b["text"])
        elif b["type"] == "table":
            ntab += 1
            segs = table_part(ask, b, "chapter %d table %d" % (ch["number"], ntab), notes, skipped)
            out.append((segs, sum(len(s["text"].split()) for s in segs)))
        elif b["type"] == "figure":
            nfig += 1
            out.append((figure_part(ask, ch, blocks, i, refs, "chapter %d figure %d" % (ch["number"], nfig), notes, skipped), 0))
    return out, paras


def summary(ask, cfg, book, ch, name, paragraphs, notes, terms):
    if not switch(cfg, book, ch, name):
        return None, terms
    where = "chapter %d %s" % (ch["number"], name)
    try:
        return textjobs.summary(ask.ses(), name, paragraphs, where, notes, terms,
                                translated=bool((book.get("translation") or {}).get("machine")))
    except textmodel.ModelSettingsError as e:
        raise ScriptError("%s is switched on but the model settings are not complete: %s" % (name, e))


def split_parts(items, max_words):
    """Near-equal parts of whole items (paragraph breaks) when the words exceed max_words. [[items]]."""
    total = sum(w for _, w in items)
    n = max(1, math.ceil(total / max_words)) if max_words else 1
    if n == 1:
        return [items]
    parts, cur, done = [], [], 0
    for it in items:
        cur.append(it)
        done += it[1]
        if len(parts) < n - 1 and done >= total * (len(parts) + 1) / n:
            parts.append(cur)
            cur = []
    if cur:
        parts.append(cur)
    return parts


def max_words(cfg):
    s = cfg.get("script") or {}
    return int(float(s.get("max_minutes", 120)) * float(s.get("words_per_minute", 155)))


def chapter_files(ask, cfg, book, ch, blocks, notes, skipped, terms):
    """[(name, title, segments)] for one chapter: one file, or parts over the length limit."""
    items, paras = body_segments(ask, ch, blocks, notes, skipped)
    preview, terms = summary(ask, cfg, book, ch, "chapter_preview", paras, notes, terms)
    mid_at, concept = None, None
    if switch(cfg, book, ch, "concept_summary"):
        total, run = sum(len(p.split()) for p in paras), 0
        for i, p in enumerate(paras):
            run += len(p.split())
            if run * 2 >= total:
                mid_at = i
                break
        concept, terms = summary(ask, cfg, book, ch, "concept_summary", paras[:mid_at + 1], notes, terms)
    end, terms = summary(ask, cfg, book, ch, "chapter_summary", paras, notes, terms)
    k = -1                                     # index of the author paragraph seen so far
    marked = []
    for segs, w in items:
        extra = []
        if segs and segs[0]["kind"] == "author":
            k += 1
            if k == mid_at and concept:
                extra = inserted("concept_summary", concept)
        marked.append((segs + extra, w))
    parts = split_parts(marked, max_words(cfg))
    title = chapter_title(book, ch)
    files = []
    for pi, part in enumerate(parts, 1):
        np = pi if len(parts) > 1 else None
        segs = [seg("header", spoken_header(book, ch, np))]
        if pi == 1 and preview:
            segs += inserted("chapter_preview", preview)
        for s, _ in part:
            segs += s
        if pi == len(parts) and end:
            segs += inserted("chapter_summary", end)
        name = "chapter-%02d" % ch["number"] + ("-part-%d" % pi if np else "")
        files.append((name, title + (", Part %s" % spoken.number_word(pi) if np else ""), segs))
    if len(parts) > 1:
        notes.append(("chapter-split", "chapter %d" % ch["number"], "%d words is over %d (%s minutes at %s words a minute): "
                      "split into %d parts at paragraph breaks" % (sum(w for _, w in items), max_words(cfg),
                      (cfg.get("script") or {}).get("max_minutes", 120), (cfg.get("script") or {}).get("words_per_minute", 155), len(parts))))
    return files, terms


def front_matter(book, ch, blocks, notes):
    """(label, front blocks, rest) for a book with no chapter headings whose text opens with a front-matter heading
    (or whose book.json sets front_matter: {label, paragraphs}). Otherwise (None, [], blocks)."""
    fm = book.get("front_matter") or {}
    paras = [i for i, b in enumerate(blocks) if b["type"] == "p"]
    if fm.get("paragraphs"):
        n = int(fm["paragraphs"])
        if n >= len(paras):
            raise ScriptError("book.json front_matter.paragraphs is %d but the text has %d paragraphs" % (n, len(paras)))
        cut = paras[n]
        return fm.get("label") or "Front Matter", blocks[:cut], blocks[cut:]
    if not ch.get("synthetic") or not paras or not FRONT_RE.match(blocks[paras[0]]["text"].strip()):
        return None, [], blocks
    label = blocks[paras[0]]["text"].strip().rstrip(".")
    for i in paras[1:]:
        t = blocks[i]["text"].strip()
        if len(t.split()) <= HEADING_MAX_WORDS and not re.search(r"[.!?,;:]$", t) and t[:1].isupper():
            notes.append(("front-matter", "book", "'%s' read as its own section up to the heading '%s'" % (label, t)))
            return label, blocks[paras[0] + 1:i], blocks[i:]
    notes.append(("front-matter-no-end", "book", "the text opens with '%s' but no later heading ends it; read as one text" % label))
    return None, [], blocks


def retail_sample(cfg, book, ch, segs_all):
    budget, words = cfg["script"]["retail_sample_words"], 0
    segs = [seg("header", spoken_header(book, ch))]
    for s in segs_all:
        if s["kind"] != "author":
            continue
        n = len(s["text"].split())
        if segs[1:] and words + n > budget:
            break
        segs.append(s)
        words += n
    return segs


def unique_titles(pairs):
    """{name: title}; a title used again gets " (2)", " (3)" and so on, so every published file name differs."""
    seen, out = {}, {}
    for n, t in pairs:
        k = t.casefold()
        seen[k] = seen.get(k, 0) + 1
        out[n] = t if seen[k] == 1 else "%s (%d)" % (t, seen[k])
    return out


def write(book_dir, name, segs):
    os.makedirs(os.path.join(book_dir, "script"), exist_ok=True)
    with open(os.path.join(book_dir, "script", name + ".json"), "w", encoding="utf-8") as f:
        json.dump(segs, f, indent=2, ensure_ascii=False)
        f.write("\n")


def write_skipped(book_dir, chapter_numbers, skipped):
    out = ["# Skipped figures and large tables", "",
           "A figure the text refers to gets one spoken sentence; a decorative one is skipped. A small table is read row",
           "by row; a large one (more than %d rows or more than %d columns) gets one or two takeaway sentences and is" % (
               spoken.TABLE_MAX_ROWS, spoken.TABLE_MAX_COLUMNS),
           "listed here. Scope: chapters %s." % ", ".join(str(n) for n in sorted(chapter_numbers)), ""]
    for where, kind, what, reason in skipped:
        out.append("- %s (%s): %s. %s." % (where, kind, what, reason.rstrip(".")))
    if not skipped:
        out.append("Nothing skipped in these chapters.")
    os.makedirs(os.path.join(book_dir, "figures"), exist_ok=True)
    with open(os.path.join(book_dir, "figures", "skipped_figures.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def run(cfg, book_dir, book, chapter_numbers, pdf=None, session=None):
    """Writes script/*.json, figures/skipped_figures.md and the audio file list and titles in book.json.
    session: a textmodel.Session shared with the book's other text work (process.py), else one is made if needed.
    Returns warnings."""
    notes, skipped = [], []
    if pdf is None and book.get("source_pdf"):
        pdf = os.path.join(book_dir, book["source_pdf"])
    blocks = chapter_blocks(cfg, book_dir, book, pdf, notes)
    ask = Ask(cfg, book_dir, session)
    files = [("opening-credits", "Opening Credits", credits(book, cfg, False))]
    chosen = [c for c in book["chapters"] if c["number"] in chapter_numbers]
    terms, sample = None, None
    try:
        for ch in chosen:
            bl = blocks[ch["number"]]
            if ch is book["chapters"][0]:
                label, front, bl = front_matter(book, ch, bl, notes)
                if label:
                    segs = [seg("header", label.rstrip(".") + ".")]
                    for s, _ in body_segments(ask, ch, front, notes, skipped)[0]:
                        segs += s
                    files.append(("front-matter", label, segs))
            got, terms = chapter_files(ask, cfg, book, ch, bl, notes, skipped, terms)
            files += got
            if ch is chosen[0]:
                sample = ("retail-sample", "Retail Sample", retail_sample(cfg, book, ch, [s for _, _, segs in got for s in segs]))
    finally:
        ask.close()
    files += [("closing-credits", "Closing Credits", credits(book, cfg, True)), sample]
    for name, _, segs in files:
        write(book_dir, name, segs)
    write_skipped(book_dir, chapter_numbers, skipped)
    book["audio_files"] = [n for n, _, _ in files]
    book["audio_titles"] = unique_titles([(n, t) for n, t, _ in files])
    return notes
