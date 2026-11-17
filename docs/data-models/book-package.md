# Book Package

The book package is the **output of the prep stage** and the **input to the TTS stage**. It is a voice-agnostic bundle that contains everything needed to produce an audiobook, except the voice itself.

## Structure

```
s3://bucket/packages/{job_id}/
├── book.epub              ← Clean, Kindle-ready EPUB
├── script.json            ← Base narrator script
├── page-ledger.json       ← Page parity manifest
├── images/                ← Extracted images (if any kept)
│   ├── figure-001.png
│   └── ...
└── manifest.json          ← Package metadata
```

## Manifest

```json
{
  "package_id": "uuid",
  "job_id": "uuid",
  "version": "1.0",
  "created_at": "2026-11-17T10:05:00Z",
  "source": {
    "format": "pdf",
    "file": "book.pdf",
    "url": "s3://bucket/inputs/{job_id}/source.pdf",
    "page_count": 342
  },
  "book": {
    "title": "The Castle",
    "author": "Jane Doe",
    "language": "en",
    "isbn": null
  },
  "artifacts": {
    "epub": "book.epub",
    "script": "script.json",
    "page_ledger": "page-ledger.json",
    "images": "images/"
  },
  "statistics": {
    "total_pages": 342,
    "kept_pages": 318,
    "skipped_pages": 24,
    "total_segments": 1250,
    "total_chapters": 12,
    "word_count": 85000,
    "estimated_duration_minutes": 410
  },
  "validation": {
    "page_parity": true,
    "epub_valid": true,
    "script_valid": true,
    "validated_at": "2026-11-17T10:05:00Z"
  }
}
```

## EPUB Requirements

The EPUB in the book package must be:

- Valid EPUB 3.0+
- Kindle-compatible (no proprietary extensions)
- Clean structure (proper `<nav>`, `<section>`, headings)
- All images embedded and referenced correctly
- No scanner artifacts or garbage content
- Table of contents matching the actual chapter structure

## Script Requirements

See [narrator-script.md](narrator-script.md) for the full format.

## Page Ledger Requirements

See [page-ledger.md](page-ledger.md) for the full format.

## Validation

Before a book package is marked complete, the prep worker must validate:

1. **Page parity**: Page ledger accounts for every page in the source
2. **EPUB validity**: EPUB opens without errors, valid XML, proper structure
3. **Script validity**: All segments have valid source references
4. **Cross-references**: 
   - Every `source_ref` in the script exists in the page ledger
   - Every page marked `kept` in the ledger appears in the EPUB or script
   - Chapter structure is consistent across all three artifacts

If any validation fails, the job is marked `failed` with detailed error messages.

## Consumption by TTS Workers

TTS workers consume the book package as follows:

1. Download the package from S3
2. Read `manifest.json` to understand the package
3. Read `script.json` to get the text to narrate
4. Apply the voice recipe to each segment
5. Produce audio chunks
6. Assemble final audiobook

The TTS worker does **not** need the EPUB or page ledger for audio generation. Those are for the reader and the audit trail, respectively.
