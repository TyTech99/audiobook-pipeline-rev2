# System Architecture Overview

## High-Level Design

The audiobook pipeline is a distributed job processing system with three main components:

1. **Coordinator** — Central API and job router
2. **Workers** — Stateless processing units (prep or TTS)
3. **Supervisors** — Per-node process managers that JIT-start workers

Voice recipes live in a **separate git repo** from the pipeline code. This enables both systems to iterate independently.

## Component Responsibilities

### Coordinator

- Accepts job submissions via REST API
- Routes jobs to appropriate queues based on type and requirements
- Maintains worker registry (capabilities, status, working hours)
- Tracks job state and progress
- Stores job metadata in PostgreSQL (persistent)
- Uses Redis for job queues and real-time worker status
- Fetches voice recipes from the recipe repo
- Validates performance style against recipe's supported styles

**Tech stack:** FastAPI, PostgreSQL, Redis

### Prep Worker (CPU)

- Ingests PDF or EPUB files
- Produces a "book package": clean EPUB + narrator script + page ledger + cover art + metadata + LLM analysis
- Validates page parity before completing
- Uploads book package to S3 storage

**Cover art handling:**
1. Extract from source (PDF/EPUB)
2. Search the internet (by title + author)
3. Flag for generation (if not found)

**Metadata extraction:**
1. Extract from PDF/EPUB metadata (PyMuPDF `doc.metadata`, XMP stream)
2. Extract ISBN from first 10-15 pages, look up metadata via isbnlib (Google Books, Open Library)
3. First-page layout analysis (largest font = title, subsequent text = author)
4. Internet search by title + author (if extraction fails)

**LLM analysis:**
- Book subject summary
- Author's tone analysis
- At least three narration style recommendations
- Suggested voice recipes matching the book's content

**Tech stack:** Python, PyMuPDF (PDF parsing), Calibre (EPUB manipulation), isbnlib, LLM, Redis client

**Input formats:** PDF, EPUB
**Output:** Book package (EPUB + script.json + page-ledger.json + cover.jpg + metadata.json + llm-analysis.json + cleanup-log.json + fidelity-report.json)

### TTS Worker (GPU)

- Consumes book packages from the convert queue
- Runs the full pipeline inside the recipe's Docker container: LLM annotation → TTS generation → mastering → packaging
- Uploads the audiobook package to S3 storage

**Pipeline stages (inside the recipe's Docker container):**

1. **LLM Annotation** — Annotates the clean script with engine-specific markup (emotion, pacing, pauses). Chapters run in parallel; chunks within a chapter run serially with state passing. Chapters are emotional reset points.
2. **TTS Generation** — Generates audio from the annotated script using the voice recipe's TTS engine.
3. **Mastering** — Applies the mastering chain (EQ, compression, loudness, noise floor, room tone).
4. **Packaging** — Assembles the final audiobook package (M4B + MP3 chapters + credits + sample + metadata + QC report).

**Parallelism model:**
- Chapters are the unit of parallelism (annotation and TTS)
- Chunks/segments within a chapter are serial (state dependency)
- Chapters are reset points for emotional and audio state

**LLM slot capabilities:**
- Cloud LLMs: unlimited parallel slots
- Local LLMs: seat count declared in worker registration
- The coordinator schedules chapter annotation based on available slots

**Tech stack:** Python, PyTorch, TTS engine (Chatterbox/Higgs V3), LLM (for annotation), FFmpeg (mastering/packaging), Redis client

**Input:** Book package + voice recipe + performance style
**Output:** Audiobook package (M4B + MP3 chapters + credits + sample + metadata + QC report)

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
Phase 1: Prep
1. User submits job
   POST /jobs {input_pdf: "s3://...", type: "prep"}
   
2. Coordinator creates job record, enqueues to "prep"
   
3. Prep worker claims job from "prep" queue
   - Downloads input from S3
   - Processes (PDF/EPUB → book package)
   - Extracts cover art, metadata, LLM analysis
   - Validates page parity
   - Uploads book package to S3
   - Marks prep job complete (status: prep_complete)

Phase 2: Audio
4. User submits Phase 2 job
   POST /jobs {book_package_id: "uuid", voice_recipe: {...}, performance_style: "noir-mystery", type: "convert"}
   
5. Coordinator validates performance style against recipe, enqueues to "convert"
   
6. TTS worker claims job from "convert" queue
   - Downloads book package from S3
   - Pulls voice recipe Docker image
   - Runs: LLM annotation → TTS generation → mastering → packaging
   - Uploads audiobook package to S3
   - Marks job complete (status: complete)
   
7. User retrieves audiobook
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

A machine joins the fleet as a **node** before any container starts. The supervisor heartbeats the node. Containers still register as workers when they start. Node invite, probe, drain, and decommission are specified in [Commissioning](commissioning.md) and [ADR-005](../decisions/005-node-commissioning.md).

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
    "models": [],
    "llm_slots": 4
  },
  "queues": ["prep"],
  "working_hours": {
    "start": "22:00",
    "end": "08:00",
    "timezone": "America/New_York"
  }
}
```

For TTS workers:

```json
POST /workers/register
{
  "worker_id": "unique-uuid",
  "node_id": "gpu-1",
  "type": "tts",
  "capabilities": {
    "cpu": 16,
    "ram_gb": 64,
    "gpu": {"model": "RTX 4090", "vram_gb": 24},
    "tools": {"tts": true},
    "models": [],
    "llm_slots": 8
  },
  "queues": ["convert"],
  "working_hours": {
    "start": "22:00",
    "end": "08:00",
    "timezone": "America/New_York"
  }
}
```

The supervisor sends those `working_hours`, and the node row keeps them. A worker registration may repeat the block, but the coordinator does not let the container change the node's hours or queues.

Workers send heartbeats every 30 seconds. The supervisor does the same for the node. If the coordinator receives no worker heartbeat for 2 minutes, that container is marked offline and its in-progress jobs are returned to the queue. A missed supervisor heartbeat marks the node offline and leaves the node record in place.

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
│       ├── page-ledger.json
│       ├── cover.jpg
│       ├── metadata.json
│       ├── llm-analysis.json
│       ├── cleanup-log.json
│       ├── fidelity-report.json
│       └── manifest.json
├── audio/
│   └── {job_id}/
│       ├── m4b/
│       │   └── audiobook.m4b
│       ├── mp3/
│       │   ├── opening-credits.mp3
│       │   ├── chapter-001.mp3
│       │   ├── chapter-002.mp3
│       │   ├── ...
│       │   ├── closing-credits.mp3
│       │   └── retail-sample.mp3
│       ├── metadata.json
│       └── qc-report.json
└── logs/
    └── {job_id}/
        └── worker.log
```

## Scaling

- **Add a prep node:** Commission a CPU machine with the supervisor. The node record is durable. Containers register only while they run. Steps are in [Commissioning](commissioning.md).
- **Add a TTS node:** Same procedure on a GPU machine. `convert` stays off `effective_queues` unless the hardware report includes a GPU.
- **Scale the coordinator:** The coordinator is stateless (state is in PostgreSQL + Redis). Run multiple instances behind a load balancer if needed.

## Failure Modes

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Worker crashes | Missed heartbeat | Jobs returned to queue after visibility timeout |
| Worker node dies | Missed heartbeat | Same as above |
| Job processing error | Worker reports failure | Job retried up to 3 times, then moved to `failed` queue |
| Storage unavailable | Upload/download error | Job retried with exponential backoff |
| Coordinator down | Workers can't register or heartbeat | Workers keep claiming from Redis and retry coordinator calls when it returns. They do not spool jobs on the node. |

## Security

- Coordinator and supervisor traffic uses HTTPS. HTTP is only for a single-host dev stand bound to localhost.
- Two API key classes, operator and per-node. See [ADR-005](../decisions/005-node-commissioning.md). Signed tokens are not part of v1.
- Redis requires a password, and the port accepts connections only from the coordinator host and commissioned nodes. A node API key does not gate `BRPOP`.
- S3 credentials are injected by the supervisor when it starts a container. They are not baked into the worker image.
