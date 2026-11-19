# Commissioning

How a coordinator host and a worker node are installed, checked, and removed.

This follows [ADR-003](../decisions/003-jit-workers.md) and [ADR-005](../decisions/005-node-commissioning.md). The supervisor is the process that stays up. Worker containers come and go. Commissioning enrolls the machine, not a container.

`audiobookctl` in `cli/` installs the coordinator and the supervisor. Prep and TTS workers are not implemented yet.

## Two hosts

| Host | What runs on it | What "installed" means |
| --- | --- | --- |
| Coordinator | Docker Engine, then the compose services Redis, PostgreSQL, and the coordinator | An operator can authenticate and the queues exist. |
| Worker node | Docker Engine, the supervisor container, the NVIDIA driver and toolkit on GPU nodes, a model directory on TTS nodes | The node record is `commissioned`, and a probe container starts and exits. |

A single machine can be both for local development. The steps are the same. The dev stand binds coordinator ports to localhost and uses HTTP. Any other layout uses HTTPS.

## Credentials created during install

Generate each secret with `openssl rand -hex 32`. Store them in env files mode `0600`. Do not commit them.

| Secret | Where it lives | Who uses it |
| --- | --- | --- |
| Operator API key | `/etc/audiobook/coordinator.env` on the coordinator host | Job submit, node invite, drain, decommission. |
| Node API key | `/etc/audiobook/node.env` on that node only | Supervisor and the containers it starts. |
| Redis password | Coordinator env, and each node env | Coordinator, supervisor, worker containers. |
| Postgres password | Coordinator env | Coordinator. |
| S3 access key and secret | Coordinator env, and each node env | Coordinator for presigns. Workers for upload and download. |

The coordinator stores SHA-256 hashes of API keys, not the keys. Node keys are shown once, in the invite response.

Redis is a separate trust boundary from the API. Firewall TCP 6379 so only the coordinator host and current node addresses can connect. Revoking a node key does not kick that host off Redis.

## Node record

PostgreSQL table `nodes`:

| Column | Notes |
| --- | --- |
| `node_id` | Operator-chosen slug. Stable for the life of the machine. Primary key. |
| `display_name` | Free text. |
| `state` | `pending`, `commissioned`, `offline`, `draining`, `decommissioned`. |
| `key_hash` | SHA-256 of the current node key. Null after decommission. |
| `previous_key_hash` | Set during rotation. Null otherwise. |
| `previous_key_expires_at` | Old key dies 10 minutes after rotation. |
| `allowed_queues` | `prep`, `convert`, or both. Set by the operator at invite. |
| `effective_queues` | Overlap of `allowed_queues` and the latest hardware report. |
| `hardware` | Last JSON report from the supervisor. |
| `working_hours` | JSON. Source of truth. Containers do not override it. |
| `last_heartbeat_at` | Supervisor heartbeat. |
| `created_at`, `updated_at` | |

Worker rows keep `node_id` as a foreign key. Deleting a worker row does not change the node.

### States

```
pending → commissioned → offline → commissioned
                      ↘ draining → decommissioned
offline can also go to draining.
```

- `pending`. Invite exists, supervisor has never registered.
- `commissioned`. Last supervisor heartbeat is newer than 120 seconds.
- `offline`. Heartbeat older than 120 seconds. The node is still in the fleet. In-flight jobs follow the existing visibility timeout.
- `draining`. Supervisor must not start containers. Running containers finish the current job.
- `decommissioned`. Terminal. Key hashes cleared. Further register calls return 401.

`offline` is not a failure of commissioning. A node inside its quiet hours, or a node whose supervisor process is stopped on purpose, sits in `offline` until the supervisor heartbeats again.

## API used by these steps

Operator calls use an operator key. Node calls use the node key. The shapes live in [Coordinator API](../api/coordinator-api.md).

| Call | Key | When |
| --- | --- | --- |
| `POST /nodes` | Operator | Create `pending` node, return the node key once. |
| `POST /nodes/{node_id}/register` | Node | Supervisor startup. Sends hardware, working hours, queues it believes it can serve. |
| `POST /nodes/{node_id}/heartbeat` | Node | Every 30 seconds. Response may include a command. |
| `POST /nodes/{node_id}/probe` | Operator | Ask the supervisor to start one throwaway container. |
| `POST /nodes/{node_id}/rotate-key` | Operator | New node key. Old key works for 10 minutes. |
| `POST /nodes/{node_id}/drain` | Operator | Enter `draining`. |
| `DELETE /nodes/{node_id}` | Operator | Decommission. |
| `POST /workers/register` | Node | Container start. Rejected unless the node is `commissioned` or `offline`. |

Supervisor heartbeat response:

```json
{
  "status": "ok",
  "server_time": "2026-11-17T10:00:30Z",
  "node_state": "commissioned",
  "command": "run"
}
```

`command` is `run`, `drain`, or `start_probe`. `start_probe` includes `probe_id` and `worker_type`.

## Coordinator host

### Install

1. Install Docker Engine. `audiobookctl` installs `docker-ce`. Do not install database packages or a certificate client on the host.
2. Run coordinator install. It writes `/etc/audiobook/coordinator.env` and copies the compose project to `/opt/audiobook/coordinator`. The unit runs `docker compose -f /opt/audiobook/coordinator/docker-compose.dev.yml up -d`.
3. That compose project runs Redis, PostgreSQL, and the coordinator. The coordinator creates tables on startup, including `nodes`. The database user and database name come from the compose file.
4. Create bucket `audiobook-pipeline` on the configured S3 endpoint. MinIO is fine for the dev stand.
5. Open the coordinator port to operators. The dev stand publishes Redis on `127.0.0.1` only. Public TLS is an operator proxy in front of port 8000.

`config/coordinator.yaml` today lists `${API_KEY_1}` and `${API_KEY_2}` as if every key were static config. Implementation replaces that list with operator keys only. Node keys go in the `nodes` table, per ADR-005.

### Accept the coordinator

Run these from a client that should be allowed to call the API.

1. `GET /health` with no key returns 200. Health stays unauthenticated so a process supervisor can poke it.
2. `GET /workers` with no key returns 401.
3. `GET /workers` with the operator key returns 200 and an empty list.
4. `redis-cli -a "$REDIS_PASSWORD" PING` on the coordinator host returns `PONG`.
5. From a host that is not the coordinator and not a commissioned node, a TCP connection to port 6379 fails.
6. `psql` as the coordinator user can `select` from `nodes`.

Do not commission a node against a coordinator that fails any of these.

## Worker node

The supervisor is a container started by `audiobook-supervisor.service`. The unit mounts `/var/run/docker.sock` and does not mount NVIDIA devices. `record-hardware.sh` on the host writes `/etc/audiobook/hardware.json` before the container starts. Worker containers still receive GPU access from the supervisor through the Docker socket.

### Install

1. Install Docker Engine. On a TTS node, install the NVIDIA container toolkit and confirm `docker run --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi` prints the GPU. The installer does not install the NVIDIA driver.
2. Do not install a supervisor virtualenv. `audiobookctl` writes the supervisor image build under `/opt/audiobook/supervisor` and starts it with `docker run`.
3. Pick `node_id`. Lowercase slug, unique in this coordinator. Example: `gpu-study` or `vps-prep-1`. Write it to `/etc/audiobook/node_id`.
4. On the coordinator, as an operator:

   ```
   POST /nodes
   {
     "node_id": "gpu-study",
     "display_name": "study GPU box",
     "allowed_queues": ["convert"],
     "working_hours": {
       "enabled": true,
       "start": "22:00",
       "end": "08:00",
       "timezone": "America/New_York"
     }
   }
   ```

   The response carries `node_api_key` once. A CPU prep node sends `"allowed_queues": ["prep"]`. A machine that does both lists both queues.

5. On the node, write `/etc/audiobook/node.env` mode `0600`:

   ```
   NODE_ID=gpu-study
   API_KEY=<node_api_key from the invite>
   COORDINATOR_URL=https://coordinator.example:8000/api/v1
   REDIS_HOST=10.0.0.5
   REDIS_PORT=6379
   REDIS_PASSWORD=<redis password>
   S3_ENDPOINT=https://s3.example
   S3_ACCESS_KEY=...
   S3_SECRET_KEY=...
   S3_BUCKET=audiobook-pipeline
   ```

6. Copy `config/supervisor.yaml`. Set `node.id` from `NODE_ID`, the coordinator URL, image references pinned by digest, and the model mount for TTS. Image names in the sample config (`registry.example.com/...:latest`) are placeholders. Pin a digest before the accept step. A floating tag makes a later probe unreproducible.
7. Create the model directory, owned by the user that will run inside the TTS container. The image does not contain weights. ADR-003 mounts them from the host.
8. Add the node's address to the Redis firewall.
9. Install `audiobook-supervisor.service` — the CLI generates the unit; there is no static unit file. `ExecStart` is `docker run` with `/var/run/docker.sock` mounted and `HARDWARE_FILE` set. `EnvironmentFile=/etc/audiobook/node.env`. The unit is `[Install]`-less (started by the installer or `node update`'s `systemctl restart`) and carries `TimeoutStartSec=1800` because its `ExecStartPre` builds every image; cold builds take minutes.
10. Start the unit. The supervisor Python runs in the container, not from a host virtualenv.

On startup the supervisor reads hardware and calls `POST /nodes/{node_id}/register`. The coordinator moves the row from `pending` to `commissioned`, stores the hardware JSON, and sets `effective_queues`. If the node has no GPU, `convert` is dropped from `effective_queues` and the response says so. The supervisor logs that and does not start TTS containers.

If register returns 401, the key is wrong or the node was decommissioned. Register is safe to repeat after a supervisor restart. A second supervisor using the same node key will also register. Do not share one node key across machines.

### Accept the node

1. `GET /nodes/gpu-study` with the operator key shows `state: commissioned`, a hardware block, and `effective_queues` matching the machine.
2. Stop the supervisor. Wait past 120 seconds. `GET /nodes/gpu-study` shows `offline`. Start it again. State returns to `commissioned`. This checks the heartbeat timeout without touching a job.
3. Issue a probe:

   ```
   POST /nodes/gpu-study/probe
   { "worker_type": "tts" }
   ```

   The next supervisor heartbeat returns `start_probe`. The supervisor starts the pinned image with `PROBE=1`, the node key, and the Redis settings. No model load and no job claim. The container calls `POST /workers/register` with a fresh `worker_id` and `type: tts`, heartbeats once with `"status": "probe_ok"`, then exits 0. The supervisor removes the container if it is still running after 60 seconds.

4. `GET /nodes/gpu-study` shows the probe `passed` and the `worker_id`. `GET /workers` does not list that worker as online after the container exits.
5. For a prep node, probe with `"worker_type": "prep"`.
6. Confirm the idle path with a real job only after probes pass. Submit one prep or convert job, watch the supervisor start the normal image, see the worker row go online, then see the container stop after `idle.timeout_minutes` once the queue is empty. Default in `config/supervisor.yaml` is 5 minutes.

A failed probe leaves the node `commissioned`. Fix the image, the GPU mount, or the clock, and probe again. Do not decommission just because a probe failed.

### What the container receives

The supervisor passes the environment below on every start, probe or real. It does not bake these into the image.

- `NODE_ID`, `WORKER_ID` (new UUID), `WORKER_TYPE`
- `API_KEY` (the node key), `COORDINATOR_URL`
- `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`
- `S3_*` as on the node
- `PROBE=1` only for a probe
- GPU devices and the model mount only for `tts`, and only when the node hardware report includes a GPU

The container registers with the queues from the node's `effective_queues` for its type, not from a copy of the supervisor YAML baked into the image.

## Single-host dev stand

Use one machine when developing the coordinator and one prep worker.

1. Run the coordinator install against localhost. Bind Redis to `127.0.0.1` only. The firewall rule is then "Redis is not reachable off the machine."
2. HTTP coordinator URL `http://127.0.0.1:8000/api/v1` is allowed in this layout only.
3. Invite a node `dev-prep` with queue `prep` and `working_hours.enabled: false`.
4. Run the supervisor on the same host. Point Redis at `127.0.0.1`.
5. Accept with a prep probe, then one real prep job using a local MinIO bucket.

TTS on the dev stand is the same pattern plus the GPU check. Skip it when the machine has no GPU. Do not pretend `effective_queues` contains `convert`.

## Key rotation

### Node key

1. `POST /nodes/{node_id}/rotate-key` with the operator key.
2. Write the new key into `/etc/audiobook/node.env`.
3. `systemctl restart audiobook-supervisor`.
4. The old key still works for 10 minutes, so a container that is mid-job can heartbeat until it exits. New containers get the new key from the supervisor process environment after the restart.

Restarting the supervisor does not kill a running worker container. The container keeps the env it was started with. That is why the overlap exists.

### Operator key

Edit `/etc/audiobook/coordinator.env`, restart the coordinator. There is one coordinator process family in v1, so this is a brief API outage. Workers keep claiming from Redis during that outage. They retry register and heartbeat until the coordinator is back. Do not build a local job spool for this. The overview sentence that said workers queue jobs locally is not part of commissioning.

### Redis password

Change the password, restart Redis, update the coordinator env, restart the coordinator, then update each node and restart each supervisor. Plan this as a maintenance window. In-flight containers still hold the old password and will fail their next Redis call. Drain the fleet first.

## Decommission a node

1. `POST /nodes/{node_id}/drain` with the operator key.
2. Supervisor heartbeats start returning `drain`. It stops launching containers. Containers already in a job finish that job. They do not `BRPOP` another one.
3. Wait until `GET /workers?node_id={node_id}` shows no online workers, or until the operator decides the grace period is over. Default grace is 60 minutes from the drain call. Past that, the supervisor sends `SIGTERM` to its worker containers, waits 60 seconds, then `SIGKILL`.
4. `DELETE /nodes/{node_id}`. State becomes `decommissioned`. Key hashes are cleared. Later heartbeats return 401. A `pending` invite that never started can be deleted without a drain. A `commissioned` node cannot. Drain it, or wait until a missed heartbeat moves it to `offline`.
5. On the machine: `systemctl disable --now audiobook-supervisor`, delete `/etc/audiobook/node.env` and `/etc/audiobook/node_id`, and remove the node's address from the Redis firewall.
6. Remove Docker images and the model directory only if the machine is leaving for good. A later recommission is a new invite with the same or a new `node_id`. `decommissioned` ids can be invited again. The new invite mints a new key and sets state back to `pending`.

Drain does not unregister worker rows by itself. The container still calls `DELETE /workers/{worker_id}` on the way out. If the container is killed, the worker heartbeat timeout marks it offline and the visibility timeout returns the job.

## Out of scope for this procedure

- Image build and registry hosting. Commissioning pins a digest that already exists.
- Voice recipe install. Recipes are a separate repo. A TTS node needs the recipe image at job time, which is not the same as the worker-tts supervisor image.
- Multi-operator accounts, SSO, and per-user job ACLs.
- Signed request tokens and mTLS. ADR-005 defers both.
- Redis Sentinel. One Redis, with the firewall above, until the fleet outgrows it. ADR-001 already notes Sentinel as later work.
