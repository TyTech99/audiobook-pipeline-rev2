# Coordinator API

REST API for job submission, status tracking, and worker management.

## Base URL

```
http://coordinator:8000/api/v1
```

## Authentication

All endpoints require an API key in the `Authorization` header:

```
Authorization: Bearer <api-key>
```

## Jobs

### Submit a Job

```
POST /jobs
```

**Request:**

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
    "title": "The Castle",
    "author": "Jane Doe",
    "customer_id": "customer-123"
  }
}
```

**Response (201):**

```json
{
  "job_id": "uuid",
  "status": "submitted",
  "created_at": "2026-11-17T10:00:00Z"
}
```

### Get Job Status

```
GET /jobs/{job_id}
```

**Response (200):**

```json
{
  "job_id": "uuid",
  "type": "pdf_to_audiobook",
  "status": "prep_complete",
  "input_format": "pdf",
  "input_url": "s3://bucket/path/to/book.pdf",
  "package_url": "s3://bucket/packages/{job_id}/",
  "output_url": null,
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
  "created_at": "2026-11-17T09:59:00Z",
  "updated_at": "2026-11-17T10:05:00Z"
}
```

### List Jobs

```
GET /jobs?status=prep_complete&limit=50&offset=0
```

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `status` | string | all | Filter by status |
| `limit` | int | 50 | Max results |
| `offset` | int | 0 | Pagination offset |
| `customer_id` | string | - | Filter by customer |

**Response (200):**

```json
{
  "jobs": [...],
  "total": 150,
  "limit": 50,
  "offset": 0
}
```

### Get Job Page Ledger

```
GET /jobs/{job_id}/page-ledger
```

**Response (200):**

Returns the page ledger JSON (see [page-ledger.md](../data-models/page-ledger.md)).

### Get Job Script

```
GET /jobs/{job_id}/script
```

**Response (200):**

Returns the narrator script JSON (see [narrator-script.md](../data-models/narrator-script.md)).

### Cancel a Job

```
POST /jobs/{job_id}/cancel
```

**Response (200):**

```json
{
  "job_id": "uuid",
  "status": "cancelled"
}
```

## Workers

### Register a Worker

```
POST /workers/register
```

**Request:**

```json
{
  "worker_id": "unique-uuid",
  "node_id": "vps-1",
  "type": "prep",
  "capabilities": {
    "cpu": 8,
    "ram_gb": 15,
    "gpu": null,
    "tools": {"pdf_to_epub": true, "tts": false},
    "models": []
  },
  "queues": ["prep"],
  "working_hours": {
    "start": "22:00",
    "end": "08:00",
    "timezone": "America/New_York"
  }
}
```

**Response (201):**

```json
{
  "worker_id": "unique-uuid",
  "registered_at": "2026-11-17T10:00:00Z"
}
```

### Worker Heartbeat

```
POST /workers/{worker_id}/heartbeat
```

**Request:**

```json
{
  "status": "idle",
  "current_job": null,
  "queue_depth": 5
}
```

**Response (200):**

```json
{
  "status": "ok",
  "server_time": "2026-11-17T10:00:30Z"
}
```

### List Workers

```
GET /workers?status=online&type=prep
```

**Response (200):**

```json
{
  "workers": [
    {
      "worker_id": "worker-abc",
      "node_id": "vps-1",
      "type": "prep",
      "status": "online",
      "current_job": null,
      "last_heartbeat": "2026-11-17T10:00:30Z"
    }
  ],
  "total": 5
}
```

### Unregister a Worker

```
DELETE /workers/{worker_id}
```

**Response (200):**

```json
{
  "worker_id": "worker-abc",
  "unregistered_at": "2026-11-17T10:00:00Z"
}
```

## Progress Reporting

### Report Job Progress

```
POST /jobs/{job_id}/progress
```

**Request:**

```json
{
  "stage": "prep",
  "percent": 45,
  "current_page": 154,
  "total_pages": 342,
  "message": "Processing chapter 5"
}
```

**Response (200):**

```json
{
  "status": "ok"
}
```

## Health

### Health Check

```
GET /health
```

**Response (200):**

```json
{
  "status": "healthy",
  "redis": "connected",
  "postgres": "connected",
  "s3": "connected",
  "workers_online": 5,
  "queue_depths": {
    "prep": 3,
    "convert": 2,
    "failed": 0
  }
}
```

## Error Responses

All errors return a consistent format:

```json
{
  "error": {
    "code": "job_not_found",
    "message": "Job with id 'uuid' not found",
    "details": {}
  }
}
```

### Error Codes

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `invalid_request` | 400 | Malformed request body |
| `unauthorized` | 401 | Missing or invalid API key |
| `job_not_found` | 404 | Job ID doesn't exist |
| `worker_not_found` | 404 | Worker ID doesn't exist |
| `invalid_state` | 409 | Operation not valid for current job state |
| `internal_error` | 500 | Unexpected server error |
