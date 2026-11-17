"""Pure functions for PDF parsing. No I/O, no Redis, no Docker."""

from __future__ import annotations

import re

import fitz  # PyMuPDF


def parse_pdf(data: bytes) -> dict:
    """Parse PDF bytes into a structured document representation."""
    doc = fitz.open(stream=data, filetype="pdf")
    pages = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text("text").strip()
        images = page.get_images(full=True)
        pages.append(
            {
                "page": page_num + 1,
                "text": text,
                "images": len(images),
            }
        )
    metadata = doc.metadata or {}
    doc.close()
    return {
        "format": "pdf",
        "pages": pages,
        "total_pages": len(pages),
        "metadata": {
            "title": metadata.get("title") or "",
            "author": metadata.get("author") or "",
            "subject": metadata.get("subject") or "",
            "creator": metadata.get("creator") or "",
        },
    }


def classify_page(page: dict) -> tuple[str, str]:
    """Classify a single page. Returns (action, reason)."""
    text = page["text"]
    if not text or len(text) < 10:
        if page["images"] > 0:
            return "skipped", "illustration only"
        return "skipped", "blank"

    lower = text.lower()
    if re.search(r"copyright|all rights reserved|isbn", lower):
        return "kept", "copyright"
    if re.search(r"^\s*(table of contents|contents)\s*$", text, re.MULTILINE):
        return "kept", "table of contents"
    if re.search(r"^\s*(dedication|for\s+\w+)", lower, re.MULTILINE):
        return "kept", "dedication"
    if re.search(r"^\s*(acknowledgments?|acknowledgements?)\s*$", lower, re.MULTILINE):
        return "kept", "acknowledgments"
    if re.search(r"^\s*(index|bibliography|references|glossary)\s*$", lower, re.MULTILINE):
        return "kept", "backmatter"

    if re.match(r"^\s*(chapter|part|book|prologue|epilogue|preface|foreword|introduction)\s+\d+", lower):
        return "kept", "chapter heading"

    return "kept", "body text"


def detect_chapters(pages: list[dict]) -> list[dict]:
    """Detect chapter boundaries from classified pages."""
    chapters = []
    current_chapter = None
    chapter_re = re.compile(
        r"^\s*(chapter|part|book|prologue|epilogue|preface|foreword|introduction)\s+(\d+|[ivxlcdm]+)\.?\s*(.*)$",
        re.IGNORECASE,
    )

    for page in pages:
        text = page["text"].strip()
        first_line = text.split("\n")[0].strip() if text else ""
        match = chapter_re.match(first_line)
        if match:
            if current_chapter:
                chapters.append(current_chapter)
            title = match.group(3).strip() or f"Chapter {match.group(2)}"
            current_chapter = {
                "number": len(chapters) + 1,
                "title": title,
                "section": f"chapter-{len(chapters) + 1}",
                "start_page": page["page"],
                "end_page": page["page"],
                "pages": [page["page"]],
            }
        elif current_chapter:
            current_chapter["end_page"] = page["page"]
            current_chapter["pages"].append(page["page"])

    if current_chapter:
        chapters.append(current_chapter)

    if not chapters:
        chapters = [
            {
                "number": 1,
                "title": "Main Content",
                "section": "chapter-1",
                "start_page": pages[0]["page"] if pages else 1,
                "end_page": pages[-1]["page"] if pages else 1,
                "pages": [p["page"] for p in pages],
            }
        ]

    return chapters
