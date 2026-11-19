# Audiobook Pipeline Rev2

Distributed PDF/EPUB to audiobook conversion pipeline. Two job types, two stages, local file storage.

## What it is

The pipeline converts a PDF or EPUB into an audiobook in two separate jobs:

1. **Prep** (CPU): source document → book package (clean EPUB, narrator script, page ledger, cover, metadata, LLM analysis). Status ends at `prep_complete`.
2. **Convert** (GPU): book package → audiobook (M4B, MP3 chapters, credits, sample). Status ends at `complete`.

You submit each stage as its own job. The convert job takes the `package_url` returned by the prep job.

## Architecture

```
┌─────────────────┐         ┌──────────────────┐
│   Coordinator   │         │     Workers      │
│  (FastAPI)      │◄───────►│  (Docker)        │
│                 │  Redis  │                  │
│  - Job API      │  Queue  │  - Prep (CPU)    │
│  - Worker reg   │         │  - TTS (GPU)     │
│  - Node mgmt    │         │                  │
└─────────────────┘         └──────────────────┘
         │                           │
         ▼                           ▼
┌─────────────────┐         ┌──────────────────┐
│ Local file      │         │   Supervisor     │
│ storage         │         │  (per node)      │
│                 │         │  - Hardware det  │
│ /var/lib/audiobook│       │  - Container mgmt│
│  packages/{id}/ │         │  - Working hours │
│  audio/{id}/    │         │                  │
└─────────────────┘         └──────────────────┘
```

Components: coordinator (FastAPI), Redis (queue), Postgres (state), supervisor (per node, JIT container management), prep worker, TTS worker, voice recipe container.

## Setup

### Prerequisites

- Debian or Ubuntu (coordinator and Linux nodes). Windows can run a node only.
- Root on Linux, Administrator on Windows.
- Python 3 already installed.
- Docker (CLI installs it when missing).
- NVIDIA driver on any host that runs convert jobs. The CLI checks `nvidia-smi` and does not install the driver.

### Standalone install (primary path)

One Debian or Ubuntu host runs the coordinator and the only node.

```bash
sudo cp deploy/examples/standalone.example.yaml /etc/audiobook/standalone.yaml
```

Edit `queues` in the state file. Use `[prep]` when the machine has no GPU. Use `[prep, convert]` when it has a GPU.

If this machine runs convert jobs, verify the GPU first.

```bash
nvidia-smi
```

Install.

```bash
sudo PYTHONPATH=. python3 -m cli standalone install --state /etc/audiobook/standalone.yaml
```

A finished run prints `coordinator installed` and `node installed`. If the coordinator container is still starting, the invite can fail. Run the same command again.

Read the operator key. The installer does not print it.

```bash
sudo awk -F= '/^API_KEY_OPERATOR=/ {print $2}' /etc/audiobook/coordinator.env
```

Confirm the containers.

```bash
sudo docker compose -f /opt/audiobook/coordinator/docker-compose.dev.yml ps
sudo systemctl status audiobook-supervisor.service --no-pager
sudo docker ps --filter name=audiobook-supervisor
```

For multi-host installs (separate coordinator and nodes), see [deploy/README.md](deploy/README.md).

## Config

### Operator key

Lives in `/etc/audiobook/coordinator.env` as `API_KEY_OPERATOR`. Generated at install when the state file leaves `operator_api_key` empty. Use it as `Authorization: Bearer $API_KEY_OPERATOR` on job and node management endpoints.

### LLM

The `llm:` block in `config/supervisor.yaml`, `config/worker-prep.yaml`, and `config/worker-tts.yaml`. The supervisor reads its own copy and injects `LLM_*` env vars into worker and recipe containers.

```yaml
llm:
  base_url: "http://127.0.0.1:8080/v1"
  api_key: ""
  model: "local-model"
  timeout_seconds: 120
  slots: 4
```

The endpoint must speak OpenAI-compatible chat completions. At install, an optional `llm:` block in the state file seeds these YAML files. After install, edit the YAML files directly.

### Storage root

Local file storage under `STORAGE_ROOT`, default `/var/lib/audiobook`.

- Prep packages: `file:///var/lib/audiobook/packages/{job_id}/`
- Audio output: `file:///var/lib/audiobook/audio/{job_id}/`

No S3 required. The `s3:` blocks in config files are optional and unused in local-file mode.

## Usage

Place the source file on the coordinator host (or any host reachable via `file://`).

### 1. Submit a prep job

```bash
export API_KEY_OPERATOR=$(sudo awk -F= '/^API_KEY_OPERATOR=/ {print $2}' /etc/audiobook/coordinator.env)

curl -s -X POST http://127.0.0.1:8000/api/v1/jobs \
  -H "Authorization: Bearer $API_KEY_OPERATOR" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "prep",
    "input_format": "pdf",
    "input_url": "file:///var/lib/audiobook/input/book.pdf",
    "metadata": {"title": "The Castle", "author": "Jane Doe"}
  }'
```

Response (201):

```json
{"job_id": "uuid", "status": "submitted", "created_at": "2026-11-17T10:00:00Z"}
```

### 2. Poll the prep job

```bash
curl -s http://127.0.0.1:8000/api/v1/jobs/{job_id} \
  -H "Authorization: Bearer $API_KEY_OPERATOR"
```

When `status` is `prep_complete`, read `package_url`. It looks like `file:///var/lib/audiobook/packages/{job_id}/`.

### 3. Submit a convert job

Use the `package_url` from the prep job as `input_url`.

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/jobs \
  -H "Authorization: Bearer $API_KEY_OPERATOR" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "convert",
    "input_format": "epub",
    "input_url": "file:///var/lib/audiobook/packages/{prep_job_id}/",
    "metadata": {
      "performance_style": "neutral",
      "voice_recipe": {}
    }
  }'
```

### 4. Poll the convert job

```bash
curl -s http://127.0.0.1:8000/api/v1/jobs/{convert_job_id} \
  -H "Authorization: Bearer $API_KEY_OPERATOR"
```

When `status` is `complete`, read `output_url`. It looks like `file:///var/lib/audiobook/audio/{job_id}/`.

### 5. Download artifacts over the API

List a job's finished artifacts (works for prep packages and convert audio
output alike, nested files such as `m4b/audiobook.m4b` included):

```bash
curl -s http://127.0.0.1:8000/api/v1/jobs/{job_id}/artifacts \
  -H "Authorization: Bearer $API_KEY_OPERATOR"
```

Download one as an attachment (correct media type, `Content-Disposition:
attachment`):

```bash
curl -s -OJ http://127.0.0.1:8000/api/v1/jobs/{job_id}/artifacts/book.epub \
  -H "Authorization: Bearer $API_KEY_OPERATOR"
```

Filenames are restricted to `[A-Za-z0-9._-]` path components — traversal
attempts are rejected with 400 before any filesystem access. A job with no
artifacts yet returns 404 `artifacts_not_found`.

### Python equivalent

```python
import os
import time

import httpx

base = "http://127.0.0.1:8000/api/v1"
headers = {"Authorization": "Bearer " + os.environ["API_KEY_OPERATOR"]}

# Prep
r = httpx.post(f"{base}/jobs", headers=headers, json={
    "type": "prep",
    "input_format": "pdf",
    "input_url": "file:///var/lib/audiobook/input/book.pdf",
    "metadata": {"title": "The Castle", "author": "Jane Doe"},
})
prep_job_id = r.json()["job_id"]

# Poll until prep_complete
while True:
    job = httpx.get(f"{base}/jobs/{prep_job_id}", headers=headers).json()
    if job["status"] in ("prep_complete", "failed"):
        break
    time.sleep(5)
package_url = job["package_url"]

# Convert
r = httpx.post(f"{base}/jobs", headers=headers, json={
    "type": "convert",
    "input_format": "epub",
    "input_url": package_url,
    "metadata": {"performance_style": "neutral"},
})
convert_job_id = r.json()["job_id"]

# Poll until complete
while True:
    job = httpx.get(f"{base}/jobs/{convert_job_id}", headers=headers).json()
    if job["status"] in ("complete", "convert_failed"):
        break
    time.sleep(5)
output_url = job["output_url"]
```

## Tests

```bash
PYTHONPATH=. python3 -m pytest
```

The e2e test (`tests/test_e2e.py`) requires a reachable Postgres (`DATABASE_URL`) and Redis. It boots the coordinator, submits a prep job, runs the prep worker, and asserts `prep_complete` with a valid page ledger.

## Repository layout

```
├── README.md                 ← You are here
├── coordinator/              ← FastAPI service (jobs, workers, nodes)
├── supervisor/               ← Per-node daemon (JIT container management)
├── worker/
│   ├── prep/                 ← Prep worker (PDF/EPUB → book package)
│   ├── tts/                  ← TTS worker (book package → audio)
│   └── common/               ← Shared storage, LLM client, settings
├── recipe/
│   └── michael-chatterbox/   ← Chatterbox michael voice recipe
├── cli/                      ← audiobookctl (install, apply, update, uninstall)
├── config/                   ← YAML config templates
├── deploy/                   ← Dockerfiles, build script, compose, examples
├── docs/
│   ├── api/coordinator-api.md
│   ├── architecture/         ← overview.md, commissioning.md
│   ├── data-models/          ← job-schema.md, book-package.md, etc.
│   └── decisions/            ← ADRs
├── schemas/                  ← JSON schemas
└── tests/                    ← e2e tests
```

## Key concepts

### Book package

Output of the prep stage. A directory containing:

- `book.epub`: clean, Kindle-ready ebook
- `script.json`: narrator script with chapter/segment breakdown
- `page-ledger.json`: page-by-page accounting of the source document
- `metadata.json`: extracted and enriched metadata
- `llm-analysis.json`: subject summary, tone analysis, style recommendations
- `manifest.json`: package manifest with validation status
- `cover.jpg`: cover art

### Page parity

Every page in the source PDF appears in the page ledger with an action (`kept` or `skipped`) and a reason. Validated before the prep job completes.

### Voice recipe

A Docker image that defines how to produce audio for a specific voice. The TTS worker runs the recipe container with the book package mounted at `/input` and writes output to `/output`. The current recipe is `recipe/michael-chatterbox/`, built at node install.

## Status

- [x] Coordinator (FastAPI, jobs, workers, nodes)
- [x] Supervisor (JIT container management)
- [x] Prep worker (local-file mode)
- [x] TTS worker (local-file mode, Chatterbox michael recipe)
- [x] Worker Docker images built at node install
- [x] End-to-end job testing

External dependencies for production use:

- GPU with NVIDIA driver and Container Toolkit for convert jobs
- Model download for the Chatterbox michael recipe on first run

## See also

- [deploy/README.md](deploy/README.md): multi-host install
- [docs/api/coordinator-api.md](docs/api/coordinator-api.md): full API reference
- [docs/data-models/job-schema.md](docs/data-models/job-schema.md): job payload and state machine
- [docs/architecture/overview.md](docs/architecture/overview.md): system design
- [docs/architecture/commissioning.md](docs/architecture/commissioning.md): node lifecycle
