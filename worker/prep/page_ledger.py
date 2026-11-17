"""Page ledger builder and validator."""

from __future__ import annotations

from datetime import datetime, timezone


def build_page_ledger(
    source_format: str,
    source_file: str,
    pages: list[dict],
    chapters: list[dict],
) -> dict:
    """Build a page ledger from parsed pages and detected chapters."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if source_format == "pdf":
        entries = []
        for page in pages:
            action, reason = page.get("action", "kept"), page.get("reason", "body text")
            section = _find_section(chapters, page["page"])
            entries.append(
                {
                    "page": page["page"],
                    "action": action,
                    "reason": reason,
                    "section": section,
                }
            )
        structure = [
            {
                "section": ch["section"],
                "number": ch["number"],
                "title": ch["title"],
                "pages": ch["pages"],
            }
            for ch in chapters
        ]
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
            section = _find_section(chapters, page["page"])
            entries.append(
                {
                    "item": page.get("item", f"page-{page['page']}"),
                    "action": action,
                    "reason": reason,
                    "section": section,
                }
            )
        structure = [
            {
                "section": ch["section"],
                "number": ch["number"],
                "title": ch["title"],
                "items": [p.get("item", f"page-{p}") for p in ch["pages"]],
            }
            for ch in chapters
        ]
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
        if sorted(page_numbers) != list(range(1, expected_count + 1)):
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
