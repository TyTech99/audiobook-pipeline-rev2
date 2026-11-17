# Page Ledger

The page ledger is the **page parity guarantee**. It accounts for every page in the source document, ensuring nothing is silently dropped during conversion.

## Purpose

- Prove that every page in the source PDF was seen and accounted for
- Document why pages were kept or skipped
- Provide a map of the document's structure (sections, chapters)
- Enable debugging and quality control
- Support partial reprocessing (know which pages belong to which chapter)

## Format: PDF Source

```json
{
  "source_format": "pdf",
  "source_file": "book.pdf",
  "total_pages": 342,
  "pages": [
    {
      "page": 1,
      "action": "kept",
      "reason": "title page",
      "section": "frontmatter"
    },
    {
      "page": 2,
      "action": "kept",
      "reason": "copyright",
      "section": "frontmatter"
    },
    {
      "page": 3,
      "action": "skipped",
      "reason": "blank",
      "section": "frontmatter"
    },
    {
      "page": 4,
      "action": "skipped",
      "reason": "illustration only",
      "section": "frontmatter"
    },
    {
      "page": 5,
      "action": "kept",
      "reason": "table of contents",
      "section": "frontmatter"
    },
    {
      "page": 6,
      "action": "kept",
      "reason": "body text",
      "section": "chapter-1"
    },
    {
      "page": 7,
      "action": "kept",
      "reason": "body text",
      "section": "chapter-1"
    },
    {
      "page": 8,
      "action": "kept",
      "reason": "chapter heading",
      "section": "chapter-2"
    },
    ...
    {
      "page": 342,
      "action": "skipped",
      "reason": "publisher ad",
      "section": "backmatter"
    }
  ],
  "structure": [
    {
      "section": "frontmatter",
      "title": "Front Matter",
      "pages": [1, 2, 3, 4, 5]
    },
    {
      "section": "chapter-1",
      "number": 1,
      "title": "The Arrival",
      "pages": [6, 7]
    },
    {
      "section": "chapter-2",
      "number": 2,
      "title": "The Gate",
      "pages": [8, 9, 10, 11]
    },
    ...
    {
      "section": "backmatter",
      "title": "Back Matter",
      "pages": [340, 341, 342]
    }
  ],
  "summary": {
    "total": 342,
    "kept": 318,
    "skipped": 24,
    "skipped_breakdown": {
      "blank": 8,
      "illustration": 5,
      "frontmatter": 6,
      "backmatter": 5
    }
  },
  "created_at": "2026-11-17T10:00:00Z"
}
```

## Format: EPUB Source

EPUBs don't have pages, so the ledger accounts for **items** (XHTML files, images, etc.):

```json
{
  "source_format": "epub",
  "source_file": "book.epub",
  "total_items": 45,
  "items": [
    {
      "item": "OEBPS/title.xhtml",
      "action": "kept",
      "reason": "title page",
      "section": "frontmatter"
    },
    {
      "item": "OEBPS/copyright.xhtml",
      "action": "kept",
      "reason": "copyright",
      "section": "frontmatter"
    },
    {
      "item": "OEBPS/toc.xhtml",
      "action": "skipped",
      "reason": "navigation only",
      "section": "frontmatter"
    },
    {
      "item": "OEBPS/chapter-001.xhtml",
      "action": "kept",
      "reason": "body text",
      "section": "chapter-1"
    },
    {
      "item": "OEBPS/chapter-002.xhtml",
      "action": "kept",
      "reason": "body text",
      "section": "chapter-2"
    },
    {
      "item": "OEBPS/images/castle.png",
      "action": "skipped",
      "reason": "illustration",
      "section": "chapter-3"
    },
    ...
  ],
  "structure": [
    {
      "section": "frontmatter",
      "title": "Front Matter",
      "items": ["OEBPS/title.xhtml", "OEBPS/copyright.xhtml", "OEBPS/toc.xhtml"]
    },
    {
      "section": "chapter-1",
      "number": 1,
      "title": "The Arrival",
      "items": ["OEBPS/chapter-001.xhtml"]
    },
    ...
  ],
  "summary": {
    "total": 45,
    "kept": 38,
    "skipped": 7,
    "skipped_breakdown": {
      "navigation": 1,
      "illustration": 3,
      "backmatter": 3
    }
  },
  "created_at": "2026-11-17T10:00:00Z"
}
```

## Field Definitions

### Page/Item Entry

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `page` or `item` | int/string | Yes | Page number (PDF) or item path (EPUB) |
| `action` | string | Yes | `kept` or `skipped` |
| `reason` | string | Yes | Human-readable explanation |
| `section` | string | Yes | Section ID (e.g., `frontmatter`, `chapter-1`, `backmatter`) |
| `note` | string | No | Additional context (optional) |

### Action Values

| Action | Meaning |
|--------|---------|
| `kept` | Content included in the output EPUB |
| `skipped` | Intentionally excluded, with documented reason |

### Reason Values (Common)

| Reason | Description |
|--------|-------------|
| `title page` | Book title, author |
| `copyright` | Legal/copyright information |
| `dedication` | Dedication page |
| `table of contents` | TOC |
| `preface` | Author's preface |
| `foreword` | Foreword by another author |
| `introduction` | Introduction |
| `body text` | Main narrative content |
| `chapter heading` | Chapter title/number |
| `section heading` | Section title |
| `blank` | Empty page |
| `illustration` | Image-only page |
| `diagram` | Technical diagram |
| `table` | Data table |
| `footnote` | Footnote content |
| `endnote` | Endnote content |
| `sidebar` | Sidebar/aside content |
| `acknowledgments` | Author's acknowledgments |
| `bibliography` | References/bibliography |
| `index` | Index pages |
| `glossary` | Glossary |
| `publisher ad` | Advertising content |
| `colophon` | Production information |
| `scanner artifact` | Scanner noise/garbage |
| `duplicate` | Duplicate of another page |

### Section Values

| Section | Description |
|---------|-------------|
| `frontmatter` | Title, copyright, TOC, dedication, preface, etc. |
| `chapter-N` | Numbered chapters (N = chapter number) |
| `part-N` | Parts/volumes |
| `appendix-N` | Appendices |
| `backmatter` | Index, bibliography, glossary, ads, etc. |
| `unassigned` | Could not be determined (flagged for review) |

## Validation Rules

A page ledger is valid if and only if:

1. **Completeness**: `len(pages)` equals `total_pages` (PDF) or `total_items` (EPUB)
2. **No gaps**: Pages/items are numbered/sequenced with no gaps or duplicates
3. **Actions valid**: Every entry has `action` of `kept` or `skipped`
4. **Reasons present**: Every entry has a non-empty `reason`
5. **Sections present**: Every entry has a non-empty `section`
6. **Structure matches**: The `structure` array's page/item lists match the individual entries
7. **Summary accurate**: `summary.kept + summary.skipped == summary.total`

## Validation Code (Reference)

```python
def validate_page_ledger(ledger, source_page_count):
    errors = []
    
    if ledger["source_format"] == "pdf":
        entries = ledger["pages"]
        entry_key = "page"
        expected_count = source_page_count
    else:
        entries = ledger["items"]
        entry_key = "item"
        expected_count = ledger["total_items"]
    
    # 1. Completeness
    if len(entries) != expected_count:
        errors.append(f"Expected {expected_count} entries, got {len(entries)}")
    
    # 2. No gaps (PDF only)
    if ledger["source_format"] == "pdf":
        page_numbers = [e["page"] for e in entries]
        if sorted(page_numbers) != list(range(1, expected_count + 1)):
            errors.append("Page numbers have gaps or duplicates")
    
    # 3-5. Field validation
    for e in entries:
        if e["action"] not in ("kept", "skipped"):
            errors.append(f"Invalid action: {e['action']}")
        if not e.get("reason"):
            errors.append(f"Missing reason for {entry_key} {e[entry_key]}")
        if not e.get("section"):
            errors.append(f"Missing section for {entry_key} {e[entry_key]}")
    
    # 7. Summary accuracy
    kept = sum(1 for e in entries if e["action"] == "kept")
    skipped = sum(1 for e in entries if e["action"] == "skipped")
    if ledger["summary"]["kept"] != kept:
        errors.append(f"Summary kept ({ledger['summary']['kept']}) != actual ({kept})")
    if ledger["summary"]["skipped"] != skipped:
        errors.append(f"Summary skipped ({ledger['summary']['skipped']}) != actual ({skipped})")
    
    return errors
```
