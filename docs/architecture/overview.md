# System Architecture Overview

## High-Level Design

The audiobook pipeline is a distributed job processing system with three main components:

1. **Coordinator** — Central API and job router
2. **Workers** — Stateless processing units (prep or TTS)
3. **Supervisors** — Per-node process managers that JIT-start workers

## Component Responsibilities

### Coordinator

- Accepts job submissions via REST API
- Routes jobs to appropriate queues based on type and requirements
- Maintains worker registry (capabilities, status, working hours)
- Tracks job state and progress
- Stores job metadata in PostgreSQL (persistent)
- Uses Redis for job queues and real-time worker status

**Tech stack:** FastAPI, PostgreSQL, Redis

### Prep Worker (CPU)

- Ingests PDF or EPUB files
- Produces a "book package": clean EPUB + narrator script + page ledger
- Validates page parity before completing
- Uploads book package to S3 storage

**Tech stack:** Python, PyMuPDF (PDF parsing), Calibre (EPUB manipulation), Redis client

**Input formats:** PDF, EPUB
**Output:** Book package (EPUB + script.json + page-ledger.json)

### TTS Worker (GPU)

- Consumes book packages from the convert queue
- Applies a voice recipe to the narrator script
- Produces audio chunks and assembles final audiobook
- Uploads audio to S3 storage

**Tech stack:** Python, PyTorch, TTS engine (Chatterbox/Higgs V3), ffmpeg, Redis client

**Input:** Book package + voice recipe
**Output:** Audiobook (MP3/M4B) + segment metadata

### Supervisor

- Runs on each worker node (VPS, GPU machine, etc.)
- Detects hardware capabilities (CPU, RAM, GPU, VRAM)
- Registers the node with the coordinator
- Monitors queue depth and working hours
- Starts/stops Docker containers (workers) as needed
- Ensures workers are decommissioned when idle

**Tech stack:** Python, Docker SDK, Redis client

## Data Flow

```
1. User submits job
   POST /jobs {input_pdf: "s3://...", type: "pdf_to_audiobook"}
   
2. Coordinator creates job record, enqueues to "prep"
   
3. Prep worker claims job from "prep" queue
   - Downloads input from S3
   - Processes (PDF/EPUB → book package)
   - Validates page parity
   - Uploads book package to S3
   - Enqueues convert job to "convert" queue
   - Marks prep job complete
   
4. TTS worker claims job from "convert" queue
   - Downloads book package from S3
   - Applies voice recipe
   - Generates audio
   - Uploads audiobook to S3
   - Marks job complete
   
5. User retrieves audiobook
   GET /jobs/{id} → {status: "complete", output: "s3://..."}
```

## Queue Design

Redis lists are used as job queues:

| Queue | Purpose | Consumers |
|-------|---------|-----------|
| `prep` | PDF/EPUB ingestion jobs | CPU prep workers |
| `convert` | TTS conversion jobs | GPU TTS workers |
| `failed` | Jobs that exceeded retry limit | Manual inspection |

**Job claiming:** Workers use `BRPOP` (blocking pop) with a 30-second timeout. This provides near-real-time job dispatch without polling overhead.

**Visibility timeout:** When a worker claims a job, it's moved to a `processing:{worker_id}` list. If the worker doesn't acknowledge completion within the timeout (configurable, default 1 hour), the job is returned to the main queue.

**Dead letter queue:** Jobs that fail 3+ times are moved to the `failed` queue with error details.

## Worker Registration

Workers register with the coordinator on startup:

```json
POST /workers/register
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

Workers send heartbeats every 30 seconds. If the coordinator doesn't receive a heartbeat for 2 minutes, the worker is marked offline and its in-progress jobs are returned to the queue.

## Storage Layout

```
s3://audiobook-pipeline/
├── inputs/
│   └── {job_id}/
│       └── source.pdf (or source.epub)
├── packages/
│   └── {job_id}/
│       ├── book.epub
│       ├── script.json
│       └── page-ledger.json
├── audio/
│   └── {job_id}/
│       ├── segments/
│       │   ├── chapter-001/
│       │   │   ├── seg-001.mp3
│       │   │   └── ...
│       │   └── ...
│       ├── final/
│       │   └── audiobook.mp3
│       └── metadata.json
└── logs/
    └── {job_id}/
        └── worker.log
```

## Scaling

- **Add more prep workers:** Run more supervisors on CPU nodes. They automatically register and start claiming from the `prep` queue.
- **Add more TTS workers:** Run more supervisors on GPU nodes. They register with GPU capabilities and claim from the `convert` queue.
- **Scale the coordinator:** The coordinator is stateless (state is in PostgreSQL + Redis). Run multiple instances behind a load balancer if needed.

## Failure Modes

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Worker crashes | Missed heartbeat | Jobs returned to queue after visibility timeout |
| Worker node dies | Missed heartbeat | Same as above |
| Job processing error | Worker reports failure | Job retried up to 3 times, then moved to `failed` queue |
| Storage unavailable | Upload/download error | Job retried with exponential backoff |
| Coordinator down | Workers can't register/claim | Workers queue jobs locally, retry when coordinator is back |

## Security

- All inter-service communication over TLS
- S3 access via IAM roles (no long-lived credentials)
- Redis with password authentication
- Coordinator API with API key authentication
- Workers authenticate to coordinator with signed tokens
