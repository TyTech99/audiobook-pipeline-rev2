# Coordinator Service

FastAPI service that manages job submission, routing, worker registration, and node commissioning.

## Purpose

The coordinator is the control plane. It accepts job submissions, pushes work to Redis queues, tracks job state in Postgres, and manages the node and worker lifecycle.

## Implementation status

Implemented. Runs as a Docker container via `deploy/coordinator.Dockerfile` and `deploy/docker-compose.dev.yml`.

## How it runs

The coordinator starts with a config file (default `config/coordinator.yaml`). The CLI copies this file to the coordinator prefix at install and generates the env file it expands.

```bash
# Inside the container
python -m coordinator.main
```

Key config values:

- `coordinator_id`: stable id nodes pin before sending a key
- `auth.operator_api_keys`: list of operator API keys (from `API_KEY_OPERATOR` env)
- `redis.*`: Redis connection for job queues
- `postgres.*`: Postgres connection for job and node state
- `s3.endpoint`: optional, unused in local-file mode

## File layout

```
coordinator/
├── main.py              ← FastAPI app entry point
├── config.py            ← Configuration loading with env expansion
├── auth.py              ← API key auth (operator and node classes)
├── errors.py            ← ApiError and exception handlers
├── clock.py             ← UTC time helpers
├── logging_setup.py     ← JSON logging
├── db/
│   ├── postgres.py      ← Connection pool and schema application
│   └── schema.sql       ← Database schema
├── routers/
│   ├── health.py        ← GET /health
│   ├── jobs.py          ← Job endpoints
│   ├── workers.py       ← Worker endpoints
│   ├── nodes.py         ← Node endpoints
│   └── schemas.py       ← Pydantic request models
└── services/
    ├── jobs.py          ← Job state machine and queue logic
    ├── workers.py       ← Worker registration and heartbeats
    ├── nodes.py         ← Node lifecycle
    └── rules.py         ← Key hashing and validation rules
```

## See also

- [Coordinator API](../docs/api/coordinator-api.md)
- [Job Schema](../docs/data-models/job-schema.md)
- [Commissioning](../docs/architecture/commissioning.md)
