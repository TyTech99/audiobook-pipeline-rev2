"""Scan fixes. Cleanup flags every word its dictionary does not know (warnings.log 'odd-word'). On pages a
scan read (page reading tier tesseract or vision; a good text layer uses none of this), the vision model is shown the
page image and asked how each flagged word is printed. A fix is applied when the printed word differs from the text:
so old spellings, foreign words and names, printed as they stand, stay. Every fix is logged with page, before and
after in scan_fixes.md and warnings.log."""
import json
import os
import re

from pipeline.text import cleanup
from pipeline.text import pagereader
from pipeline.text import textmodel

WORD_RE = re.compile(r"[A-Za-z\u00C0-\u024F][A-Za-z\u00C0-\u024F'\-]*")
ANSWER_RE = re.compile(r"^[A-Za-z\u00C0-\u024F][A-Za-z\u00C0-\u024F'\-]*$")
CONTEXT_WORDS = 8
FIXES_NAME = "scan_fixes.md"


def scanned_pages(book_dir):
    path = os.path.join(book_dir, pagereader.PAGES_NAME)
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as f:
        recs = json.load(f).get("pages", [])
    return {r["page"] for r in recs if r.get("tier") in ("tesseract", "vision")}


def flagged(text):
    """[(start, end, word)] of the words cleanup's dictionary does not know (same rule as cleanup.odd_words)."""
    out = []
    for m in WORD_RE.finditer(text):
        core = re.sub(r"'s$", "", m.group(0).strip("'-"))
        if len(core) > 1 and not cleanup.known(core):
            out.append((m.start(), m.end(), m.group(0)))
    return out


def answer_word(out):
    w = out.strip().strip('"\'\u201c\u201d\u2018\u2019.,;:!?')
    if w.upper() == "UNCLEAR" or not ANSWER_RE.match(w):
        return None
    return w


def run(cfg, book_dir, book, pdf, session, chapter_numbers):
    """Applies scan fixes to text/chapter-NN.txt for the chosen chapters. Returns warnings."""
    pages = scanned_pages(book_dir)
    notes, fixes = [], []
    if not pages:
        return notes
    cleanup.set_language(book.get("language"))
    dpi = pagereader.gate(cfg)["dpi"]
    images = {}
    for ch in book["chapters"]:
        if ch["number"] not in chapter_numbers or not ch.get("text_file"):
            continue
        path = os.path.join(book_dir, ch["text_file"])
        with open(path, encoding="utf-8") as f:
            paras = f.read().split("\n\n")
        places_path = path[:-4] + ".json"
        places = []
        if os.path.exists(places_path):
            with open(places_path, encoding="utf-8") as f:
                places = json.load(f).get("paragraphs", [])
        changed = False
        for i, para in enumerate(paras):
            page = places[i].get("pdf_page") if i < len(places) else None
            if page not in pages or para.lstrip().startswith("|"):
                continue
            edits = []
            for start, end, word in flagged(para):
                words = para[:start].split()[-CONTEXT_WORDS:] + [word] + para[end:].split()[:CONTEXT_WORDS]
                if page not in images:
                    images[page] = pagereader.render(pdf, page, dpi)
                user, uv = textmodel.user_text("scan_fix", word=word, context=" ".join(words))
                got = answer_word(session.ask("scan_fix", user, images=[(images[page], "image/png")],
                                              extra_inputs={"user_template": uv})["output"])
                if got and got != word:
                    edits.append((start, end, word, got))
            for start, end, word, got in reversed(edits):
                para = para[:start] + got + para[end:]
            for _, _, word, got in edits:
                fixes.append((page, ch["number"], word, got))
                notes.append(("scan-fix", "pdf p.%d ch%d para %d" % (page, ch["number"], i + 1), "'%s' -> '%s' (vision model read the page image)" % (word, got)))
            if edits:
                paras[i] = para
                changed = True
        if changed:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n\n".join(paras))
    lines = ["# Scan fixes", "", "Words the dictionary did not know on scanned pages, checked by the vision model against",
             "the page image. Only words printed differently from the scan's reading were changed.", "",
             "| PDF page | Chapter | Before | After |", "| --- | --- | --- | --- |"]
    lines += ["| %d | %d | %s | %s |" % f for f in fixes]
    with open(os.path.join(book_dir, FIXES_NAME), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    book["scan_fixes"] = len(fixes)
    return notes
