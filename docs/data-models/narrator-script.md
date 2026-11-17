# Narrator Script

The narrator script is the **base text** for TTS generation. It is voice-agnostic — no voice-specific markup, no engine-specific parameters. It contains only the text to be narrated, structured by chapter and segment.

## Purpose

- Provide a clean, structured text source for any TTS engine
- Maintain page/item references for traceability
- Support chapter/section navigation
- Enable segment-level processing (parallel TTS, retry failed segments)

## Format

```json
{
  "book_id": "book-123",
  "job_id": "uuid",
  "title": "The Castle",
  "author": "Jane Doe",
  "source_format": "pdf",
  "source_file": "book.pdf",
  "language": "en",
  "chapters": [
    {
      "chapter": 1,
      "title": "The Arrival",
      "section": "chapter-1",
      "source_ref": "pages 6-12",
      "segments": [
        {
          "id": "seg-001",
          "text": "The castle loomed against the gray sky.",
          "source_ref": "page 6",
          "section": "chapter-1",
          "content_type": "body_text"
        },
        {
          "id": "seg-002",
          "text": "He paused at the gate, his hand resting on the hilt of his sword.",
          "source_ref": "page 6",
          "section": "chapter-1",
          "content_type": "body_text"
        },
        {
          "id": "seg-003",
          "text": "Chapter 2: The Gate",
          "source_ref": "page 13",
          "section": "chapter-2",
          "content_type": "chapter_heading"
        }
      ]
    },
    {
      "chapter": 2,
      "title": "The Gate",
      "section": "chapter-2",
      "source_ref": "pages 13-25",
      "segments": [
        {
          "id": "seg-004",
          "text": "The gate creaked open as he pushed against it.",
          "source_ref": "page 13",
          "section": "chapter-2",
          "content_type": "body_text"
        }
      ]
    }
  ],
  "frontmatter": {
    "dedication": {
      "text": "For my mother, who taught me to read.",
      "source_ref": "page 4",
      "section": "frontmatter"
    },
    "epigraphs": [
      {
        "chapter": 1,
        "text": "All that glitters is not gold.",
        "source_ref": "page 6",
        "section": "chapter-1"
      }
    ]
  },
  "metadata": {
    "total_segments": 1250,
    "total_chapters": 12,
    "total_pages": 342,
    "narrated_pages": 295,
    "estimated_word_count": 85000,
    "estimated_duration_minutes": 410,
    "created_at": "2026-11-17T10:00:00Z"
  }
}
```

## Field Definitions

### Top Level

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `book_id` | string | Yes | Unique book identifier |
| `job_id` | string | Yes | Job that produced this script |
| `title` | string | Yes | Book title |
| `author` | string | Yes | Author name |
| `source_format` | string | Yes | `pdf` or `epub` |
| `source_file` | string | Yes | Original filename |
| `language` | string | Yes | ISO 639-1 language code |
| `chapters` | array | Yes | Chapter array |
| `frontmatter` | object | No | Dedication, epigraphs, etc. |
| `metadata` | object | Yes | Statistics and timestamps |

### Chapter

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `chapter` | int | Yes | Chapter number |
| `title` | string | Yes | Chapter title |
| `section` | string | Yes | Section ID (matches page ledger) |
| `source_ref` | string | Yes | Page range or item reference |
| `segments` | array | Yes | Segment array |

### Segment

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | Yes | Unique segment ID (e.g., `seg-001`) |
| `text` | string | Yes | Text to be narrated |
| `source_ref` | string | Yes | Page number or item path |
| `section` | string | Yes | Section ID |
| `content_type` | string | Yes | Type of content |

### Content Types

| Type | Description |
|------|-------------|
| `body_text` | Main narrative |
| `chapter_heading` | Chapter title |
| `section_heading` | Section title |
| `dialogue` | Character dialogue |
| `epigraph` | Quote at start of chapter |
| `dedication` | Dedication text |
| `table` | Table content (read as text) |
| `footnote` | Footnote text |
| `note` | Author's note |

## Default Narration Policy

The prep worker uses this policy to decide what goes into the script:

### Included (narrated)

- Body text
- Chapter headings
- Section headings
- Dialogue
- Epigraphs
- Dedication
- Preface
- Foreword
- Introduction
- Tables (read as text)
- Conclusions
- Epilogues

### Excluded (not in script)

- Title page
- Copyright page
- Table of contents
- Illustrations (images)
- Diagrams
- Maps
- Acknowledgments
- Bibliography
- Index
- Glossary
- Publisher ads
- Colophon
- Blank pages
- Scanner artifacts

### Ambiguous (default: exclude, can be overridden)

- Footnotes
- Endnotes
- Sidebars
- Author's notes

## Segment Granularity

Segments are typically **one to three sentences**. This granularity:

- Enables parallel TTS processing
- Allows retrying failed segments without redoing the whole chapter
- Provides fine-grained progress tracking
- Keeps individual TTS calls within optimal length limits

## What's NOT in the Script

The base script is deliberately minimal. It does **not** contain:

- Voice assignments (narrator, character voices)
- Pacing instructions (pauses, speed changes)
- Emphasis markup
- TTS engine-specific parameters
- Audio descriptions for illustrations
- Chapter breaks or timing information

These are added by the **voice recipe** at the TTS stage. The script is the raw material; the recipe is the instruction set.

## Validation Rules

1. Every segment has a unique `id`
2. Every segment has non-empty `text`
3. Every segment has a valid `source_ref` that exists in the page ledger
4. Every chapter has at least one segment
5. `metadata.total_segments` equals the actual count
6. Segment IDs are sequential (`seg-001`, `seg-002`, ...)
