# Prep Worker

CPU worker that ingests PDFs/EPUBs and produces book packages.

## Purpose

The prep worker claims jobs from the Redis `prep` queue, processes the source document, and writes a book package to local storage. The package contains a clean EPUB, narrator script, page ledger, cover art, metadata, and LLM analysis.

## Implementation status

Implemented (local-file mode). Parsing, page ledger, narrator script, EPUB assembly, cover extraction/search, metadata enrichment (ISBN lookup), and LLM analysis are all implemented in-tree. The worker Dockerfile is at `deploy/worker-prep.Dockerfile` and is built at node install.

## How it runs

The supervisor JIT-starts the prep worker container when there is work on the `prep` queue. The container runs:

```bash
python -m worker.prep.worker
```

The worker reads `config/worker-prep.yaml` (path overridable via `PREP_WORKER_CONFIG`). Key config values:

- `redis.*`: Redis connection for the job queue
- `coordinator.url` and `coordinator.api_key`: for registration, heartbeats, and completion reports
- `storage.root`: local storage root (default `/var/lib/audiobook`)
- `llm.*`: OpenAI-compatible LLM settings for book analysis

## Processing stages

1. **Ingestion**: reads the source document from `file://` URL or absolute path
2. **Parsing**: PyMuPDF for PDF, built-in parser for EPUB
3. **Classification**: classifies each page (body text, TOC, front matter, etc.)
4. **Cover art**: extracts from source, searches Open Library, or generates a placeholder
5. **Metadata**: extracts from source, scans for ISBN, enriches via Open Library
6. **LLM analysis**: subject summary, tone analysis, style recommendations (when LLM is configured)
7. **Package assembly**: builds EPUB, script, ledger, and writes all files to `packages/{job_id}/`

## File layout

```
worker/prep/
├── worker.py            ← Main worker loop (Redis BRPOP)
├── pdf_parser.py        ← PDF parsing with PyMuPDF
├── epub_parser.py       ← EPUB parsing
├── page_ledger.py       ← Page ledger build and validation
├── narrator_script.py   ← Narrator script build and validation
├── epub_builder.py      ← Clean EPUB assembly
├── cover.py             ← Cover extraction, Open Library search, placeholder
├── metadata_enrich.py   ← ISBN scanning and Open Library lookup
├── llm/
│   ├── analyzer.py      ← LLM analysis (subject, tone, style recommendations)
│   └── prompts.py       ← LLM prompt templates
└── tests/
    └── test_prep.py     ← Unit tests
```

## Dependencies

- Python 3.11+
- PyMuPDF (PDF parsing)
- Pillow (cover art)
- redis-py
- httpx
- PyYAML

## See also

- [Book Package](../../docs/data-models/book-package.md)
- [Page Ledger](../../docs/data-models/page-ledger.md)
- [Narrator Script](../../docs/data-models/narrator-script.md)
