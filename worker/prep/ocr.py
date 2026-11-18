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

    Args:
        page: A PyMuPDF page object.
        dpi: Resolution for rendering the page image.
        language: Tesseract language code (e.g., 'eng', 'deu', 'spa').

    Returns:
        Dictionary with:
            - text: extracted text
            - confidence: mean word confidence (0-100)
            - words: total word count
            - low_conf_words: count of words with confidence < 60
    """
    try:
        # Render page to image at specified DPI
        mat = page.get_pixmap(dpi=dpi)
        img_data = mat.tobytes("png")
        img = Image.open(io.BytesIO(img_data))

        # Run Tesseract OCR with data output for confidence scores
        data = pytesseract.image_to_data(img, lang=language, output_type=pytesseract.Output.DICT)

        # Extract text and confidence
        text_parts = []
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
            text_parts.append(text)

        text = " ".join(text_parts)
        mean_conf = sum(confidences) / len(confidences) if confidences else 0.0

        return {
            "text": text.strip(),
            "confidence": round(mean_conf, 1),
            "words": len(text_parts),
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
