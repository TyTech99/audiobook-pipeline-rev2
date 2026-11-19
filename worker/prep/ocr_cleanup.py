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
    # A lowercase fragment continuing after terminal punctuation mid-line
    # ('fight. ry did not' — a scan-edge clipped line) is junk on any page:
    # more than this many flags the page for LLM cleanup. 0 = any occurrence.
    max_mid_line_lower_restarts: int = 0
    # Tokens mixing digits and letters ('1x', a misread roman-numeral footer)
    # are junk on any page: more than this many flags the page. 0 = any.
    max_digit_letter_tokens: int = 0
    language: str = "eng"
    llm_cleanup_enabled: bool = True
    # TurkuNLP ACL-2025 Optuna optimum for English OCR post-correction
    # (temp 0.26, top_k 65, top_p 0.66); 0.1 is too cold for this task.
    llm_cleanup_temperature: float = 0.26
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
            max_mid_line_lower_restarts=int(ocr.get("max_mid_line_lower_restarts", 0)),
            max_digit_letter_tokens=int(ocr.get("max_digit_letter_tokens", 0)),
            language=str(ocr.get("language", "eng")),
            llm_cleanup_enabled=bool(llm_cleanup.get("enabled", True)),
            llm_cleanup_temperature=float(llm_cleanup.get("temperature", 0.26)),
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

    # Check dictionary share (the real measure from dicts.py — the same one
    # the old pipeline used for its page quality check). Missing word list:
    # warn once per call and pass this check rather than fail every page.
    dict_share = _dictionary_share(text, settings.language)
    if dict_share < settings.min_dict_share:
        reasons.append(f"low_dict_share ({dict_share:.2f} < {settings.min_dict_share})")

    # A lowercase fragment continuing after terminal punctuation mid-line:
    # the signature of a scan-edge clipped line ('fight. ry did not').
    # Verified on Unfinished Victory: 1 on damaged p.22, 0 on every clean page.
    restarts = sum(len(_MID_LINE_LOWER_RESTART_RE.findall(ln)) for ln in text.split("\n"))
    if restarts > settings.max_mid_line_lower_restarts:
        reasons.append(f"mid_line_lower_restart ({restarts} > {settings.max_mid_line_lower_restarts})")

    # Tokens mixing digits and letters ('1x' — a misread roman-numeral
    # footer). Real numbers ('1940') have no letters and stay unflagged.
    digit_letter = len(_DIGIT_LETTER_TOKEN_RE.findall(text))
    if digit_letter > settings.max_digit_letter_tokens:
        reasons.append(f"digit_letter_token ({digit_letter} > {settings.max_digit_letter_tokens})")

    return {
        "passed": len(reasons) == 0,
        "confidence": confidence,
        "dict_share": round(dict_share, 3),
        "chars": chars,
        "reasons": reasons,
    }


_MID_LINE_LOWER_RESTART_RE = re.compile(r"[.!?]\s+[a-z]")
_DIGIT_LETTER_TOKEN_RE = re.compile(r"(?=\w*\d)(?=\w*[A-Za-z])\w+")


def _dictionary_share(text: str, language: str = "eng") -> float:
    """Fraction of the text's words found in the language's word list.

    Uses dicts.py — the Debian word lists (wamerican-large et al.), the
    same measure the old pipeline used for the page quality check. Falls
    back to 1.0 with a warning when the word list is not installed, so a
    missing dictionary disables the check instead of failing every page.
    """
    from worker.prep import dicts

    if not text.strip():
        return 0.0
    try:
        return dicts.share(text, language)
    except (FileNotFoundError, ValueError) as e:
        logger.warning("dictionary share check unavailable: %s", e)
        return 1.0


def needs_llm_cleanup(ocr_result: dict, settings: OcrQualitySettings) -> bool:
    """Check if an OCR result needs LLM cleanup."""
    if not settings.llm_cleanup_enabled:
        return False
    judgment = judge_ocr_quality(ocr_result, settings)
    return not judgment["passed"]


# How each gate reason is explained to the cleanup model. A generic
# 'fix obvious OCR errors' does not engage the model: at 27B the damage
# passes through unchanged. With the damage CLASS described (verified
# live on Unfinished Victory p.22 against Qwen3.8-27B: 'ry did not' ->
# 'Germany did not' and '10 other' -> 'no other' only when the
# instruction named the class), the same model repairs both (#50).
REASON_INSTRUCTIONS = {
    "mid_line_lower_restart": (
        "A lowercase fragment continuing after terminal punctuation mid-line "
        "(like 'fight. ry did not') is a line whose opening was clipped by the "
        "scan edge: reconstruct the missing opening word(s) from context - the "
        "fragment itself may be the tail of the clipped word"
    ),
    "digit_letter_token": (
        "Tokens mixing digits and letters are OCR misreads of printed words "
        "(a common case is 'no' read as '10'); infer and restore the printed "
        "word from the sentence"
    ),
    "low_dict_share": (
        "Words not found in the dictionary may be broken OCR of real words: "
        "repair the clearly broken ones, but keep proper nouns, place names "
        "and period spellings as printed"
    ),
    "low_confidence": (
        "Overall recognition confidence is low for this page: read carefully "
        "for misrecognized characters and broken words"
    ),
}


def _repair_instructions(reasons: list[str]) -> list[str]:
    """Concrete repair instructions for the reasons the gate reported."""
    out = []
    for reason in reasons:
        instruction = REASON_INSTRUCTIONS.get(reason.split(" ")[0])
        if instruction and instruction not in out:
            out.append(instruction)
    return out


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

    repairs = _repair_instructions(quality.get("reasons", []))
    repairs_text = ("\nThis page's specific damage — repair it:\n"
                    + "\n".join("- " + r for r in repairs) + "\n"
                    if repairs else "")

    prompt = f"""Clean up the following OCR-extracted text from page {page_number} of a book.

Mean confidence: {quality['confidence']:.1f}%
Dictionary share: {quality['dict_share']:.1%}
{repairs_text}
Rules:
- Fix obvious OCR errors (misrecognized characters, broken words, hyphenation artifacts)
- Remove page headers, footers, page numbers, and running titles
- Fix broken hyphens at line ends (rejoin words split across lines)
- Preserve the original meaning and wording as much as possible
- Do NOT add content that isn't in the original
- CRITICAL: preserve each line's leading spaces EXACTLY as given and keep the
  same line breaks — leading spaces encode paragraph indentation. Do not
  reflow, re-wrap, or merge lines beyond repairing hyphenated words.
- Return ONLY the cleaned text, no explanations

OCR text:
---
{ocr_text}
---

Cleaned text:"""

    try:
        # LLMClient.chat_text is a plain-text completion (chat_json would
        # force a JSON response format). Sync call inside this async
        # wrapper: parse_pdf drives it via asyncio.run per page.
        cleaned = llm_client.chat_text(
            system=system_prompt,
            user=prompt,
            temperature=settings.llm_cleanup_temperature,
            max_tokens=settings.llm_cleanup_max_tokens,
        )
        # Keep the first content line's leading spaces (paragraph
        # indentation, #46): strip only trailing whitespace and any
        # blank lines the model put before the text.
        cleaned = cleaned.rstrip()
        cleaned = re.sub(r"^\s*\n", "", cleaned, count=1)
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
