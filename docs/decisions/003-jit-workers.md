# ADR-003: JIT Worker Containers

## Status

Accepted

## Context

Workers need to be **decommissionable**. A node might:

- Run GPU inference during the day, audiobook TTS at night
- Be a shared resource with other workloads
- Be spun up/down based on cost optimization
- Need to release GPU memory completely when idle

Running workers as always-on processes would:

- Consume GPU memory 24/7 (blocking other work)
- Use RAM for loaded models when idle
- Make it hard to share hardware between workloads

## Decision

Workers run as **Docker containers** that are started and stopped by a **supervisor** process based on:

1. **Queue depth**: Start container when jobs are waiting
2. **Idle timeout**: Stop container after N minutes with no jobs
3. **Working hours**: Only start containers during configured hours
4. **Manual control**: Can be started/stopped on demand

### Architecture

```
Node (VPS/GPU machine)
├── Supervisor (always running, ~50MB RAM)
│   ├── Detects hardware capabilities
│   ├── Registers with coordinator
│   ├── Monitors queue depth
│   ├── Starts/stops Docker containers
│   └── Enforces working hours
└── Docker Engine
    └── Worker containers (JIT)
        ├── Prep worker (CPU, lightweight)
        └── TTS worker (GPU, heavy)
```

### Container Lifecycle

```
1. Supervisor detects jobs in queue
2. Supervisor starts Docker container
   - Mounts GPU if available (--gpus all)
   - Mounts models and files
   - Passes Redis connection info
3. Container starts, loads models (~30s)
4. Worker claims jobs from Redis, processes them
5. Queue empty for N minutes (default 5)
6. Supervisor sends SIGTERM to container
7. Container finishes current job, stops
8. GPU memory freed, RAM freed
9. Only supervisor remains (~50MB)
```

## Alternatives Considered

### Always-On Workers

- Simplest, no startup latency
- But: consumes resources 24/7, blocks GPU for other work
- Rejected because we need decommissionable workers

### Cloud Functions (Lambda, etc.)

- True JIT, pay-per-use
- But: execution time limits (15 min), cold starts, GPU support limited
- Rejected because audiobook jobs take hours

### Kubernetes

- Advanced scheduling, auto-scaling
- But: heavy infrastructure, overkill for our scale
- Rejected for simplicity

## Consequences

### Positive

- Zero resource usage when idle, other than the supervisor container
- Full GPU availability for other work when not processing
- Easy to add/remove nodes
- Clean isolation between jobs

### Negative

- Container startup latency (~30s for model loading)
- Need to manage Docker images
- Slightly more complex deployment

## Implementation Notes

- The supervisor process is the `audiobook-supervisor` container, with the Docker socket mounted. It does not mount NVIDIA devices.
- GPU devices stay on the worker containers. The supervisor requests them with `--gpus all` through the socket.
- The host starter writes hardware JSON. The supervisor reads `HARDWARE_FILE` and does not probe `pynvml` inside the container.
- Uses Docker SDK for container management
- Worker images are pre-built and versioned
- Models are mounted from host (not baked into images) for fast updates
- Container logs are captured and uploaded to S3 for debugging
