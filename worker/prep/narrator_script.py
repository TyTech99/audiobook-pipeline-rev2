"""Narrator script builder.

Ported from the old pipeline's script.py, adapted to the new distributed
architecture. The old pipeline wrote one JSON file per audio file; the new
pipeline writes a single script.json with all chapters and segments.

Segment types (from the old pipeline):
- author: verbatim text from the book (added=false)
- table: small table read row by row (added=false)
- figure_sentence: a figure the text refers to (added=true)
- table_takeaway: a large table summarized (added=true)
- credits: opening/closing credits (added=true)
- header: chapter header (added=true)
- label: summary label (added=true)
- cue: chime cue (added=true)
- chapter_preview: chapter preview summary (added=true)
- concept_summary: mid-chapter concept summary (added=true)
- chapter_summary: chapter-end summary (added=true)

The new pipeline's script.json format is preserved for compatibility with
the existing narrator_script.py consumers.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone


def build_narrator_script(
    job_id: str,
    source_format: str,
    source_file: str,
    title: str,
    author: str,
    language: str,
    pages: list[dict],
    chapters: list[dict],
) -> dict:
    """Build a narrator script from parsed pages and detected chapters.

    Uses the old pipeline's segment types and structure, adapted to the
    new pipeline's script.json format.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    script_chapters = []
    segment_counter = 0
    total_segments = 0

    # Opening credits
    opening_credits = [
        {
            "id": f"seg-{segment_counter + 1:03d}",
            "text": f"This is {title}, written by {author}.",
            "source_ref": "credits",
            "section": "opening-credits",
            "content_type": "credits",
        }
    ]
    segment_counter += 1

    for ch in chapters:
        segments = []

        # Chapter header
        header_text = _chapter_header(ch)
        segments.append(
            {
                "id": f"seg-{segment_counter + 1:03d}",
                "text": header_text,
                "source_ref": f"page {ch['start_page']}",
                "section": ch["section"],
                "content_type": "header",
            }
        )
        segment_counter += 1

        # Chapter content
        for page_num in ch["pages"]:
            page = next((p for p in pages if p["page"] == page_num), None)
            if page is None or not page["text"]:
                continue
            if page.get("action") == "skipped":
                continue

            text = page["text"]
            paragraphs = _split_paragraphs(text)
            for para in paragraphs:
                para = para.strip()
                if not para or len(para) < 3:
                    continue
                # Skip the chapter heading line itself (already in the header)
                if _is_chapter_heading(para, ch):
                    continue
                # Skip short title lines that are part of the chapter heading
                if page_num == ch["start_page"] and len(para) < 50 and not _looks_like_body_text(para):
                    continue
                segment_counter += 1
                segments.append(
                    {
                        "id": f"seg-{segment_counter:03d}",
                        "text": para,
                        "source_ref": f"page {page_num}",
                        "section": ch["section"],
                        "content_type": "author",
                    }
                )

        if segments:
            total_segments += len(segments)
            script_chapters.append(
                {
                    "chapter": ch["number"],
                    "title": ch["title"],
                    "section": ch["section"],
                    "source_ref": f"pages {ch['start_page']}-{ch['end_page']}",
                    "segments": segments,
                }
            )

    # Closing credits
    closing_credits = [
        {
            "id": f"seg-{segment_counter + 1:03d}",
            "text": f"This is the end of {title}, written by {author}.",
            "source_ref": "credits",
            "section": "closing-credits",
            "content_type": "credits",
        }
    ]
    segment_counter += 1

    word_count = sum(len(seg["text"].split()) for ch in script_chapters for seg in ch["segments"])
    estimated_minutes = max(1, word_count // 150)

    return {
        "book_id": job_id,
        "job_id": job_id,
        "title": title,
        "author": author,
        "source_format": source_format,
        "source_file": source_file,
        "language": language,
        "chapters": script_chapters,
        "frontmatter": {},
        "opening_credits": opening_credits,
        "closing_credits": closing_credits,
        "metadata": {
            "total_segments": total_segments,
            "total_chapters": len(script_chapters),
            "total_pages": len(pages),
            "narrated_pages": sum(len(ch["pages"]) for ch in chapters),
            "estimated_word_count": word_count,
            "estimated_duration_minutes": estimated_minutes,
            "created_at": now,
        },
    }


def _chapter_header(ch: dict) -> str:
    """The chapter's header as spoken."""
    if ch.get("synthetic"):
        return f"{ch.get('title', 'The Complete Text')}."
    if ch.get("label"):
        return f"{ch['label']}. {ch['title']}."
    return f"Chapter {_number_word(ch['number'])}. {ch['title']}."


def _number_word(n: int) -> str:
    """Number as a word (One, Two, Three, ...)."""
    words = [
        "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight",
        "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen",
        "Sixteen", "Seventeen", "Eighteen", "Nineteen", "Twenty",
    ]
    if 1 <= n <= len(words):
        return words[n - 1]
    return str(n)


def _looks_like_body_text(text: str) -> bool:
    """Check if text looks like body text (not a title or heading)."""
    # Body text is usually longer and has sentence structure
    if len(text) < 20:
        return False
    # Has sentence-ending punctuation
    if any(c in text for c in ".!?"):
        return True
    # Has multiple words and isn't all caps
    words = text.split()
    if len(words) > 5 and not text.isupper():
        return True
    return False


def _is_chapter_heading(text: str, ch: dict) -> bool:
    """Check if text is the chapter heading (already in the header)."""
    # Match "Chapter N:" or "CHAPTER N" patterns
    import re
    pattern = rf"^(Chapter|CHAPTER)\s*{_number_word(ch['number']).lower()}\s*[:.]?\s*$"
    if re.match(pattern, text, re.IGNORECASE):
        return True
    # Match the chapter title exactly
    if text.strip() == ch.get("title", "").strip():
        return True
    return False


def _split_paragraphs(text: str) -> list[str]:
    """Split text into paragraphs (double newline or single newline)."""
    # First try double newline
    parts = re.split(r"\n\s*\n", text)
    if len(parts) > 1:
        return [p.strip() for p in parts if p.strip()]
    # Fall back to single newline
    parts = text.split("\n")
    return [p.strip() for p in parts if p.strip()]


def validate_script(script: dict, ledger: dict) -> list[str]:
    """Validate a narrator script against a page ledger."""
    errors = []
    seen_ids = set()

    # Validate opening/closing credits
    for key in ("opening_credits", "closing_credits"):
        for seg in script.get(key, []):
            if not seg.get("id"):
                errors.append(f"{key} segment missing id")
            elif seg["id"] in seen_ids:
                errors.append(f"Duplicate segment id: {seg['id']}")
            else:
                seen_ids.add(seg["id"])
            if not seg.get("text"):
                errors.append(f"{key} segment {seg.get('id')} has no text")

    for ch in script.get("chapters", []):
        if not ch.get("segments"):
            errors.append(f"Chapter {ch.get('chapter')} has no segments")
        for seg in ch.get("segments", []):
            if not seg.get("id"):
                errors.append("Segment missing id")
            elif seg["id"] in seen_ids:
                errors.append(f"Duplicate segment id: {seg['id']}")
            else:
                seen_ids.add(seg["id"])
            if not seg.get("text"):
                errors.append(f"Segment {seg.get('id')} has no text")
            if not seg.get("source_ref"):
                errors.append(f"Segment {seg.get('id')} has no source_ref")

    total = sum(len(ch.get("segments", [])) for ch in script.get("chapters", []))
    if script.get("metadata", {}).get("total_segments") != total:
        errors.append(
            f"metadata.total_segments ({script.get('metadata', {}).get('total_segments')}) != actual ({total})"
        )

    return errors
