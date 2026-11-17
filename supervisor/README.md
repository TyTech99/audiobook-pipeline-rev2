# Node Supervisor

Lightweight daemon that runs on each worker node. Detects hardware, registers with the coordinator, and JIT-starts/stops worker containers.

## Implementation Status

**Not yet implemented.** See design docs:

- [Architecture Overview](../docs/architecture/overview.md)
- [ADR-003: JIT Workers](../docs/decisions/003-jit-workers.md)
- [ADR-005: Node Commissioning](../docs/decisions/005-node-commissioning.md)
- [Commissioning](../docs/architecture/commissioning.md)

## Planned Structure

```
supervisor/
├── supervisor.py        ← Main supervisor loop
├── config.py            ← Configuration loading
├── hardware.py          ← Hardware detection (CPU, RAM, GPU)
├── docker_manager.py    ← Docker container lifecycle
├── registration.py      ← Coordinator registration
├── scheduler.py         ← Working hours and idle timeout logic
└── requirements.txt
```

## Dependencies

- Python 3.11+
- Docker SDK for Python
- redis-py
- PyYAML
- psutil (hardware detection)
- nvidia-ml-py (GPU detection, optional)

## Resource Usage

- RAM: ~50MB
- CPU: <1% (idle)
- Runs 24/7 on each node
