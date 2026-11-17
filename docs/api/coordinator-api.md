# Coordinator API

REST API for job submission, status tracking, and worker management.

## Base URL

```
http://coordinator:8000/api/v1
```

## Authentication

Callers send an API key in the `Authorization` header:

```
Authorization: Bearer <api-key>
```

Two classes, defined in [ADR-005](../decisions/005-node-commissioning.md):

| Class | Held by | Can call |
| --- | --- | --- |
| Operator | Coordinator config | Jobs, list and read endpoints, node invite, patch, probe, rotate-key, drain, decommission |
| Node | One node, in `/etc/audiobook/node.env` | Node register and heartbeat, worker register, heartbeat, and unregister, probe failure, job progress |

`GET /health` takes no key. It returns `coordinator_id` and no keys. Every other endpoint returns 401 when the key is missing, unknown, or the wrong class for that route.

A worker or supervisor cannot create a node. `POST /nodes` with an operator key is the only authorization. Register calls with a missing bearer, an operator key, or a key for a node that was never invited return 401 and write nothing.

The coordinator stores SHA-256 hashes of keys. Node keys are created by `POST /nodes` and are not listed in `config/coordinator.yaml`. Operator keys are `auth.operator_api_keys`.

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

Requires a **node** key. `node_id` must already be `commissioned` or `offline`. `pending`, `draining`, and `decommissioned` return 409. The coordinator ignores `working_hours` on this call and keeps the node record's copy. `queues` must be a subset of the node's `effective_queues`.

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

Requires the node key for the worker's `node_id`. A probe container sends `"status": "probe_ok"` once, then exits.
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

Requires the node key for that worker. This drops one container. It does not decommission the node. Use `DELETE /nodes/{node_id}` for that.
**Response (200):**

```json
{
  "worker_id": "worker-abc",
  "unregistered_at": "2026-11-17T10:00:00Z"
}
```

## Nodes

Node lifecycle, including install and removal order, is in [Commissioning](../architecture/commissioning.md).

### Invite a Node

```
POST /nodes
```

Operator key. Creates a row in `pending` and returns the node API key once.

**Request:**

```json
{
  "node_id": "gpu-study",
  "display_name": "study GPU box",
  "allowed_queues": ["convert"],
  "working_hours": {
    "enabled": true,
    "start": "22:00",
    "end": "08:00",
    "timezone": "America/New_York"
  }
}
```

**Response (201):**

```json
{
  "node_id": "gpu-study",
  "state": "pending",
  "node_api_key": "shown-once",
  "created_at": "2026-11-17T10:00:00Z"
}
```

A second invite for a `decommissioned` id mints a new key and returns the row to `pending`. An id in any other state returns 409.

### Register a Node

```
POST /nodes/{node_id}/register
```

Node key. Supervisor calls this on startup.

**Request:**

```json
{
  "queues": ["convert"],
  "hardware": {
    "cpu": 16,
    "ram_gb": 64,
    "gpu": {"model": "RTX 4090", "vram_gb": 24}
  },
  "working_hours": {
    "enabled": true,
    "start": "22:00",
    "end": "08:00",
    "timezone": "America/New_York"
  }
}
```

**Response (200):**

```json
{
  "node_id": "gpu-study",
  "state": "commissioned",
  "effective_queues": ["convert"]
}
```

`effective_queues` is the overlap of `allowed_queues` and what the hardware report can run. No GPU drops `convert`. The coordinator replaces `working_hours` with the body of this call so the supervisor config is what gets stored. Register is idempotent while the node is `pending`, `commissioned`, `offline`, or `draining`. A second register refreshes hardware and does not return 409 because a heartbeat arrived recently. `draining` stays `draining`. The other three become `commissioned`. `decommissioned` returns 401 because the key is gone.

### Node Heartbeat

```
POST /nodes/{node_id}/heartbeat
```

Node key. Every 30 seconds. No request body.

**Response (200):**

```json
{
  "status": "ok",
  "server_time": "2026-11-17T10:00:30Z",
  "node_state": "commissioned",
  "command": "run"
}
```

`command` is `run`, `drain`, or `start_probe`. For `start_probe` the body also has `probe_id` and `worker_type`.

A missed heartbeat for 120 seconds moves `commissioned` to `offline`. The next successful heartbeat moves `offline` back to `commissioned`. `draining` and `decommissioned` do not change because of a heartbeat.

### Get a Node

```
GET /nodes/{node_id}
```

Operator key.

**Response (200):**

```json
{
  "node_id": "gpu-study",
  "display_name": "study GPU box",
  "state": "commissioned",
  "allowed_queues": ["convert"],
  "effective_queues": ["convert"],
  "hardware": {
    "cpu": 16,
    "ram_gb": 64,
    "gpu": {"model": "RTX 4090", "vram_gb": 24}
  },
  "working_hours": {
    "enabled": true,
    "start": "22:00",
    "end": "08:00",
    "timezone": "America/New_York"
  },
  "last_heartbeat_at": "2026-11-17T10:00:30Z",
  "last_probe": {
    "probe_id": "uuid",
    "worker_type": "tts",
    "worker_id": "uuid",
    "result": "passed"
  }
}
```

### List Nodes

```
GET /nodes?state=commissioned
```

Operator key. Same fields as get, without requiring `last_probe`. `state` is optional.

### Probe a Node

```
POST /nodes/{node_id}/probe
```

Operator key. The node must be `commissioned`.

**Request:**

```json
{
  "worker_type": "tts"
}
```

**Response (202):**

```json
{
  "probe_id": "uuid",
  "node_id": "gpu-study",
  "worker_type": "tts",
  "status": "pending"
}
```

The supervisor learns about the probe from its next heartbeat. The probe container registers a worker, heartbeats `probe_ok`, and exits. It does not claim a job. A pending probe older than 90 seconds becomes `expired` on the next heartbeat, and the command returns to `run` or `drain`.

### Report a Failed Probe

```
POST /nodes/{node_id}/probes/{probe_id}
```

Node key.

**Request:**

```json
{
  "result": "failed",
  "detail": "container exited 1"
}
```

**Response (200):**

```json
{
  "probe_id": "uuid",
  "status": "failed"
}
```

### Patch a Node

```
PATCH /nodes/{node_id}
```

Operator key. Changes `display_name`, `allowed_queues`, or `working_hours`. `effective_queues` is recomputed from the last hardware report. Omitted fields stay as they are.

### Rotate a Node Key

```
POST /nodes/{node_id}/rotate-key
```

Operator key. No request body.

**Response (200):**

```json
{
  "node_id": "gpu-study",
  "node_api_key": "shown-once",
  "previous_key_expires_at": "2026-11-17T10:10:00Z"
}
```

### Drain a Node

```
POST /nodes/{node_id}/drain
```

Operator key. Sets state to `draining`. Heartbeats then return `command: drain`.

**Response (200):**

```json
{
  "node_id": "gpu-study",
  "state": "draining",
  "grace_deadline": "2026-11-17T11:00:00Z"
}
```

`grace_deadline` is 60 minutes after the call. After that the supervisor may `SIGKILL` containers that are still running.

### Decommission a Node

```
DELETE /nodes/{node_id}
```

Operator key. Allowed from `pending`, `draining`, or `offline`. `pending` has no supervisor yet, so a bad invite can be thrown away without a drain. `commissioned` returns 409 until the operator drains it or the heartbeat times out into `offline`. Clears key hashes and sets `decommissioned`.

**Response (200):**

```json
{
  "node_id": "gpu-study",
  "state": "decommissioned",
  "decommissioned_at": "2026-11-17T11:05:00Z"
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

No API key. Process supervisors use this. It reports reachability of dependencies, not whether any node is commissioned. `coordinator_id` is the stable id nodes pin before they send a key. `s3` is `disabled` when no endpoint is configured, and `configured` when one is set. This check does not call S3.

**Response (200):**

```json
{
  "status": "healthy",
  "coordinator_id": "uuid",
  "redis": "connected",
  "postgres": "connected",
  "s3": "disabled",
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
| `unauthorized` | 401 | Missing or invalid API key, or the key class cannot call this route |
| `forbidden` | 403 | Node key used against a different `node_id` than the one it was issued for |
| `job_not_found` | 404 | Job ID doesn't exist |
| `worker_not_found` | 404 | Worker ID doesn't exist |
| `node_not_found` | 404 | Node ID doesn't exist |
| `invalid_state` | 409 | Operation not valid for the current job, worker, or node state |
| `internal_error` | 500 | Unexpected server error |
