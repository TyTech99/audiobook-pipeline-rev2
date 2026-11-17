# Prep Worker

CPU worker that ingests PDFs/EPUBs and produces book packages (EPUB + narrator script + page ledger).

## Implementation Status

**Not yet implemented.** See design docs:

- [Architecture Overview](../../docs/architecture/overview.md)
- [Book Package](../../docs/data-models/book-package.md)
- [Page Ledger](../../docs/data-models/page-ledger.md)
- [Narrator Script](../../docs/data-models/narrator-script.md)

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
├── builders/
│   ├── epub.py          ← EPUB builder
│   ├── script.py        ← Narrator script builder
│   └── ledger.py        ← Page ledger builder
├── validation/
│   ├── parity.py        ← Page parity validation
│   └── package.py       ← Book package validation
└── requirements.txt
```

## Dependencies

- Python 3.11+
- PyMuPDF (PDF parsing)
- Calibre (EPUB manipulation)
- redis-py
- boto3 (S3)
- PyYAML
