# Audiobook Pipeline Rev2

Distributed PDF/EPUB to audiobook conversion pipeline with job queue, worker supervision, and page parity guarantees.

## What This Is

A system that converts PDFs and EPUBs into audiobooks using a distributed worker architecture. The pipeline has two main stages:

1. **Prep** (CPU workers): PDF/EPUB → clean EPUB + narrator script + page ledger
2. **Convert** (GPU workers): narrator script + voice recipe → audiobook

## Core Principles

- **Page Parity**: Every page in the source PDF is accounted for in a page ledger. No content silently disappears.
- **Voice-Agnostic Prep**: The prep stage produces a base script with no voice-specific markup. Voice selection happens at the TTS stage.
- **JIT Workers**: Workers spin up (Docker containers) when there's work and spin down when idle. GPU workers release their GPU when not in use.
- **Decommissionable**: Any worker can be added or removed without affecting the system. Workers identify by ID, not IP.
- **Working Hours**: Workers can declare operating schedules (e.g., 10 PM - 8 AM) for cost optimization or resource sharing.

## Architecture Overview

```
┌─────────────────┐         ┌──────────────────┐
│   Coordinator   │         │     Workers      │
│  (FastAPI)      │◄───────►│  (Docker)        │
│                 │  Redis  │                  │
│  - Job API      │  Queue  │  - Prep (CPU)    │
│  - Worker reg   │         │  - TTS (GPU)     │
│  - Job routing  │         │                  │
└─────────────────┘         └──────────────────┘
         │                           │
         ▼                           ▼
┌─────────────────┐         ┌──────────────────┐
│   Storage (S3)  │         │   Supervisor     │
│                 │         │  (per node)      │
│  - Inputs       │         │  - Hardware det  │
│  - Book packages│         │  - Container mgmt│
│  - Audio output │         │  - Working hours │
└─────────────────┘         └──────────────────┘
```

## Repository Structure

```
├── README.md                 ← You are here
├── docs/
│   ├── architecture/         ← System design, component specs
│   │   ├── overview.md       ← High-level architecture
│   │   ├── coordinator.md    ← Coordinator API and logic
│   │   ├── worker-prep.md    ← Prep worker (PDF/EPUB → book package)
│   │   ├── worker-tts.md     ← TTS worker (book package → audio)
│   │   └── supervisor.md     ← Node supervisor (JIT container management)
│   ├── data-models/          ← Data schemas and formats
│   │   ├── job-schema.md     ← Job payload and state machine
│   │   ├── book-package.md   ← EPUB + script + ledger structure
│   │   ├── page-ledger.md    ← Page parity manifest format
│   │   ├── narrator-script.md← Base narrator script format
│   │   └── voice-recipe.md   ← Voice recipe structure (TTS stage)
│   ├── api/                  ← API specifications
│   │   ├── coordinator-api.md← REST API for job submission/status
│   │   └── worker-api.md     ← Internal worker-coordinator protocol
│   └── decisions/            ← Architecture Decision Records (ADRs)
│       ├── 001-redis-queue.md
│       ├── 002-page-parity.md
│       ├── 003-jit-workers.md
│       └── 004-voice-agnostic-prep.md
├── schemas/                  ← JSON schemas for validation
│   ├── job.json
│   ├── book-package.json
│   ├── page-ledger.json
│   └── narrator-script.json
├── config/                   ← Configuration templates
│   ├── coordinator.yaml
│   ├── worker-prep.yaml
│   ├── worker-tts.yaml
│   └── supervisor.yaml
├── coordinator/              ← Coordinator service (FastAPI)
├── worker/
│   ├── prep/                 ← Prep worker (PDF/EPUB ingestion)
│   └── tts/                  ← TTS worker (audio generation)
└── supervisor/               ← Node supervisor (JIT container management)
```

## Quick Start (Developer)

### Prerequisites

- Python 3.11+
- Redis 7+
- Docker (for workers)
- S3-compatible storage (MinIO for local dev)

### Local Development

```bash
# Start Redis
docker run -d -p 6379:6379 redis:7

# Start MinIO (S3-compatible storage)
docker run -d -p 9000:9000 -p 9001:9001 \
  -e MINIO_ROOT_USER=admin \
  -e MINIO_ROOT_PASSWORD=password \
  minio/minio server /data

# Start coordinator
cd coordinator
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000

# Start a prep worker
cd worker/prep
pip install -r requirements.txt
python worker.py --config ../../config/worker-prep.yaml
```

## Key Concepts

### Book Package

The output of the prep stage. A voice-agnostic bundle containing:

1. **EPUB** — Clean, Kindle-ready ebook
2. **Narrator Script** (JSON) — Structured text with chapter/segment breakdown
3. **Page Ledger** (JSON) — Page-by-page accounting of the source document

### Page Parity

The guarantee that every page in the source PDF appears in the page ledger with an action (`kept` or `skipped`) and a reason. This is validated before a job can be marked complete.

### Voice Recipe

A self-contained bundle (Docker image + model + parameters) that defines how to produce audio for a specific voice. Recipes are versioned, checksummed, and deployable to any worker meeting the minimum hardware requirements.

### Worker Capabilities

Workers register with the coordinator declaring what they can do:

```yaml
capabilities:
  cpu: 8
  ram_gb: 15
  gpu:
    available: true
    vram_gb: 10
    model: "RTX 3080"
  tools:
    pdf_to_epub: true
    tts: true
  models:
    - "chatterbox"
    - "higgs-v3"
queues:
  - "prep"
  - "convert"
```

## Status

**Phase: Design / MVP Planning**

- [x] Architecture design
- [x] Data model definitions
- [x] API specifications
- [ ] Coordinator implementation
- [ ] Prep worker implementation
- [ ] TTS worker implementation
- [ ] Supervisor implementation
- [ ] End-to-end testing

## Contributing

See `docs/architecture/overview.md` for the full system design before making changes. All architectural decisions are recorded in `docs/decisions/`.
