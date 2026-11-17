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

Run the CLI from a checkout of this repo. The program name is `audiobookctl`. The command is `python3 -m cli` on Linux and `python -m cli` on Windows. It does not open SSH. Run it on the machine you are installing.

Four workloads:

| Workload | Machines | OS |
| --- | --- | --- |
| Single machine | Coordinator and one node together | Linux only |
| Coordinator | API, Redis, and PostgreSQL | Linux only |
| Prep node | CPU supervisor | Linux or Windows |
| Convert node | GPU supervisor | Linux or Windows |

Prep and TTS do not process jobs in this slice. A node install starts the supervisor. It does not start a prep or TTS worker.

### Rules for every install

- Install Python 3 yourself. The CLI needs it. The installer does not install Python.
- Linux installs require Debian or Ubuntu, as root. Any other distro exits 2.
- Windows can install a node. A coordinator install on Windows exits 2.
- The CLI installs Docker when it is missing. The package is `docker-ce` on Linux and `Docker.DockerDesktop` on Windows.
- The CLI does not install an NVIDIA driver. A convert queue exits 2 when `nvidia-smi` fails, and prints `NVIDIA driver is missing. The installer does not install drivers.`
- On a convert node where `nvidia-smi` works, Linux also installs `nvidia-container-toolkit`.
- `http://` is allowed only for localhost and private addresses. A public hostname has to be `https://`.
- The installer does not install a TLS certificate client. Put your own proxy in front of port 8000 if the API has to be public.
- `deploy/docker-compose.dev.yml` publishes Redis, PostgreSQL, and the coordinator on `127.0.0.1` only. Other machines cannot reach those ports until you edit that file. Details are in the coordinator section.
- Exit code 0 means the install finished. Exit code 4 means Windows installed Docker Desktop and wants a reboot. Run the same command again after the reboot.

`node update` and `coordinator update` restart the service and leave the env file in place. They do not copy a new checkout into `/opt/audiobook`. On Linux the supervisor service rebuilds its image from the files already under `/opt/audiobook/supervisor` every time it starts.

Address and auth rules are in `docs/architecture/commissioning.md`.

### Single Linux machine

Use this when one Debian or Ubuntu host runs the coordinator and the only node. Nothing else on the network needs to reach Redis or the API.

You get three containers from `deploy/docker-compose.dev.yml`, bound to `127.0.0.1`.

- Redis on port 6379
- PostgreSQL on port 5432
- The coordinator on port 8000

You also get a supervisor container for node id `local`. Secrets are generated into `/etc/audiobook/coordinator.env` when the state file leaves them empty. The node env is `/etc/audiobook/node.env`.

1. If this machine should take convert jobs, install the NVIDIA driver yourself and check it.

```bash
nvidia-smi
```

If that command fails, this machine can still be a prep node. You will drop `convert` in the next step.

2. Copy the example state file out of the repo. Keep the addresses on this machine.

```bash
sudo mkdir -p /etc/audiobook
sudo cp deploy/examples/standalone.example.yaml /etc/audiobook/standalone.yaml
sudo chmod 600 /etc/audiobook/standalone.yaml
```

Leave `api_url` at `http://127.0.0.1:8000/api/v1`. Leave `redis_bind` and `redis_advertise` at `127.0.0.1`. If `nvidia-smi` failed, edit `queues` so the only entry is `prep`.

3. Install from the checkout, as root.

```bash
sudo PYTHONPATH=. python3 -m cli standalone install --state /etc/audiobook/standalone.yaml
```

A finished run prints `coordinator installed` and `node installed`. If the coordinator container is still starting, the invite can fail. Run the same command again. The second run prints `coordinator unchanged` and retries the node.

4. Read the operator key. It is not printed by the installer.

```bash
sudo awk -F= '/^API_KEY_OPERATOR=/ {print $2}' /etc/audiobook/coordinator.env
```

5. Check health, an unauthenticated 401, and the node list.

```bash
export API_KEY_OPERATOR='paste-the-key'
sh deploy/accept-dev.sh
```

6. Confirm the containers. The supervisor service builds `audiobook-supervisor:local` on first start, so that start is the slow one.

```bash
sudo docker compose -f /opt/audiobook/coordinator/docker-compose.dev.yml ps
sudo systemctl status audiobook-supervisor.service --no-pager
sudo docker ps --filter name=audiobook-supervisor
```

To remove this host, stop the node first, then the coordinator. `--purge` also deletes that role's env file.

```bash
sudo PYTHONPATH=. python3 -m cli node uninstall --state /etc/audiobook/standalone.yaml --local-only --purge
sudo PYTHONPATH=. python3 -m cli coordinator uninstall --purge
```

### Coordinator, Linux

Use this when other machines will register as nodes. Windows cannot run this workload.

The compose file in the repo binds every published port to `127.0.0.1`. Edit it before install if nodes on other hosts must connect. Change each `127.0.0.1` port mapping to this host's private IP. Use that same IP in `api_url`, `redis_bind`, and `redis_advertise`. The installer copies the file to `/opt/audiobook/coordinator/docker-compose.dev.yml` at install time. Editing the repo copy later does not change a host that is already installed. Edit the copy under `/opt/audiobook/coordinator` and restart `audiobook-coordinator.service`.

1. Copy the example and point it at the private IP.

```bash
sudo mkdir -p /etc/audiobook
sudo cp deploy/examples/coordinator.example.yaml /etc/audiobook/coordinator.yaml
sudo chmod 600 /etc/audiobook/coordinator.yaml
```

Set `api_url` to `http://10.0.0.5:8000/api/v1`, using your private IP instead of `10.0.0.5`. Set `redis_bind` and `redis_advertise` to that same IP. Set `tls.mode` to `off` on a private network. The installer does not read the cert file paths in the example.

2. Install.

```bash
sudo PYTHONPATH=. python3 -m cli coordinator install --state /etc/audiobook/coordinator.yaml
```

3. Read both secrets from `/etc/audiobook/coordinator.env`. Nodes need `API_KEY_OPERATOR` and `REDIS_PASSWORD`.

```bash
sudo awk -F= '/^(API_KEY_OPERATOR|REDIS_PASSWORD)=/ {print}' /etc/audiobook/coordinator.env
```

4. Check the API on the address you published. `deploy/accept-dev.sh` defaults to `http://127.0.0.1:8000`, which fails when the ports are on a private IP.

```bash
export API_KEY_OPERATOR='paste-the-key'
export COORDINATOR_URL='http://10.0.0.5:8000'
sh deploy/accept-dev.sh
```

The unit is `audiobook-coordinator.service`. `ExecStart` runs `docker compose up -d`. `ExecStop` runs `docker compose down`.

```bash
sudo PYTHONPATH=. python3 -m cli coordinator uninstall --purge
```

### Prep node

A prep node joins a coordinator that is already up. The queue name is `prep`. No GPU is required.

Create the node on the coordinator, from any checkout that can reach the API. `--json` is required if you want the new key. Without it, the command does not print the key.

```bash
sudo PYTHONPATH=. python3 -m cli node invite \
  --coordinator-url http://10.0.0.5:8000/api/v1 \
  --operator-api-key "$API_KEY_OPERATOR" \
  --node-id vps-prep-1 \
  --queues prep \
  --redis-host 10.0.0.5 \
  --json
```

Copy `node_api_key` and `coordinator_id` from that JSON. Copy `REDIS_PASSWORD` from the coordinator's `/etc/audiobook/coordinator.env`. The invite response does not include the Redis password.

The probe image is not built by the installer. Build it on the node before you expect a probe to succeed.

```bash
docker build -t audiobook-probe:dev -f deploy/probe.Dockerfile .
```

#### Linux

```bash
sudo mkdir -p /etc/audiobook
sudo cp deploy/examples/node.example.yaml /etc/audiobook/node.yaml
sudo chmod 600 /etc/audiobook/node.yaml
```

Set these fields in that file.

- `node_id` to the id you invited
- `queues` to `prep` only
- `coordinator_url` to the same URL you passed to invite
- `coordinator_id` and `node_api_key` from the JSON
- `operator_api_key` to `API_KEY_OPERATOR`, so a later uninstall can drain the node
- `redis.host` to the coordinator's private IP
- `redis.password` to `REDIS_PASSWORD`
- `probe_image` to `audiobook-probe:dev`

Install as root from the checkout.

```bash
sudo PYTHONPATH=. python3 -m cli node install --state /etc/audiobook/node.yaml
```

This installs `docker-ce` if it is missing, writes `/etc/audiobook/node.env`, and installs `audiobook-supervisor.service`. The service writes `/etc/audiobook/hardware.json` from `nvidia-smi` when the driver is present, builds `audiobook-supervisor:local`, and runs that container with `/var/run/docker.sock` mounted. The container does not get a GPU device.

```bash
sudo systemctl status audiobook-supervisor.service --no-pager
sudo docker ps --filter name=audiobook-supervisor
```

Remove it with the coordinator still reachable, or pass `--local-only` to leave the coordinator's node row in place.

```bash
sudo PYTHONPATH=. python3 -m cli node uninstall --state /etc/audiobook/node.yaml --purge
```

#### Windows

Install Python 3 yourself and open PowerShell as Administrator. From the checkout, build the supervisor image. The scheduled task does not build it.

```powershell
docker build -t audiobook-supervisor:local -f deploy/supervisor.Dockerfile .
```

Copy `deploy/examples/node.example.yaml` to `C:\ProgramData\audiobook\node.yaml` and set the same fields as on Linux. `queues` stays `prep` only.

```powershell
$env:PYTHONPATH = "."
python -m cli node install --state C:\ProgramData\audiobook\node.yaml
```

Exit code 4 means Docker Desktop was installed and Windows wants a reboot. Reboot, then run the same install again.

The task name is `AudiobookSupervisor`. It runs at startup as SYSTEM. Start Docker Desktop before you run the task, or the container exits.

```powershell
schtasks /Run /TN AudiobookSupervisor
```

The installer writes `C:\ProgramData\audiobook\hardware.json` with `gpu` set to null. That is fine for a prep node. The env file is `C:\ProgramData\audiobook\node.env`.

```powershell
python -m cli node uninstall --state C:\ProgramData\audiobook\node.yaml --purge
```

### Convert node

Do everything in the prep section, with these differences.

Install the NVIDIA driver before the CLI. `nvidia-smi` must print the GPU. The installer will not install the driver.

Invite with `--queues convert`, or `--queues prep,convert` when the same machine does both. In the node state file, set `queues` to match.

On Linux, the package list adds `nvidia-container-toolkit`. The supervisor container still does not mount a GPU device. Worker containers are the ones that receive GPUs, and those worker images are not in this slice.

On Windows, build `audiobook-supervisor:local` the same way as a prep node. After install, replace `C:\ProgramData\audiobook\hardware.json`. The installer writes `gpu` as null, so the node would register with no GPU. Use the shape below, then run the task again.

```json
{"cpu": 8, "ram_gb": 32.0, "gpu": {"model": "RTX 3080", "vram_gb": 10.0}}
```

```powershell
schtasks /End /TN AudiobookSupervisor
schtasks /Run /TN AudiobookSupervisor
```

## Tests

From the checkout:

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
