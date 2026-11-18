# Node Supervisor

Daemon that runs on each worker node. Detects hardware, registers with the coordinator, and JIT-starts/stops worker containers.

## Purpose

The supervisor is the node agent. It keeps the node registered with the coordinator, responds to heartbeat commands (run, drain, start_probe), and launches worker containers when there is work on the queues. It injects `LLM_*`, `STORAGE_ROOT`, and `DEFAULT_RECIPE_IMAGE` into worker environments and mounts `/var/lib/audiobook` and `/var/run/docker.sock` (TTS only).

## Implementation status

Implemented. Runs as a Docker container managed by `audiobook-supervisor.service` on Linux nodes. The image is built at node install from `deploy/supervisor.Dockerfile`.

## How it runs

The supervisor reads `config/supervisor.yaml` (path overridable via `SUPERVISOR_CONFIG` env). The CLI copies this file to the node prefix at install.

```bash
# Inside the container
python -m supervisor.supervisor
```

Key config values:

- `node.id`: node identifier (from `NODE_ID` env)
- `coordinator.url`: coordinator API base URL
- `coordinator.api_key`: node API key (from `API_KEY` env)
- `redis.*`: Redis connection for queue depth checks
- `llm.*`: OpenAI-compatible LLM settings injected into worker containers
- `docker.images.*`: image tags for prep, tts, recipe, and probe containers
- `working_hours.*`: when the node is allowed to run jobs
- `idle.timeout_minutes`: how long a worker container stays up after its last job

## File layout

```
supervisor/
├── supervisor.py        ← Entry point and signal handling
├── config.py            ← Configuration loading with env expansion
├── coordinator_client.py← HTTP client for the coordinator API
├── docker_manager.py    ← Docker container lifecycle (start, stop, probe)
├── hardware.py          ← Hardware detection (CPU, RAM, GPU)
├── loop.py              ← Main loop (heartbeat, command handling, JIT)
└── probe_client.py      ← Probe container management
```

## Dependencies

- Python 3.11+
- Docker SDK for Python
- redis-py
- PyYAML
- psutil (hardware detection)
- nvidia-ml-py (GPU detection, optional)

## Resource usage

- RAM: ~50MB
- CPU: <1% (idle)
- Runs 24/7 on each node

## See also

- [ADR-003: JIT Workers](../docs/decisions/003-jit-workers.md)
- [ADR-005: Node Commissioning](../docs/decisions/005-node-commissioning.md)
- [Commissioning](../docs/architecture/commissioning.md)
