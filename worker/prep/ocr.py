"""OCR fallback for scanned PDFs.

Uses Tesseract to extract text from pages where PyMuPDF finds no text layer.
Returns confidence scores for quality judgment.
"""

from __future__ import annotations

import io
import logging

import pytesseract
from PIL import Image

logger = logging.getLogger(__name__)


def ocr_page(page, dpi: int = 300, language: str = "eng") -> dict:
    """Extract text from a single PyMuPDF page using Tesseract OCR.

    Uses Tesseract's built-in block/paragraph/line structure to preserve
    line breaks, so downstream normalization (header/footer detection,
    paragraph breaks) has layout to work with.

    Args:
        page: A PyMuPDF page object.
        dpi: Resolution for rendering the page image.
        language: Tesseract language code (e.g., 'eng', 'deu', 'spa').

    Returns:
        Dictionary with:
            - text: extracted text with line breaks preserved
            - confidence: mean word confidence (0-100)
            - words: total word count
            - low_conf_words: count of words with confidence < 60
    """
    try:
        # Render page to image at specified DPI
        mat = page.get_pixmap(dpi=dpi)
        img_data = mat.tobytes("png")
        img = Image.open(io.BytesIO(img_data))

        # Run Tesseract OCR with data output for confidence scores and structure
        data = pytesseract.image_to_data(img, lang=language, output_type=pytesseract.Output.DICT)

        # Collect words with their structural identifiers
        words = []
        confidences = []
        low_conf_count = 0

        for i, text in enumerate(data["text"]):
            text = text.strip()
            if not text:
                continue
            conf = int(data["conf"][i])
            confidences.append(conf)
            if conf < 60:
                low_conf_count += 1
            words.append({
                "text": text,
                "block": data["block_num"][i],
                "par": data["par_num"][i],
                "line": data["line_num"][i],
                "word": data["word_num"][i],
                "top": data["top"][i],
                "left": data["left"][i],
            })

        if not words:
            return {
                "text": "",
                "confidence": 0.0,
                "words": 0,
                "low_conf_words": 0,
            }

        # Group words by (block, par, line) — Tesseract's own line detection.
        # Within each line, sort by word_num (Tesseract's reading order).
        # Lines are ordered by their top position (first word's top).
        from collections import OrderedDict

        # Use y-coordinate-based line detection instead of Tesseract's line grouping.
        # Tesseract sometimes merges headings with body text when they're close together.
        # We detect lines by clustering words with similar y-coordinates.
        from collections import OrderedDict

        # Sort all words by their top position, then left position
        words_sorted = sorted(words, key=lambda w: (w["top"], w["left"]))

        if words_sorted:
            # Use a threshold of 5 pixels at 300 DPI (approximately 0.4mm)
            # Words with top positions within this threshold are on the same line
            line_threshold = 5  # pixels at 300 DPI

            y_lines = []
            current_line = None

            for w in words_sorted:
                if current_line is None or abs(w["top"] - current_line["top"]) <= line_threshold:
                    if current_line is None:
                        current_line = {"words": [], "top": w["top"]}
                        y_lines.append(current_line)
                    current_line["words"].append(w)
                    # Update current_line top to the average of words in this line
                    tops = [ww["top"] for ww in current_line["words"]]
                    current_line["top"] = sum(tops) / len(tops)
                else:
                    current_line = {"words": [], "top": w["top"]}
                    y_lines.append(current_line)
                    current_line["words"].append(w)

            # Sort words within each line by left position (reading order)
            for line in y_lines:
                line["words"].sort(key=lambda w: w["left"])

            # Sort lines by vertical position
            sorted_lines = sorted(y_lines, key=lambda l: l["top"])
        else:
            sorted_lines = []

        # Build text: join words within a line with spaces, lines with newlines
        text_lines = []
        for line in sorted_lines:
            text_lines.append(" ".join(w["text"] for w in line["words"]))
        text = "\n".join(text_lines)

        mean_conf = sum(confidences) / len(confidences) if confidences else 0.0

        return {
            "text": text.strip(),
            "confidence": round(mean_conf, 1),
            "words": len(words),
            "low_conf_words": low_conf_count,
        }
    except Exception as e:
        logger.warning("OCR failed for page %d: %s", page.number + 1, e)
        return {
            "text": "",
            "confidence": 0.0,
            "words": 0,
            "low_conf_words": 0,
        }


def ocr_pages(doc, start: int = 0, end: int | None = None, dpi: int = 300, language: str = "eng") -> dict[int, dict]:
    """Extract text from a range of pages using Tesseract OCR.

    Args:
        doc: A PyMuPDF document.
        start: Starting page index (0-based).
        end: Ending page index (exclusive), or None for all remaining pages.
        dpi: Resolution for rendering page images.
        language: Tesseract language code.

    Returns:
        Dictionary mapping page index (0-based) to OCR result dict.
    """
    if end is None:
        end = len(doc)

    results = {}
    for i in range(start, min(end, len(doc))):
        page = doc[i]
        result = ocr_page(page, dpi=dpi, language=language)
        if result["text"]:
            results[i] = result
            logger.info(
                "OCR page %d: %d chars, conf=%.1f, %d words (%d low-conf)",
                i + 1,
                len(result["text"]),
                result["confidence"],
                result["words"],
                result["low_conf_words"],
            )

    return results
