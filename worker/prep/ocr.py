"""OCR fallback for scanned PDFs.

Uses Tesseract to extract text from pages where PyMuPDF finds no text layer.
"""

from __future__ import annotations

import io
import logging

import pytesseract
from PIL import Image

logger = logging.getLogger(__name__)


def ocr_page(page, dpi: int = 300) -> str:
    """Extract text from a single PyMuPDF page using Tesseract OCR.

    Args:
        page: A PyMuPDF page object.
        dpi: Resolution for rendering the page image.

    Returns:
        Extracted text, or empty string if OCR fails.
    """
    try:
        # Render page to image at specified DPI
        mat = page.get_pixmap(dpi=dpi)
        img_data = mat.tobytes("png")
        img = Image.open(io.BytesIO(img_data))

        # Run Tesseract OCR
        text = pytesseract.image_to_string(img)
        return text.strip()
    except Exception as e:
        logger.warning("OCR failed for page %d: %s", page.number + 1, e)
        return ""


def ocr_pages(doc, start: int = 0, end: int | None = None, dpi: int = 300) -> dict[int, str]:
    """Extract text from a range of pages using Tesseract OCR.

    Args:
        doc: A PyMuPDF document.
        start: Starting page index (0-based).
        end: Ending page index (exclusive), or None for all remaining pages.
        dpi: Resolution for rendering page images.

    Returns:
        Dictionary mapping page index (0-based) to extracted text.
    """
    if end is None:
        end = len(doc)

    results = {}
    for i in range(start, min(end, len(doc))):
        page = doc[i]
        text = ocr_page(page, dpi=dpi)
        if text:
            results[i] = text
            logger.info("OCR page %d: %d chars", i + 1, len(text))

    return results
