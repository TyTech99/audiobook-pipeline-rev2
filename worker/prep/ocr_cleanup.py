"""OCR quality judgment and LLM-based cleanup for low-quality pages.

Thresholds and prompts are read from worker-prep.yaml config (ocr section),
not hardcoded.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class OcrQualitySettings:
    """OCR quality settings from config."""

    min_confidence: float = 85.0
    min_dict_share: float = 0.90
    min_chars: int = 10
    llm_cleanup_enabled: bool = True
    llm_cleanup_temperature: float = 0.1
    llm_cleanup_max_tokens: int = 4096
    llm_cleanup_system_prompt: str = ""

    @classmethod
    def from_config(cls, config: dict) -> "OcrQualitySettings":
        """Create settings from worker-prep.yaml ocr section."""
        ocr = config.get("ocr", {})
        llm_cleanup = ocr.get("llm_cleanup", {})
        return cls(
            min_confidence=float(ocr.get("min_confidence", 85.0)),
            min_dict_share=float(ocr.get("min_dict_share", 0.90)),
            min_chars=int(ocr.get("min_chars", 10)),
            llm_cleanup_enabled=bool(llm_cleanup.get("enabled", True)),
            llm_cleanup_temperature=float(llm_cleanup.get("temperature", 0.1)),
            llm_cleanup_max_tokens=int(llm_cleanup.get("max_tokens", 4096)),
            llm_cleanup_system_prompt=str(llm_cleanup.get("system_prompt", "")).strip(),
        )


def judge_ocr_quality(ocr_result: dict, settings: OcrQualitySettings) -> dict:
    """Judge the quality of an OCR result.

    Args:
        ocr_result: Result from ocr_page() with text, confidence, words, low_conf_words.
        settings: OCR quality settings from config.

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
    if chars < settings.min_chars:
        reasons.append(f"too_few_chars ({chars} < {settings.min_chars})")

    # Check confidence
    if confidence < settings.min_confidence:
        reasons.append(f"low_confidence ({confidence:.1f} < {settings.min_confidence})")

    # Check dictionary share
    dict_share = _dictionary_share(text)
    if dict_share < settings.min_dict_share:
        reasons.append(f"low_dict_share ({dict_share:.2f} < {settings.min_dict_share})")

    return {
        "passed": len(reasons) == 0,
        "confidence": confidence,
        "dict_share": round(dict_share, 3),
        "chars": chars,
        "reasons": reasons,
    }


def _dictionary_share(text: str) -> float:
    """Calculate the percentage of words that look like real words.

    Uses a simple heuristic: words that are all alphabetic and not all-caps
    are considered valid. For production accuracy, use the full dictionary
    from dicts.py.
    """
    if not text.strip():
        return 0.0

    # Extract words (alphabetic sequences of 2+ chars)
    words = re.findall(r"[a-zA-Z]{2,}", text.lower())
    if not words:
        return 0.0

    # Count words that look like real words (not all caps, not too short)
    valid_words = 0
    for word in words:
        if len(word) < 2:
            continue
        valid_words += 1

    return min(1.0, valid_words / len(words))


def needs_llm_cleanup(ocr_result: dict, settings: OcrQualitySettings) -> bool:
    """Check if an OCR result needs LLM cleanup."""
    if not settings.llm_cleanup_enabled:
        return False
    judgment = judge_ocr_quality(ocr_result, settings)
    return not judgment["passed"]


async def cleanup_page_with_llm(
    llm_client,
    page_number: int,
    ocr_text: str,
    quality: dict,
    settings: OcrQualitySettings,
) -> str:
    """Send a low-quality OCR page to the LLM for cleanup.

    Args:
        llm_client: LLM client instance.
        page_number: Page number (1-based).
        ocr_text: Raw OCR text.
        quality: Quality judgment from judge_ocr_quality().
        settings: OCR quality settings from config.

    Returns:
        Cleaned text, or original text if LLM cleanup fails.
    """
    system_prompt = settings.llm_cleanup_system_prompt or _DEFAULT_SYSTEM_PROMPT

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
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
            temperature=settings.llm_cleanup_temperature,
            max_tokens=settings.llm_cleanup_max_tokens,
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


_DEFAULT_SYSTEM_PROMPT = """You are an OCR text cleanup specialist. Your job is to fix OCR errors in extracted book text while preserving the original meaning and wording.

Common OCR errors to fix:
- Misrecognized characters (e.g., '1' instead of 'l', '0' instead of 'O', 'rn' instead of 'm')
- Broken hyphens at line ends (rejoin words split across lines)
- Random page breaks and headers/footers (remove page numbers, running titles)
- Garbage characters and symbols that don't belong
- Spacing errors (missing or extra spaces)

Rules:
- Preserve the original wording and meaning as much as possible
- Do NOT add content that isn't in the original
- Do NOT rewrite or paraphrase — only fix OCR artifacts
- Return ONLY the cleaned text, no explanations or markdown"""
