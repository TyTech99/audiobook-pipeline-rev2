# Coordinator Service

FastAPI service that manages job submission, routing, and worker registration.

## Implementation Status

**Not yet implemented.** See design docs:

- [Architecture Overview](../docs/architecture/overview.md)
- [Coordinator API](../docs/api/coordinator-api.md)
- [Commissioning](../docs/architecture/commissioning.md)
- [Job Schema](../docs/data-models/job-schema.md)

## Planned Structure

```
coordinator/
├── main.py              ← FastAPI app entry point
├── config.py            ← Configuration loading
├── models/              ← Pydantic models
│   ├── job.py
│   ├── worker.py
│   └── response.py
├── routers/             ← API routers
│   ├── jobs.py
│   ├── workers.py
│   └── health.py
├── services/            ← Business logic
│   ├── job_service.py
│   ├── worker_service.py
│   └── queue_service.py
├── db/                  ← Database layer
│   ├── postgres.py
│   └── redis.py
└── requirements.txt
```
