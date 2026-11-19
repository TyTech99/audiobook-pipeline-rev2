"""Page ledger builder and validator."""

from __future__ import annotations

from datetime import datetime, timezone

from worker.prep import frontmatter as fm


def build_page_ledger(
    source_format: str,
    source_file: str,
    pages: list[dict],
    chapters: list[dict],
    fm_sections: dict[int, str] | None = None,
) -> dict:
    """Build a page ledger from parsed pages and detected chapters.

    fm_sections: precomputed frontmatter classification (from
    frontmatter.classify_frontmatter); computed here when omitted. Pass
    it in when the caller already ran classify_frontmatter for chapter
    detection, so the job computes it once (issue #42).
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Classify frontmatter/backmatter pages
    if fm_sections is None:
        fm_sections = fm.classify_frontmatter(pages)

    if source_format == "pdf":
        entries = []
        for page in pages:
            action, reason = page.get("action", "kept"), page.get("reason", "body text")
            page_num = page["page"]

            # Frontmatter/backmatter takes precedence over chapter assignment
            if page_num in fm_sections:
                section = fm_sections[page_num]
                reason = fm.get_frontmatter_reason(section)
            else:
                section = _find_section(chapters, page_num)

            entries.append(
                {
                    "page": page_num,
                    "action": action,
                    "reason": reason,
                    "section": section,
                }
            )
        structure = _build_structure(chapters, fm_sections, pages)
        ledger = {
            "source_format": "pdf",
            "source_file": source_file,
            "total_pages": len(entries),
            "pages": entries,
            "structure": structure,
            "created_at": now,
        }
    else:
        entries = []
        for page in pages:
            action, reason = page.get("action", "kept"), page.get("reason", "body text")
            page_num = page["page"]

            if page_num in fm_sections:
                section = fm_sections[page_num]
                reason = fm.get_frontmatter_reason(section)
            else:
                section = _find_section(chapters, page_num)

            entries.append(
                {
                    "item": page.get("item", f"page-{page_num}"),
                    "action": action,
                    "reason": reason,
                    "section": section,
                }
            )
        structure = _build_structure(chapters, fm_sections, pages, item_mode=True)
        ledger = {
            "source_format": "epub",
            "source_file": source_file,
            "total_items": len(entries),
            "items": entries,
            "structure": structure,
            "created_at": now,
        }

    kept = sum(1 for e in ledger.get("pages", ledger.get("items", [])) if e["action"] == "kept")
    skipped = len(ledger.get("pages", ledger.get("items", []))) - kept
    breakdown = {}
    for e in ledger.get("pages", ledger.get("items", [])):
        if e["action"] == "skipped":
            breakdown[e["reason"]] = breakdown.get(e["reason"], 0) + 1
    ledger["summary"] = {
        "total": kept + skipped,
        "kept": kept,
        "skipped": skipped,
        "skipped_breakdown": breakdown,
    }
    return ledger


def _build_structure(
    chapters: list[dict],
    fm_sections: dict[int, str],
    pages: list[dict],
    item_mode: bool = False,
) -> list[dict]:
    """Build the structure array including frontmatter/backmatter sections."""
    structure = []

    # Add frontmatter sections (in page order)
    fm_by_section: dict[str, list[int]] = {}
    for page_num, section in sorted(fm_sections.items()):
        fm_by_section.setdefault(section, []).append(page_num)

    for section, page_nums in fm_by_section.items():
        entry: dict = {
            "section": section,
            "title": fm.get_frontmatter_reason(section).title(),
        }
        if item_mode:
            entry["items"] = [p.get("item", f"page-{n}") for p, n in zip(pages, page_nums) if p["page"] == n]
        else:
            entry["pages"] = page_nums
        structure.append(entry)

    # Add chapter sections
    for ch in chapters:
        entry: dict = {
            "section": ch["section"],
            "number": ch["number"],
            "title": ch["title"],
        }
        if item_mode:
            entry["items"] = [p.get("item", f"page-{p}") for p in ch["pages"]]
        else:
            entry["pages"] = ch["pages"]
        structure.append(entry)

    return structure


def _find_section(chapters: list[dict], page_num: int) -> str:
    for ch in chapters:
        if ch["start_page"] <= page_num <= ch["end_page"]:
            return ch["section"]
    return "unassigned"


def validate_page_ledger(ledger: dict, source_count: int) -> list[str]:
    """Validate a page ledger. Returns a list of errors (empty if valid)."""
    errors = []

    if ledger["source_format"] == "pdf":
        entries = ledger.get("pages", [])
        entry_key = "page"
        expected_count = source_count
    else:
        entries = ledger.get("items", [])
        entry_key = "item"
        expected_count = ledger.get("total_items", len(entries))

    if len(entries) != expected_count:
        errors.append(f"Expected {expected_count} entries, got {len(entries)}")

    if ledger["source_format"] == "pdf":
        page_numbers = [e["page"] for e in entries]
        # Contiguity, not start-at-1: a job may process a mid-book
        # page_range (e.g. [3, 4]) to sample a chapter or skip leading
        # scan/utility pages — the ledger is valid as long as its pages
        # form one unbroken run (#55). Count mismatches are already
        # flagged above by the expected_count check.
        if page_numbers:
            first = min(page_numbers)
            if sorted(page_numbers) != list(range(first, first + expected_count)):
                errors.append("Page numbers have gaps or duplicates")

    for e in entries:
        if e["action"] not in ("kept", "skipped"):
            errors.append(f"Invalid action: {e['action']}")
        if not e.get("reason"):
            errors.append(f"Missing reason for {entry_key} {e[entry_key]}")
        if not e.get("section"):
            errors.append(f"Missing section for {entry_key} {e[entry_key]}")

    summary = ledger.get("summary", {})
    kept = sum(1 for e in entries if e["action"] == "kept")
    skipped = sum(1 for e in entries if e["action"] == "skipped")
    if summary.get("kept") != kept:
        errors.append(f"Summary kept ({summary.get('kept')}) != actual ({kept})")
    if summary.get("skipped") != skipped:
        errors.append(f"Summary skipped ({summary.get('skipped')}) != actual ({skipped})")

    return errors
