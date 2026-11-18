"""PDF parsing: PyMuPDF extraction + OCR fallback + old pipeline's unit-based structure detection.

The new pipeline uses PyMuPDF for text extraction. If a page has no text layer
(scanned PDF), it falls back to Tesseract OCR. The old pipeline's structure
detection works on "units" (pages with layout text and geometry). We convert
PyMuPDF pages into units so the old structure.py, pageparts.py, and treatise.py
can work without modification.
"""

from __future__ import annotations

import logging
import re

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)


def parse_pdf(data: bytes, use_ocr: bool = True, ocr_dpi: int = 300) -> dict:
    """Parse PDF bytes into a structured document representation.

    Args:
        data: PDF file bytes.
        use_ocr: If True, use Tesseract OCR for pages with no text layer.
        ocr_dpi: Resolution for OCR rendering.
    """
    doc = fitz.open(stream=data, filetype="pdf")
    pages = []
    ocr_count = 0

    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text("text").strip()
        images = page.get_images(full=True)

        # If no text layer and OCR is enabled, try OCR
        if not text and use_ocr:
            from worker.prep import ocr
            text = ocr.ocr_page(page, dpi=ocr_dpi)
            if text:
                ocr_count += 1
                logger.info("OCR used for page %d: %d chars", page_num + 1, len(text))

        pages.append(
            {
                "page": page_num + 1,
                "text": text,
                "images": len(images),
            }
        )

    metadata = doc.metadata or {}
    doc.close()

    if ocr_count > 0:
        logger.info("OCR used for %d of %d pages", ocr_count, len(pages))

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


def pages_to_units(pages: list[dict]) -> list[dict]:
    """Convert PyMuPDF pages into the old pipeline's unit format.

    Each unit is a dict with:
      - pdf_page: 1-based page number
      - half: None (we don't split landscape pages yet)
      - text: the page text (layout preserved by PyMuPDF)
      - geom: None (no line geometry from PyMuPDF text extraction)
      - normalized: {}

    The old structure.py and pageparts.py work with units. They use
    unit["text"].split("\\n") for line-based analysis and unit["geom"]
    for line positions (optional — they handle None).
    """
    units = []
    for p in pages:
        units.append({
            "pdf_page": p["page"],
            "half": None,
            "text": p["text"],
            "geom": None,
            "normalized": {},
        })
    return units


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
    """Detect chapter boundaries using the old pipeline's structure detection.

    Falls back to the naive regex if the old pipeline's detection fails
    (e.g., no units available).
    """
    from worker.prep import structure

    units = pages_to_units(pages)
    try:
        heads = structure.find_headings(units)
        if heads:
            chapters = []
            for k, h in enumerate(heads):
                if k + 1 < len(heads):
                    nh = heads[k + 1]
                    last_unit = nh["unit"] - 1
                else:
                    last_unit = len(units) - 1
                title = h.get("title") or f"Chapter {h['number']}"
                ch = {
                    "number": h["number"],
                    "title": title,
                    "section": f"chapter-{h['number']}",
                    "start_page": units[h["unit"]]["pdf_page"],
                    "end_page": units[last_unit]["pdf_page"],
                    "pages": [units[i]["pdf_page"] for i in range(h["unit"], last_unit + 1)],
                }
                if h.get("label"):
                    ch["label"] = h["label"]
                if h.get("synthetic"):
                    ch["synthetic"] = True
                chapters.append(ch)
            if chapters:
                return chapters
    except Exception:
        pass

    # Fallback: naive regex detection
    return _detect_chapters_naive(pages)


def _detect_chapters_naive(pages: list[dict]) -> list[dict]:
    """Naive chapter detection (original implementation)."""
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
