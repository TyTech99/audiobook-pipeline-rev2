"""Small helpers shared by the stages: book.json, warnings.log, slugs."""
import json
import os
import re

WARN_NAME = "warnings.log"


def slug(text):
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text


def read_book(book_dir):
    with open(os.path.join(book_dir, "book.json"), encoding="utf-8") as f:
        return json.load(f)


def write_book(book_dir, book):
    with open(os.path.join(book_dir, "book.json"), "w", encoding="utf-8") as f:
        json.dump(book, f, indent=2, ensure_ascii=False)
        f.write("\n")


def write_warnings(book_dir, stage, entries):
    """Replace this stage's lines in warnings.log. entry = (kind, where, message).
    Line format: stage | kind | where | message"""
    path = os.path.join(book_dir, WARN_NAME)
    keep = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            keep = [ln for ln in f.read().splitlines() if ln and not ln.startswith(stage + " |")]
    one = lambda t: " / ".join(re.sub(r"[\x00-\x08\x0b-\x1f]", " ", str(t).replace("\r", "\n")).split("\n")).strip()     # one entry = one line
    lines = keep + ["%s | %s | %s | %s" % (stage, k, one(w), one(m)) for k, w, m in entries]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))


def title_case(text):
    small = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to"}
    words = text.lower().split()
    out = []
    for i, w in enumerate(words):
        out.append(w if (w in small and 0 < i < len(words) - 1) else w[:1].upper() + w[1:])
    return " ".join(out)
