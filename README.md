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

### Prerequisites

Every host you install needs:

- **OS**: Debian or Ubuntu for the coordinator and Linux nodes. Windows can run a node only.
- **User**: root on Linux, an Administrator on Windows.
- **Python 3**: already installed. The CLI uses it and does not install it.
- **Docker**: the CLI installs it when it is missing.
- **NVIDIA driver**: install it yourself on any host that should run convert jobs. The CLI checks `nvidia-smi` and exits when it fails. It does not install the driver.
- **Network**: the coordinator and nodes must reach each other. Redis is not reachable off the coordinator host unless you change `redis_bind`.

### How the CLI works

You run the same CLI, `audiobookctl`, from a checkout of this repo on every host. You invoke it as `python3 -m cli` on Linux and `python -m cli` on Windows. The subcommand you choose selects the workload:

| Subcommand | What it installs | Where you run it |
| --- | --- | --- |
| `standalone install` | The coordinator and one local node on the same host | A single Linux machine |
| `coordinator install` | The coordinator: Redis, PostgreSQL, and the API | The coordinator host, Linux |
| `node invite` | Creates a node record on the coordinator and prints a one-time key | Any machine that can reach the coordinator API |
| `node install` | The supervisor container that manages worker containers | A node host, Linux or Windows |

Each install reads a state file from `deploy/examples/`. Copy the example that matches your workload, edit the addresses and queues, and pass the file as `--state`. The CLI generates the operator key and Redis password when you leave them empty, and writes them to `/etc/audiobook/coordinator.env`. The CLI reads `redis_bind` from the state file and binds the copied compose file to that address.

For the full install procedures and what each state file field means, see [`deploy/README.md`](deploy/README.md). The example files carry comments above each field.

Prep and TTS workers do not process jobs yet. A node install starts the supervisor, which is the process that will start worker containers when they are ready.

### Single Linux machine

Use this when one Debian or Ubuntu host runs the coordinator and the only node. Nothing else on the network needs to reach Redis or the API.

1. Copy the example state file.

```bash
sudo cp deploy/examples/standalone.example.yaml /etc/audiobook/standalone.yaml
```

2. If this machine should take convert jobs, check the GPU.

```bash
nvidia-smi
```

If that command fails, edit `queues` in the state file so the only entry is `prep`.

3. Install.

```bash
sudo PYTHONPATH=. python3 -m cli standalone install --state /etc/audiobook/standalone.yaml
```

A finished run prints `coordinator installed` and `node installed`. If the coordinator container is still starting, the invite can fail. Run the same command again. The second run prints `coordinator unchanged` and retries the node.

4. Read the operator key. It is not printed by the installer.

```bash
sudo awk -F= '/^API_KEY_OPERATOR=/ {print $2}' /etc/audiobook/coordinator.env
```

5. Confirm the containers. The supervisor service builds its image on first start, so that start is the slow one.

```bash
sudo docker compose -f /opt/audiobook/coordinator/docker-compose.dev.yml ps
sudo systemctl status audiobook-supervisor.service --no-pager
sudo docker ps --filter name=audiobook-supervisor
```

### Coordinator, Linux

Use this when other machines will register as nodes.

1. Copy the example state file.

```bash
sudo cp deploy/examples/coordinator.example.yaml /etc/audiobook/coordinator.yaml
```

2. Set `api_url`, `redis_bind`, and `redis_advertise` to this host's private IP. The CLI binds the copied compose file to `redis_bind`.

3. Install.

```bash
sudo PYTHONPATH=. python3 -m cli coordinator install --state /etc/audiobook/coordinator.yaml
```

4. Read both secrets from `/etc/audiobook/coordinator.env`. Nodes need `API_KEY_OPERATOR` and `REDIS_PASSWORD`.

```bash
sudo awk -F= '/^(API_KEY_OPERATOR|REDIS_PASSWORD)=/ {print}' /etc/audiobook/coordinator.env
```

### Prep node

Use this when a host should join an existing coordinator and take prep jobs. No GPU is required.

1. On the coordinator, read the operator key and Redis password.

```bash
sudo awk -F= '/^(API_KEY_OPERATOR|REDIS_PASSWORD)=/ {print}' /etc/audiobook/coordinator.env
```

2. Invite the node. Run this from any machine that can reach the coordinator API. `--json` prints the one-time node key.

```bash
sudo PYTHONPATH=. python3 -m cli node invite \
  --coordinator-url http://10.0.0.5:8000/api/v1 \
  --operator-api-key "$API_KEY_OPERATOR" \
  --node-id vps-prep-1 --queues prep \
  --redis-host 10.0.0.5 --redis-password "$REDIS_PASSWORD" --json
```

3. On the node, copy the example state file.

```bash
sudo cp deploy/examples/node.example.yaml /etc/audiobook/node.yaml
```

4. Set these fields in the state file:

   - `node_id` to the id you invited
   - `queues` to `prep` only
   - `coordinator_url` to the same URL you passed to invite
   - `coordinator_id` and `node_api_key` from the invite JSON
   - `operator_api_key` to `API_KEY_OPERATOR`, so a later uninstall can drain the node
   - `redis.host` to the coordinator's private IP
   - `redis.password` to `REDIS_PASSWORD`

5. Install as root.

```bash
sudo PYTHONPATH=. python3 -m cli node install --state /etc/audiobook/node.yaml
```

This installs `docker-ce` if it is missing, writes `/etc/audiobook/node.env`, and installs `audiobook-supervisor.service`. The service writes `/etc/audiobook/hardware.json` from `nvidia-smi` when the driver is present, builds the supervisor image, and runs that container with `/var/run/docker.sock` mounted.

6. Confirm the supervisor is running.

```bash
sudo systemctl status audiobook-supervisor.service --no-pager
sudo docker ps --filter name=audiobook-supervisor
```

### Convert node

Do everything in the prep section, with these differences.

- Install the NVIDIA driver before the CLI. `nvidia-smi` must print the GPU.
- Invite with `--queues convert`, or `--queues prep,convert` when the same machine does both. Set `queues` in the state file to match.
- On Linux, the package list adds `nvidia-container-toolkit`.
- On Windows, after install, set `gpu` in `C:\ProgramData\audiobook\hardware.json`. The installer writes `gpu` as null.

### Windows node

Windows can run a node only. Open PowerShell as Administrator and run the CLI from the checkout.

1. Build the supervisor image. The scheduled task does not build it.

```powershell
docker build -t audiobook-supervisor:local -f deploy/supervisor.Dockerfile .
```

2. Copy the example state file to `C:\ProgramData\audiobook\node.yaml` and set the same fields as on Linux.

3. Install.

```powershell
$env:PYTHONPATH = "."
python -m cli node install --state C:\ProgramData\audiobook\node.yaml
```

Exit code 4 means Docker Desktop was installed and Windows wants a reboot. Reboot, then run the same install again.

The task name is `AudiobookSupervisor`. It runs at startup as SYSTEM. Start Docker Desktop before you run the task, or the container exits.

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

**Phase: Core pipeline**

- [x] Architecture design
- [x] Data model definitions
- [x] API specifications
- [x] Coordinator commissioning API
- [x] Supervisor registration and probe loop
- [x] `audiobookctl` install, apply, update, uninstall
- [x] Prep worker implementation
- [x] TTS worker implementation
- [x] JIT start and stop of real prep and TTS images
- [x] End-to-end job testing

## Contributing

See `docs/architecture/overview.md` for the full system design before making changes. All architectural decisions are recorded in `docs/decisions/`.
