# Job Schema

## Two-Phase Job Model

A job has two distinct phases. They are separate, sequential, and the second phase depends on the first.

### Phase 1: Prep (Source Document → Book Package)

- **Input**: PDF or EPUB (minimum submission requirement)
- **Output**: Book package (clean EPUB, narrator script, page ledger, cover art, metadata, LLM analysis)

### Phase 2: Audio (Book Package → Audiobook)

- **Input**: Book package + voice recipe + performance style
- **Output**: Audiobook package (M4B file, MP3 chapters, credits, sample, metadata, QC report)

## Job Payload

### Phase 1 Submission

```json
{
  "type": "prep",
  "input_format": "pdf",
  "input_url": "s3://bucket/path/to/book.pdf",
  "metadata": {
    "title": "Optional title override",
    "author": "Optional author override",
    "genre": "Optional genre",
    "description": "Optional description",
    "language": "en",
    "year": "2026",
    "customer_id": "customer-123"
  }
}
```

### Phase 1 Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | Yes | `prep` |
| `input_format` | string | Yes | `pdf` or `epub` |
| `input_url` | string | Yes | S3 URL of the input file |
| `metadata` | object | No | Optional metadata (extracted when omitted) |
| `metadata.title` | string | No | Book title |
| `metadata.author` | string | No | Author name |
| `metadata.genre` | string | No | Genre |
| `metadata.description` | string | No | Book summary |
| `metadata.language` | string | No | Language code (e.g., `en`) |
| `metadata.year` | string | No | Publication year |
| `metadata.customer_id` | string | No | Customer identifier |

### Phase 2 Submission

```json
{
  "type": "convert",
  "book_package_id": "uuid",
  "voice_recipe": {
    "name": "narrator-deep-male-v2",
    "version": "2.1.0"
  },
  "performance_style": "noir-mystery"
}
```

### Phase 2 Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | Yes | `convert` |
| `book_package_id` | string | Yes | Reference to the Phase 1 output |
| `voice_recipe` | object | Yes | Voice recipe to use |
| `voice_recipe.name` | string | Yes | Recipe name |
| `voice_recipe.version` | string | Yes | Recipe version |
| `performance_style` | string | Yes | Performance style (validated against recipe's supported styles) |

## Job State Machine

```
submitted → in_progress → prep_complete → converting → complete
                │               │
                │               └→ convert_failed
                │
                └→ failed
```

### States

| State | Description |
|-------|-------------|
| `submitted` | Job accepted by coordinator, waiting in queue |
| `in_progress` | Prep worker is processing the job |
| `prep_complete` | Book package created, waiting for Phase 2 submission |
| `converting` | TTS worker is generating audio |
| `complete` | Audiobook ready |
| `failed` | Job failed after max retries |
| `convert_failed` | Prep succeeded but TTS failed |

## Job Record (Coordinator Database)

```json
{
  "job_id": "uuid",
  "type": "pdf_to_audiobook",
  "status": "prep_complete",
  "input_format": "pdf",
  "input_url": "s3://bucket/path/to/book.pdf",
  "package_url": "s3://bucket/packages/{job_id}/",
  "output_url": null,
  "metadata": {...},
  "prep": {
    "worker_id": "worker-abc",
    "started_at": "2026-11-17T10:00:00Z",
    "completed_at": "2026-11-17T10:05:00Z",
    "total_pages": 342,
    "kept_pages": 318,
    "skipped_pages": 24
  },
  "convert": {
    "worker_id": null,
    "voice_recipe": null,
    "performance_style": null,
    "started_at": null,
    "completed_at": null,
    "progress": null
  },
  "error": null,
  "retry_count": 0,
  "created_at": "2026-11-17T09:59:00Z",
  "updated_at": "2026-11-17T10:05:00Z"
}
```

## Queue Message Format

### Prep Queue Message

```json
{
  "job_id": "uuid",
  "type": "prep",
  "input_format": "pdf",
  "input_url": "s3://bucket/path/to/book.pdf",
  "output_prefix": "s3://bucket/packages/{job_id}/",
  "metadata": {...},
  "created_at": "2026-11-17T09:59:00Z"
}
```

### Convert Queue Message

```json
{
  "job_id": "uuid",
  "type": "convert",
  "package_url": "s3://bucket/packages/{job_id}/",
  "output_prefix": "s3://bucket/audio/{job_id}/",
  "voice_recipe": {
    "name": "narrator-deep-male-v2",
    "version": "2.1.0",
    "image": "registry.example.com/audiobook/voice-deep-male-v2@sha256:..."
  },
  "performance_style": "noir-mystery",
  "created_at": "2026-11-17T10:05:00Z"
}
```

## Progress Reporting

Workers report progress via the coordinator API:

```json
POST /jobs/{job_id}/progress
{
  "stage": "prep",
  "percent": 45,
  "current_page": 154,
  "total_pages": 342,
  "message": "Processing chapter 5"
}
```

For TTS jobs:

```json
POST /jobs/{job_id}/progress
{
  "stage": "convert",
  "percent": 62,
  "current_chapter": 8,
  "total_chapters": 12,
  "current_segment": 950,
  "total_segments": 1500,
  "estimated_minutes_remaining": 45
}
```
