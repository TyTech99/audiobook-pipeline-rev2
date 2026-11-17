"""Narrator script builder."""

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
    """Build a narrator script from parsed pages and detected chapters."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    script_chapters = []
    segment_counter = 0
    total_segments = 0

    for ch in chapters:
        segments = []
        for page_num in ch["pages"]:
            page = next((p for p in pages if p["page"] == page_num), None)
            if page is None or not page["text"]:
                continue
            if page.get("action") == "skipped":
                continue

            text = page["text"]
            sentences = _split_sentences(text)
            for sentence in sentences:
                sentence = sentence.strip()
                if not sentence or len(sentence) < 3:
                    continue
                segment_counter += 1
                segments.append(
                    {
                        "id": f"seg-{segment_counter:03d}",
                        "text": sentence,
                        "source_ref": f"page {page_num}",
                        "section": ch["section"],
                        "content_type": "body_text",
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


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if p.strip()]


def validate_script(script: dict, ledger: dict) -> list[str]:
    """Validate a narrator script against a page ledger."""
    errors = []
    seen_ids = set()

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
