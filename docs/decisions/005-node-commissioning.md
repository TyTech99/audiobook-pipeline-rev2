# ADR-005: Node Commissioning and API Keys

## Status

Accepted

## Context

ADR-003 makes each machine run a small supervisor that starts and stops worker containers. The coordinator API only knows about workers. `POST /workers/register` fires when a container starts, and `DELETE /workers/{worker_id}` fires when it stops. That is the container lifetime. It is not how a machine joins or leaves the fleet.

The architecture overview also disagrees with itself on credentials. The security section says workers use signed tokens. The coordinator API and `config/coordinator.yaml` use static API keys in `Authorization: Bearer`.

Commissioning has to pick one story before install steps exist. Two questions:

1. What is the durable record for a machine, as opposed to a container that may live for one job?
2. What credential does that machine hold?

## Decision

### The node is the commissioned object

A **node** is a machine running the supervisor. It has a stable `node_id` chosen by the operator at commission time. The node record lives in PostgreSQL.

A **worker** is one container start. `worker_id` is a new UUID each time the supervisor starts a container. The worker row is ephemeral. Redis holds its online flag. PostgreSQL keeps the row only so job history can name the container that ran a job.

The supervisor registers the node and heartbeats the node. Containers register workers and heartbeat workers. A worker registration is accepted only when `node_id` refers to a node in `commissioned` or `offline`. `pending`, `draining`, and `decommissioned` reject it.

Working hours, allowed queues, and the node credential belong to the node. A container cannot widen its own queues. The coordinator computes effective queues as the overlap of what the operator allowed and what the supervisor last reported for hardware. A node with no GPU never receives `convert`, even if the invite asked for it.

### Two API key classes, no signed tokens in v1

All coordinator calls use `Authorization: Bearer <api-key>`.

| Class | Who holds it | What it can call |
| --- | --- | --- |
| Operator | The person running the fleet. Stored in coordinator config. | Jobs, worker and node reads, commission, drain, decommission, key rotation. |
| Node | One key per node, on that machine only. | `POST /nodes/register`, node heartbeat, worker register, worker heartbeat, job progress, worker unregister. |

The coordinator stores only a hash of each key.

Signed tokens and mTLS wait. They need a rotation protocol and clock checks that this fleet does not have yet. The overview line that required signed tokens is withdrawn.

Keys do not protect Redis. Workers claim jobs with `BRPOP` using the Redis password. Any host that can open the Redis port and knows that password can take work without a node key. Commissioning therefore firewalls Redis to the coordinator host and to commissioned node addresses. The password is still required.

## Alternatives Considered

### Worker self-registration only

The supervisor would start a container, and the container's `POST /workers/register` would be the whole enrollment. That matches the API as first written.

Rejected. An idle node has no container, so the coordinator cannot tell a commissioned machine from an unknown one. Drain and decommission would have nothing stable to name. Hardware and working hours would be re-reported by every container start, and a container could claim queues the operator never allowed.

### One shared worker API key

Every supervisor and container would send the same bearer token from `config/coordinator.yaml`.

Rejected. Decommissioning one machine would mean rotating the key on every other machine. A leaked worker config would be a fleet-wide credential.

### Signed tokens at commission time

The coordinator would sign `{node_id, exp}` with a private key, and the supervisor would present that token.

Rejected for v1. Expiry forces a refresh path before the supervisor even exists. A node key in a `0600` file is the same trust level on a single-operator fleet, and revocation is a row update.

## Consequences

### Positive

- A node can be offline, with no container running, and still be in the fleet.
- Decommission revokes one key and stops one supervisor.
- Prep and TTS images stay generic. The supervisor injects the node key, Redis password, and S3 credentials at start.

### Negative

- The coordinator API gains a node resource. Worker register now depends on it.
- Redis still has a shared password. Losing a node means removing its address from the firewall, not only revoking its API key.
- Operator keys still rotate by editing coordinator config and restarting the coordinator.

## Implementation Notes

The install, accept, and remove steps are in [Commissioning](../architecture/commissioning.md).
