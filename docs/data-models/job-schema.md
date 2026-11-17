# Job Schema

## Job Payload

### Submission

```json
{
  "type": "pdf_to_audiobook",
  "input_format": "pdf",
  "input_url": "s3://bucket/path/to/book.pdf",
  "options": {
    "output_format": "mp3",
    "bitrate": "192k",
    "narration_policy": "default"
  },
  "metadata": {
    "title": "Optional title override",
    "author": "Optional author override",
    "customer_id": "customer-123"
  }
}
```

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | Yes | Job type: `pdf_to_audiobook`, `epub_to_audiobook`, `pdf_to_epub`, `epub_to_script` |
| `input_format` | string | Yes | `pdf` or `epub` |
| `input_url` | string | Yes | S3 URL of the input file |
| `options` | object | No | Processing options |
| `options.output_format` | string | No | Audio format: `mp3`, `m4b`, `wav` (default: `mp3`) |
| `options.bitrate` | string | No | Audio bitrate (default: `192k`) |
| `options.narration_policy` | string | No | Policy name: `default` or custom policy ID |
| `metadata` | object | No | Optional metadata overrides |

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
| `prep_complete` | Book package created, waiting for TTS worker |
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
  "options": {...},
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
  "options": {...},
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
  "options": {
    "output_format": "mp3",
    "bitrate": "192k"
  },
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
