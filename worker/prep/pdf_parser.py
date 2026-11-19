"""PDF parsing: PyMuPDF extraction + OCR fallback + old pipeline's unit-based structure detection.

The new pipeline uses PyMuPDF for text extraction. If a page has no text layer
(scanned PDF), it falls back to Tesseract OCR. The old pipeline's structure
detection works on "units" (pages with layout text and geometry). We convert
PyMuPDF pages into units so the old structure.py, pageparts.py, and treatise.py
can work without modification.
"""

from __future__ import annotations

import json as _json
import logging
import os as _os
import pathlib as _pathlib
import re

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)


def parse_pdf(data: bytes, use_ocr: bool = True, ocr_dpi: int = 300, progress_callback=None, llm_client=None, language: str = "eng", ocr_settings=None, checkpoint_dir: str | None = None, start_page: int | None = None, end_page: int | None = None) -> dict:
    """Parse PDF bytes into a structured document representation.

    Args:
        data: PDF file bytes.
        use_ocr: If True, use Tesseract OCR for pages with no text layer.
        ocr_dpi: Resolution for OCR rendering.
        progress_callback: Optional callable(page_num, total_pages) for progress updates.
        llm_client: Optional LLM client for cleaning up low-quality OCR pages.
        language: Language code for OCR and dictionary checking.
        ocr_settings: Optional OcrQualitySettings from config.
        checkpoint_dir: Optional directory for per-page OCR checkpoints. If set,
            completed pages are saved as page-{N}.json and skipped on restart.
        start_page: Optional 1-based start page (inclusive). Defaults to 1.
        end_page: Optional 1-based end page (inclusive). Defaults to last page.
    """
    from worker.prep.ocr_cleanup import OcrQualitySettings

    if ocr_settings is None:
        ocr_settings = OcrQualitySettings()
    # The gate's dictionary language must follow the job's actual OCR
    # language, not just the static config default (#48).
    ocr_settings.language = language

    doc = fitz.open(stream=data, filetype="pdf")
    pages = []
    ocr_count = 0
    llm_cleanup_count = 0
    total_pages = len(doc)

    # Apply page range (1-based, inclusive)
    start_idx = (start_page or 1) - 1
    end_idx = end_page if end_page else total_pages
    start_idx = max(0, min(start_idx, total_pages))
    end_idx = max(start_idx + 1, min(end_idx, total_pages))
    page_count = end_idx - start_idx
    if page_count != total_pages:
        logger.info("Processing page range %d-%d of %d", start_idx + 1, end_idx, total_pages)

    # Load existing checkpoints
    checkpoints: dict[int, dict] = {}
    if checkpoint_dir:
        cp_path = _pathlib.Path(checkpoint_dir)
        cp_path.mkdir(parents=True, exist_ok=True)
        for f in cp_path.glob("page-*.json"):
            try:
                page_num = int(f.stem.split("-")[1])
                with open(f) as fh:
                    checkpoints[page_num] = _json.load(fh)
            except (ValueError, _json.JSONDecodeError, OSError):
                continue
        if checkpoints:
            logger.info("Loaded %d page checkpoints from %s", len(checkpoints), checkpoint_dir)

    def save_checkpoint(page_num: int, page_data: dict) -> None:
        if checkpoint_dir:
            cp_path = _pathlib.Path(checkpoint_dir)
            cp_file = cp_path / f"page-{page_num}.json"
            tmp_file = cp_file.with_suffix(".tmp")
            tmp_file.write_text(_json.dumps(page_data))
            tmp_file.rename(cp_file)

    for page_num in range(start_idx, end_idx):
        # Check if this page has a valid checkpoint
        if page_num in checkpoints:
            page_data = checkpoints[page_num]
            pages.append(page_data)
            logger.debug("Page %d loaded from checkpoint", page_num + 1)
            if progress_callback:
                progress_callback(page_num + 1 - start_idx, page_count)
            continue

        page = doc[page_num]
        text = page.get_text("text").strip()
        images = page.get_images(full=True)
        ocr_quality = None
        ocr_result = None
        cleanup_record = None
        declared_words = None

        # If no text layer and OCR is enabled, try OCR
        if not text and use_ocr:
            from worker.prep import ocr
            from worker.prep import ocr_cleanup

            ocr_result = ocr.ocr_page(page, dpi=ocr_dpi, language=language)
            text = ocr_result["text"]

            if text:
                ocr_count += 1
                ocr_quality = ocr_cleanup.judge_ocr_quality(ocr_result, ocr_settings)

                # If quality is poor and we have an LLM client, try cleanup
                if not ocr_quality["passed"] and llm_client and ocr_settings.llm_cleanup_enabled:
                    logger.info(
                        "Page %d OCR quality poor (%s), attempting LLM cleanup",
                        page_num + 1,
                        ", ".join(ocr_quality["reasons"]),
                    )
                    import asyncio

                    try:
                        cleaned = asyncio.run(
                            ocr_cleanup.cleanup_page_with_llm(
                                llm_client, page_num + 1, text, ocr_quality, ocr_settings
                            )
                        )
                        # Verify-and-revert (#65): accept only defect-attributed
                        # repairs; every other change returns to the OCR text.
                        cleaned, cleanup_record = ocr_cleanup.verify_cleanup(
                            page_num + 1, text, cleaned, ocr_settings
                        )
                        if cleanup_record.get("kept"):
                            logger.info(
                                "Page %d cleanup verified: %d kept, %d reverted%s",
                                page_num + 1,
                                len(cleanup_record["kept"]),
                                len(cleanup_record["reverted"]),
                                " (structural revert)" if cleanup_record.get("structural_revert") else "",
                            )
                        if cleaned != text:
                            text = cleaned
                            llm_cleanup_count += 1
                            logger.info("Page %d LLM cleanup applied", page_num + 1)
                    except Exception as e:
                        logger.warning("Page %d LLM cleanup failed: %s", page_num + 1, e)

                logger.info(
                    "OCR used for page %d: %d chars, conf=%.1f, dict_share=%.2f%s",
                    page_num + 1,
                    len(text),
                    ocr_quality["confidence"],
                    ocr_quality["dict_share"],
                    " (LLM cleaned)" if llm_cleanup_count > 0 else "",
                )
                # Word-level damage declaration (#68): non-dictionary words
                # in the SHIPPED text, on every OCR page — the honest U.
                declared_words = ocr_cleanup.declare_page_words(page_num + 1, text, language)

        page_data = {
            "page": page_num + 1,
            "text": text,
            "images": len(images),
            "ocr_quality": ocr_quality,
            "cleanup": cleanup_record,
            "low_conf_words": (ocr_result or {}).get("low_conf_words", 0),
            "declared_words": declared_words,
        }
        pages.append(page_data)

        # Save checkpoint for this page
        save_checkpoint(page_num, page_data)

        # Report progress every page (callback decides whether to actually report)
        if progress_callback:
            progress_callback(page_num + 1 - start_idx, page_count)

    metadata = doc.metadata or {}
    doc.close()

    if ocr_count > 0:
        logger.info("OCR used for %d of %d pages (%d LLM cleaned)", ocr_count, len(pages), llm_cleanup_count)

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
    """Classify a single page. Returns (action, reason).

    Thin wrapper over page_types.classify_text_page — the canonical
    classifier shared with the EPUB path (issue #42).
    """
    from worker.prep.page_types import classify_text_page

    return classify_text_page(page["text"], page.get("images", 0))


def detect_chapters(pages: list[dict], fm_sections: dict[int, str] | None = None) -> list[dict]:
    """Detect chapter boundaries using the old pipeline's structure detection.

    Pages classified as a printed table of contents are excluded from the
    scan: their 'CHAPTER N' entry lines would otherwise be read as real
    chapter headings (Unfinished Victory p.4 yields a bogus 'Chapter 1'
    from its CONTENTS page).

    Falls back to the naive regex if the old pipeline's detection fails
    (e.g., no units available).

    fm_sections: precomputed frontmatter classification (from
    frontmatter.classify_frontmatter); computed here when omitted. Pass
    it in when the caller already ran classify_frontmatter for the page
    ledger, so the job computes it once (issue #42).
    """
    from worker.prep import frontmatter as fm
    from worker.prep import structure

    fm_map = fm_sections if fm_sections is not None else fm.classify_frontmatter(pages)
    scan_pages = [p for p in pages if fm_map.get(p["page"]) != "frontmatter-toc"]

    units = pages_to_units(scan_pages)
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
                if h.get("label"):
                    # An unnumbered section (Introduction, Part I, ...) keeps
                    # its printed name as the section id.
                    slug = re.sub(r"[^a-z0-9]+", "-", h["label"].lower()).strip("-")
                    section = f"section-{slug}"
                else:
                    section = f"chapter-{h['number']}"
                ch = {
                    "number": h["number"],
                    "title": title,
                    "section": section,
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

    # Fallback: naive regex detection (TOC pages excluded here too)
    return _detect_chapters_naive(scan_pages)


def _detect_chapters_naive(pages: list[dict]) -> list[dict]:
    """Naive chapter detection (original implementation)."""
    from worker.prep.page_types import NUMBERED_HEADING_LINE_RE

    chapters = []
    current_chapter = None
    chapter_re = NUMBERED_HEADING_LINE_RE

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
