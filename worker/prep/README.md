# Prep Worker

CPU worker that ingests PDFs/EPUBs and produces book packages (clean EPUB, narrator script, page ledger, cover art, metadata, LLM analysis).

## Implementation Status

**Not yet implemented.** See design docs:

- [Architecture Overview](../../docs/architecture/overview.md)
- [Book Package](../../docs/data-models/book-package.md)
- [Page Ledger](../../docs/data-models/page-ledger.md)
- [Narrator Script](../../docs/data-models/narrator-script.md)

## Processing Stages

The prep worker runs the following stages:

### 1. Ingestion

- Downloads the source document (PDF or EPUB) from S3
- Validates the file format and integrity

### 2. Parsing

- **PDF**: Uses PyMuPDF to extract text, images, and structure
- **EPUB**: Uses Calibre to extract content, images, and structure
- Identifies chapters, sections, and page boundaries

### 3. Classification

- Classifies each page's content type (body text, table of contents, front matter, back matter, images, etc.)
- Detects chapter/section boundaries
- Identifies garbage content (scanner artifacts, watermarks, etc.)

### 4. Cover Art Handling

1. **Extract from source**: Try to extract the cover image from the PDF/EPUB
2. **Search the internet**: If not found in the source, search for the book's cover online (by title + author)
3. **Flag for generation**: If not found, flag the book package as needing cover art

The cover art is stored as `cover.jpg` in the book package.

### 5. Metadata Extraction

1. Extract from PDF/EPUB metadata (PyMuPDF `doc.metadata`, XMP stream)
2. Extract ISBN from first 10-15 pages, look up metadata via isbnlib (Google Books, Open Library)
3. First-page layout analysis (largest font = title, subsequent text = author)
4. Internet search by title + author (if extraction fails)

Metadata extraction is best-effort. The user submits a PDF, and the pipeline extracts or finds metadata.

The metadata is stored as `metadata.json` in the book package.

### 6. LLM Analysis

The prep worker uses an LLM to analyze the book and produce:

- **Subject summary**: A brief summary of the book's subject matter
- **Author's tone analysis**: The author's tone, style, and voice
- **Narration style recommendations**: At least three recommended narration styles with reasons
- **Suggested voice recipes**: Voice recipes that match the book's content and tone

The LLM analysis is stored as `llm-analysis.json` in the book package.

### 7. Book Package Assembly

- Builds the clean EPUB (Kindle-ready, proper structure)
- Builds the narrator script (segments with source references)
- Builds the page ledger (page parity manifest)
- Assembles the book package (EPUB, script, ledger, cover, metadata, LLM analysis, manifest)
- Validates the package (page parity, EPUB validity, script validity, cross-references)
- Uploads the package to S3

## Planned Structure

```
worker/prep/
├── worker.py            ← Main worker loop (Redis BRPOP)
├── config.py            ← Configuration loading
├── processors/
│   ├── pdf.py           ← PDF ingestion pipeline
│   ├── epub.py          ← EPUB ingestion pipeline
│   └── base.py          ← Common processing logic
├── classifiers/
│   ├── page.py          ← Page content type classification
│   └── section.py       ← Chapter/section detection
├── cover/
│   ├── extractor.py     ← Cover art extraction from source
│   ├── searcher.py      ← Cover art search (internet)
│   └── placeholder.py   ← Placeholder cover generation
├── metadata/
│   ├── extractor.py     ← Metadata extraction (PDF/EPUB, ISBN, layout)
│   └── searcher.py      ← Metadata search (internet)
├── llm/
│   ├── analyzer.py      ← LLM analysis (subject, tone, style recommendations)
│   └── prompts.py       ← LLM prompt templates
├── builders/
│   ├── epub.py          ← EPUB builder
│   ├── script.py        ← Narrator script builder
│   ├── ledger.py        ← Page ledger builder
│   └── package.py       ← Book package assembly
├── validation/
│   ├── parity.py        ← Page parity validation
│   └── package.py       ← Book package validation
└── requirements.txt
```

## Dependencies

- Python 3.11+
- PyMuPDF (PDF parsing)
- Calibre (EPUB manipulation)
- isbnlib (ISBN lookup)
- LLM (for analysis) — cloud API or local model
- redis-py
- boto3 (S3)
- PyYAML
