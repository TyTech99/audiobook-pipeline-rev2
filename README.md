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
│   │   ├── supervisor.md     ← Node supervisor (JIT container management)
│   │   └── commissioning.md  ← Install, accept, and remove coordinator and nodes
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
│       ├── 004-voice-agnostic-prep.md
│       └── 005-node-commissioning.md
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
├── cli/                      ← audiobookctl install, apply, update, uninstall
├── deploy/                   ← Compose file, probe image, example state files
├── worker/
│   ├── prep/                 ← Prep worker (PDF/EPUB ingestion)
│   └── tts/                  ← TTS worker (audio generation)
└── supervisor/               ← Node supervisor (JIT container management)
```

## Setup

Run `audiobookctl` from a checkout of this repo. Linux is Debian or Ubuntu, as root. Windows can install a node only. Python 3 is already installed. The CLI installs Docker. It does not install an NVIDIA driver. Prep and TTS do not process jobs yet. A node install starts the supervisor.

Copy a file from `deploy/examples/` and pass it as `--state`. The operator key and Redis password land in `/etc/audiobook/coordinator.env`. The CLI reads `redis_bind` from the state file and binds the copied compose file to that address. Port, TLS, and accept rules are in `docs/architecture/commissioning.md`.

### Single Linux machine

One host runs the coordinator and node `local` on `127.0.0.1`. Remove `convert` from `queues` in the state file when `nvidia-smi` fails.

```bash
sudo PYTHONPATH=. python3 -m cli standalone install --state deploy/examples/standalone.example.yaml
```

### Coordinator, Linux

Set `api_url`, `redis_bind`, and `redis_advertise` in the state file to this host's private IP when other machines must connect. The CLI binds the copied compose file to `redis_bind`.

```bash
sudo PYTHONPATH=. python3 -m cli coordinator install --state deploy/examples/coordinator.example.yaml
```

### Prep node

Invite on the coordinator. `--json` prints `node_api_key`. Pass `--redis-password` to include it in the JSON. Put that key, `queues: [prep]`, and the Redis password in a copy of `deploy/examples/node.example.yaml`.

```bash
sudo PYTHONPATH=. python3 -m cli node invite \
  --coordinator-url http://10.0.0.5:8000/api/v1 \
  --operator-api-key "$API_KEY_OPERATOR" \
  --node-id vps-prep-1 --queues prep \
  --redis-host 10.0.0.5 --redis-password "$REDIS_PASSWORD" --json
```

Linux:

```bash
sudo PYTHONPATH=. python3 -m cli node install --state deploy/examples/node.example.yaml
```

Windows, from an Administrator prompt. Build the image first. The scheduled task does not. Exit code 4 means reboot and run the install again.

```powershell
docker build -t audiobook-supervisor:local -f deploy/supervisor.Dockerfile .
$env:PYTHONPATH = "."
python -m cli node install --state C:\ProgramData\audiobook\node.yaml
```

### Convert node

Same commands as a prep node. `nvidia-smi` must already succeed. Use `--queues convert`. Linux also installs `nvidia-container-toolkit`. On Windows, after install, set `gpu` in `C:\ProgramData\audiobook\hardware.json`. The installer writes `gpu` as null.

## Tests

```bash
PYTHONPATH=. python3 -m pytest
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

**Phase: Commissioning slice**

- [x] Architecture design
- [x] Data model definitions
- [x] API specifications
- [x] Coordinator commissioning API
- [x] Supervisor registration and probe loop
- [x] `audiobookctl` install, apply, update, uninstall
- [ ] Prep worker implementation
- [ ] TTS worker implementation
- [ ] JIT start and stop of real prep and TTS images
- [ ] End-to-end job testing

## Contributing

See `docs/architecture/overview.md` for the full system design before making changes. All architectural decisions are recorded in `docs/decisions/`.
