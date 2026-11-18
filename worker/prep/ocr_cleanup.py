"""OCR quality judgment and LLM-based cleanup for low-quality pages."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Thresholds for OCR quality judgment (from old pipeline's pagereader_old.py)
MIN_CONFIDENCE = 85.0  # Mean word confidence below this triggers LLM cleanup
MIN_DICT_SHARE = 0.90  # Dictionary share below this triggers LLM cleanup
MIN_CHARS = 10  # Minimum characters per page to consider it valid


def judge_ocr_quality(ocr_result: dict, language: str = "eng") -> dict:
    """Judge the quality of an OCR result.

    Args:
        ocr_result: Result from ocr_page() with text, confidence, words, low_conf_words.
        language: Language code for dictionary checking.

    Returns:
        Dictionary with:
            - passed: True if quality is acceptable
            - confidence: mean confidence
            - dict_share: percentage of words found in language dictionary
            - reasons: list of failure reasons
    """
    text = ocr_result.get("text", "")
    confidence = ocr_result.get("confidence", 0.0)
    reasons = []

    # Check minimum characters
    chars = len(re.sub(r"\s", "", text))
    if chars < MIN_CHARS:
        reasons.append(f"too_few_chars ({chars} < {MIN_CHARS})")

    # Check confidence
    if confidence < MIN_CONFIDENCE:
        reasons.append(f"low_confidence ({confidence:.1f} < {MIN_CONFIDENCE})")

    # Check dictionary share
    dict_share = _dictionary_share(text, language)
    if dict_share < MIN_DICT_SHARE:
        reasons.append(f"low_dict_share ({dict_share:.2f} < {MIN_DICT_SHARE})")

    return {
        "passed": len(reasons) == 0,
        "confidence": confidence,
        "dict_share": round(dict_share, 3),
        "chars": chars,
        "reasons": reasons,
    }


def _dictionary_share(text: str, language: str) -> float:
    """Calculate the percentage of words found in the language dictionary.

    Uses a simple heuristic: words that are all alphabetic and not in a
    small stopword list are considered "dictionary words". For a more
    accurate check, we'd use the full dictionary from dicts.py.
    """
    if not text.strip():
        return 0.0

    # Extract words (alphabetic sequences of 2+ chars)
    words = re.findall(r"[a-zA-Z]{2,}", text.lower())
    if not words:
        return 0.0

    # Simple heuristic: count words that look like real English words
    # (not all caps, not too short, not just numbers)
    valid_words = 0
    for word in words:
        # Skip very short words (likely OCR artifacts)
        if len(word) < 2:
            continue
        # Skip all-caps words (likely headers or artifacts)
        if word.isupper():
            continue
        valid_words += 1

    # This is a rough estimate — for production, use the full dictionary
    # from dicts.py to get an accurate share
    return min(1.0, valid_words / len(words))


def needs_llm_cleanup(ocr_result: dict, language: str = "eng") -> bool:
    """Check if an OCR result needs LLM cleanup."""
    judgment = judge_ocr_quality(ocr_result, language)
    return not judgment["passed"]


async def cleanup_page_with_llm(
    llm_client,
    page_number: int,
    ocr_text: str,
    quality: dict,
    language: str = "eng",
) -> str:
    """Send a low-quality OCR page to the LLM for cleanup.

    Args:
        llm_client: LLM client instance.
        page_number: Page number (1-based).
        ocr_text: Raw OCR text.
        quality: Quality judgment from judge_ocr_quality().
        language: Language code.

    Returns:
        Cleaned text, or original text if LLM cleanup fails.
    """
    from worker.prep.llm.prompts import OCR_CLEANUP_SYSTEM_PROMPT

    prompt = f"""Clean up the following OCR-extracted text from page {page_number} of a book.

Quality issues detected: {', '.join(quality['reasons'])}
Mean confidence: {quality['confidence']:.1f}%
Dictionary share: {quality['dict_share']:.1%}

Rules:
- Fix obvious OCR errors (misrecognized characters, broken words, hyphenation artifacts)
- Remove page headers, footers, page numbers, and running titles
- Fix broken hyphens at line ends (rejoin words split across lines)
- Preserve the original meaning and wording as much as possible
- Do NOT add content that isn't in the original
- Return ONLY the cleaned text, no explanations

OCR text:
---
{ocr_text}
---

Cleaned text:"""

    try:
        response = await llm_client.chat(
            system=OCR_CLEANUP_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=4096,
        )
        cleaned = response.strip()
        if cleaned and len(cleaned) > len(ocr_text) * 0.5:  # Sanity check
            logger.info("LLM cleanup for page %d: %d -> %d chars", page_number, len(ocr_text), len(cleaned))
            return cleaned
        else:
            logger.warning("LLM cleanup for page %d returned suspiciously short result", page_number)
            return ocr_text
    except Exception as e:
        logger.warning("LLM cleanup failed for page %d: %s", page_number, e)
        return ocr_text
