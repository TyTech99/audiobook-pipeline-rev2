"""Prompt templates for prep-stage book analysis."""

from __future__ import annotations

SYSTEM_PROMPT = """You analyze books for an audiobook production pipeline.
Return a single JSON object with exactly these keys:
- subject_summary: string, brief subject summary
- author_tone: string, tone/style/voice of the prose
- narration_style_recommendations: array of 3+ objects, each with style, reason, confidence
- suggested_voice_recipes: array of objects, each with recipe, style, reason

confidence must be one of: high, medium, low.
Do not include markdown. Do not include keys outside that schema.
"""


def build_user_prompt(
    *,
    title: str,
    author: str,
    language: str,
    sample_text: str,
    available_recipes: list[str] | None = None,
) -> str:
    recipes = available_recipes or [
        "narrator-deep-male-v2",
        "narrator-warm-female-v1",
        "narrator-neutral-v1",
    ]
    recipe_line = ", ".join(recipes)
    sample = sample_text.strip()
    if len(sample) > 12000:
        sample = sample[:12000] + "\n…[truncated]"
    return (
        f"Title: {title}\n"
        f"Author: {author}\n"
        f"Language: {language}\n"
        f"Known voice recipes (prefer these ids when suggesting): {recipe_line}\n\n"
        f"Book sample text:\n{sample}\n"
    )


CHAPTER_SYSTEM_PROMPT = """You analyze one chapter of a book for an audiobook production pipeline.
Return a single JSON object with exactly these keys:
- summary: string, 2-3 sentences summarizing what this chapter covers
- tone_note: string, one line describing the pacing and emotional register
  for the narrator (e.g. "measured and elegiac, with irony in the last third")

Do not include markdown. Do not include keys outside that schema."""


def build_chapter_prompt(
    *,
    book_title: str,
    chapter_title: str,
    language: str,
    sample_text: str,
) -> str:
    sample = sample_text.strip()
    if len(sample) > 8000:
        sample = sample[:8000] + "\n…[truncated]"
    return (
        f"Book: {book_title}\n"
        f"Chapter: {chapter_title}\n"
        f"Language: {language}\n\n"
        f"Chapter text:\n{sample}\n"
    )
