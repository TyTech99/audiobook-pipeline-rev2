"""Translation into English. A per-book switch: book.json "translate": true (settings
translation.enabled is the default, off). Scan fixes run first. The source-language EPUB is saved as
book.<language>.epub, then the book is translated chapter by chapter (textjobs.translate: chunk by chunk with the term
list, keeping paragraphs) and text/chapter-NN.txt holds the English text; the source text is kept in text/source/.
The English EPUB is built from it by the epub stage and passes the same checks. book.json "translation" records it,
and the book details say it is a machine translation."""
import hashlib
import os
import shutil

from pipeline.text import pagereader
from pipeline.text import textjobs


def wanted(cfg, book):
    if book.get("translate") is not None:
        return bool(book["translate"])
    return bool((cfg.get("translation") or {}).get("enabled", False))


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def run(cfg, book_dir, book, session, source_epub=None):
    """Translates every chapter text in place (source kept). source_epub: path of the source-language EPUB just built,
    saved beside it. Returns warnings."""
    code = book.get("language") or "eng"
    if code == "eng":
        book.pop("translation", None)
        return [("translation-skipped", "book", "translate is on but the book is already in English")]
    notes = []
    rec = book.get("translation") or {}
    english = rec.get("english_sha256") or {}
    name = pagereader.language_name(code)
    if source_epub and os.path.exists(source_epub):
        saved = "book.%s.epub" % code
        shutil.copyfile(source_epub, os.path.join(book_dir, saved))
        rec["source_epub"] = saved
    os.makedirs(os.path.join(book_dir, "text", "source"), exist_ok=True)
    texts = {}
    for ch in book["chapters"]:
        path = os.path.join(book_dir, ch["text_file"])
        keep = os.path.join(book_dir, "text", "source", os.path.basename(ch["text_file"]))
        if english.get(ch["text_file"]) == _sha(path) and os.path.exists(keep):
            src = keep                       # already English from an earlier run: translate the kept source
        else:
            shutil.copyfile(path, keep)
            src = keep
        with open(src, encoding="utf-8") as f:
            texts[ch["number"]] = [p.strip() for p in f.read().split("\n\n") if p.strip()]
    terms = textjobs.build_terms(session, [p for n in sorted(texts) for p in texts[n]])
    titles = [ch.get("source_title") or ch["title"] for ch in book["chapters"]]
    en_titles = textjobs.translate(session, titles, name, terms, "chapter titles", notes)
    for ch, t_src, t_en in zip(book["chapters"], titles, en_titles):
        ch["source_title"] = t_src
        ch["title"] = t_en.rstrip(".") if not t_src.endswith(".") else t_en
        en = textjobs.translate(session, texts[ch["number"]], name, terms, "chapter %d" % ch["number"], notes)
        path = os.path.join(book_dir, ch["text_file"])
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n\n".join(en) + "\n")
        english[ch["text_file"]] = _sha(path)
        ch["words"] = sum(len(p.split()) for p in en)
    rec.update({"machine": True, "from": code, "to": "eng", "english_sha256": english, "terms": len(terms)})
    book["translation"] = rec
    notes.append(("translation", "book", "machine translation from %s into English, %d chapters, %d listed terms" % (
        name, len(book["chapters"]), len(terms))))
    return notes
