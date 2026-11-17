# ADR-001: Use Redis for Job Queues

## Status

Accepted

## Context

The pipeline needs a job queue system to distribute work between the coordinator and multiple workers. Requirements:

- Multiple workers (CPU prep, GPU TTS) competing for jobs
- Atomic job claiming (no duplicate processing)
- Job visibility timeout (crashed workers release jobs)
- Dead letter queue for failed jobs
- Low latency job dispatch
- Horizontal scaling (10+ workers expected)

## Decision

Use **Redis** as the job queue with the following patterns:

- **`BRPOP`** for blocking job claims (no polling)
- **Separate lists** for `prep`, `convert`, and `failed` queues
- **Visibility timeout** via `processing:{worker_id}` lists
- **Dead letter queue** after 3 failed attempts

## Alternatives Considered

### SQLite + HTTP Polling

- Simpler, no extra infrastructure
- But: race conditions on job claiming, polling overhead, doesn't scale past a few workers
- Rejected because we expect 10+ workers and high volume

### Celery + RabbitMQ

- Feature-rich, battle-tested
- But: overkill for our use case, complex configuration, RabbitMQ is heavy to run
- Rejected for simplicity

### AWS SQS / Managed Queues

- Zero infrastructure
- But: vendor lock-in, cost, less control over job format
- Rejected for flexibility and cost

## Consequences

### Positive

- Atomic job claiming with no race conditions
- Near-real-time dispatch (BRPOP is push-like)
- Scales to hundreds of workers
- Simple to operate (single Redis instance)
- Built-in data structures for all queue patterns

### Negative

- Need to run and maintain Redis
- Redis is in-memory (need AOF persistence for durability)
- Single point of failure (mitigated by Redis Sentinel or Cluster in production)

## Implementation Notes

- Redis 7+ with AOF persistence enabled
- Password authentication required
- Monitor queue depth and memory usage
- Plan for Redis Sentinel or Cluster when scaling beyond 1 node
