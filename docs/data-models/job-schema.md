# Job Schema

## Two separate job types

The pipeline has two job types. Each is submitted independently via `POST /jobs`.

- **Prep**: source document → book package. Status ends at `prep_complete`.
- **Convert**: book package → audiobook. Status ends at `complete`.

The convert job takes the `package_url` from the prep job as its `input_url`.

## Prep job

### Submission

```json
{
  "type": "prep",
  "input_format": "pdf",
  "input_url": "file:///var/lib/audiobook/input/book.pdf",
  "metadata": {
    "title": "Optional title override",
    "author": "Optional author override",
    "language": "en",
    "customer_id": "customer-123"
  }
}
```

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | Yes | `prep` |
| `input_format` | string | Yes | `pdf` or `epub` |
| `input_url` | string | Yes | `file://` URL or absolute path to the source document |
| `metadata` | object | No | Optional metadata. Extracted from the source when omitted. |
| `metadata.title` | string | No | Book title |
| `metadata.author` | string | No | Author name |
| `metadata.language` | string | No | Language code (e.g., `en`) |
| `metadata.customer_id` | string | No | Customer identifier |

### Queue message

The coordinator pushes this to the Redis `prep` queue:

```json
{
  "job_id": "uuid",
  "type": "prep",
  "input_format": "pdf",
  "input_url": "file:///var/lib/audiobook/input/book.pdf",
  "output_prefix": "file:///var/lib/audiobook/packages/{job_id}/",
  "metadata": {},
  "created_at": "2026-11-17T09:59:00Z"
}
```

### Completion

The prep worker POSTs to `/jobs/{job_id}/complete`:

```json
{
  "package_url": "file:///var/lib/audiobook/packages/{job_id}/",
  "total_pages": 342,
  "kept_pages": 318,
  "skipped_pages": 24
}
```

The job status moves to `prep_complete` and `package_url` is stored.

## Convert job

### Submission

```json
{
  "type": "convert",
  "input_format": "epub",
  "input_url": "file:///var/lib/audiobook/packages/{prep_job_id}/",
  "metadata": {
    "performance_style": "neutral",
    "voice_recipe": {}
  }
}
```

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | Yes | `convert` |
| `input_format` | string | Yes | `epub` (the book package format) |
| `input_url` | string | Yes | `package_url` from the prep job |
| `metadata` | object | No | Optional. See below. |
| `metadata.performance_style` | string | No | Performance style (default `neutral`) |
| `metadata.voice_recipe` | object | No | Voice recipe override. Empty object uses the default recipe image. |

The coordinator copies `input_url` to `package_url` in the database and in the queue message so TTS workers can read the package without remapping.

### Queue message

The coordinator pushes this to the Redis `convert` queue:

```json
{
  "job_id": "uuid",
  "type": "convert",
  "input_url": "file:///var/lib/audiobook/packages/{prep_job_id}/",
  "package_url": "file:///var/lib/audiobook/packages/{prep_job_id}/",
  "output_prefix": "file:///var/lib/audiobook/audio/{job_id}/",
  "voice_recipe": {},
  "performance_style": "neutral",
  "metadata": {},
  "created_at": "2026-11-17T10:05:00Z"
}
```

### Completion

The TTS worker POSTs to `/jobs/{job_id}/complete`:

```json
{
  "output_url": "file:///var/lib/audiobook/audio/{job_id}/"
}
```

The job status moves to `complete` and `output_url` is stored.

## Job state machine

### Prep job

```
submitted → in_progress → prep_complete
     │            │
     │            └→ failed (after max retries)
     │
     └→ cancelled
```

### Convert job

```
submitted → converting → complete
     │            │
     │            └→ convert_failed (after max retries)
     │
     └→ cancelled
```

### States

| State | Applies to | Description |
|-------|------------|-------------|
| `submitted` | both | Job accepted by coordinator, waiting in queue |
| `in_progress` | prep | Prep worker is processing the job |
| `prep_complete` | prep | Book package created. Terminal for prep. |
| `converting` | convert | TTS worker is generating audio |
| `complete` | convert | Audiobook ready. Terminal for convert. |
| `failed` | prep | Job failed after max retries |
| `convert_failed` | convert | TTS failed after max retries |
| `cancelled` | both | Job cancelled by operator |

On failure before max retries, the job returns to `submitted` and is requeued.

## Job record (coordinator database)

```json
{
  "job_id": "uuid",
  "type": "prep",
  "status": "prep_complete",
  "input_format": "pdf",
  "input_url": "file:///var/lib/audiobook/input/book.pdf",
  "package_url": "file:///var/lib/audiobook/packages/{job_id}/",
  "output_url": null,
  "metadata": {},
  "prep": {
    "worker_id": "worker-abc",
    "started_at": "2026-11-17T10:00:00Z",
    "completed_at": "2026-11-17T10:05:00Z",
    "total_pages": 342,
    "kept_pages": 318,
    "skipped_pages": 24
  },
  "convert": {},
  "progress": {},
  "error": null,
  "retry_count": 0,
  "created_at": "2026-11-17T09:59:00Z",
  "updated_at": "2026-11-17T10:05:00Z"
}
```

For a convert job, `type` is `convert`, `status` is `complete`, `output_url` is set, and the `convert` object contains `worker_id`, `started_at`, `completed_at`, `voice_recipe`, and `performance_style`.

## Progress reporting

Workers report progress via `POST /jobs/{job_id}/progress`. Only valid when the job is `in_progress` (prep) or `converting` (convert).

### Prep progress

```json
{
  "stage": "prep",
  "percent": 45,
  "current_page": 154,
  "total_pages": 342,
  "message": "Processing chapter 5"
}
```

### Convert progress

```json
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
