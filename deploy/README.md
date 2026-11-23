# Deploy

Install the coordinator and nodes with `audiobookctl` and the state files in `examples/`.

## State file versus runtime config

A state file is input to `audiobookctl`. The CLI reads it, generates secrets where fields are empty, and writes env files. The state file is not the runtime config. Runtime config lives in `config/` and uses `${VAR}` expansion from the env files the CLI generates.

## Pick a workload

| Goal | Copy | Run on |
| --- | --- | --- |
| One Linux host | `examples/standalone.example.yaml` | that host |
| Coordinator only | `examples/coordinator.example.yaml` | coordinator host |
| Add a node | invite, then `examples/node.example.yaml` | node host |

## Secret handoff

| Value | Created where | Copied to |
| --- | --- | --- |
| `COORDINATOR_ID` | coordinator env (generated if state empty) | node state `coordinator_id` |
| `API_KEY_OPERATOR` | coordinator env | node state `operator_api_key`; invite CLI flag |
| `REDIS_PASSWORD` | coordinator env | node state `redis.password` |
| `node_api_key` | `node invite` JSON (once) | node state `node_api_key` |

The CLI writes these values to `/etc/audiobook/coordinator.env` on the coordinator host. It does not print them. Read them with:

```bash
sudo awk -F= '/^(COORDINATOR_ID|API_KEY_OPERATOR|REDIS_PASSWORD)=/ {print}' /etc/audiobook/coordinator.env
```

## Config

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

The endpoint must speak OpenAI-compatible chat completions. At install, an optional `llm:` block in the state file seeds these YAML files under the node prefix. After install, edit the YAML files directly.

### Storage

Local file storage under `STORAGE_ROOT`, default `/var/lib/audiobook`. Prep packages go to `packages/{job_id}/`, audio output goes to `audio/{job_id}/`. No S3 required.

### Secrets

- `API_KEY_OPERATOR` in `/etc/audiobook/coordinator.env` (coordinator host)
- `API_KEY` (node key) in `/etc/audiobook/node.env` (node host)
- `REDIS_PASSWORD` in both env files

## How to install standalone

Use this when one Debian or Ubuntu host runs the coordinator and the only node.

1. Copy the example state file.

```bash
sudo cp deploy/examples/standalone.example.yaml /etc/audiobook/standalone.yaml
```

2. If this machine should take convert jobs, check the GPU.

```bash
nvidia-smi
```

If that command fails, edit `queues` in the state file so the only entry is `prep`.

3. Install.

```bash
sudo PYTHONPATH=. python3 -m cli standalone install --state /etc/audiobook/standalone.yaml
```

A finished run prints `coordinator installed` and `node installed`. If the coordinator container is still starting, the invite can fail. Run the same command again. The second run prints `coordinator unchanged` and retries the node.

4. Read the operator key. It is not printed by the installer.

```bash
sudo awk -F= '/^API_KEY_OPERATOR=/ {print $2}' /etc/audiobook/coordinator.env
```

5. Confirm the containers. The supervisor service builds its image on first start, so that start is the slow one.

```bash
sudo docker compose -f /opt/audiobook/coordinator/docker-compose.dev.yml ps
sudo systemctl status audiobook-supervisor.service --no-pager
sudo docker ps --filter name=audiobook-supervisor
```

## How to install coordinator

Use this when other machines will register as nodes.

1. Copy the example state file.

```bash
sudo cp deploy/examples/coordinator.example.yaml /etc/audiobook/coordinator.yaml
```

2. Set `api_url`, `redis_bind`, and `redis_advertise` to this host's private IP. The CLI binds the copied compose file to `redis_bind`.

3. Install.

```bash
sudo PYTHONPATH=. python3 -m cli coordinator install --state /etc/audiobook/coordinator.yaml
```

4. Read both secrets from `/etc/audiobook/coordinator.env`. Nodes need `API_KEY_OPERATOR` and `REDIS_PASSWORD`.

```bash
sudo awk -F= '/^(API_KEY_OPERATOR|REDIS_PASSWORD)=/ {print}' /etc/audiobook/coordinator.env
```

## How to invite and install a node

Use this when a host should join an existing coordinator.

1. On the coordinator, read the operator key and Redis password.

```bash
sudo awk -F= '/^(API_KEY_OPERATOR|REDIS_PASSWORD)=/ {print}' /etc/audiobook/coordinator.env
```

2. Invite the node. Run this from any machine that can reach the coordinator API. `--json` prints the one-time node key.

```bash
sudo PYTHONPATH=. python3 -m cli node invite \
  --coordinator-url http://10.0.0.5:8000/api/v1 \
  --operator-api-key "$API_KEY_OPERATOR" \
  --node-id vps-prep-1 --queues prep \
  --redis-host 10.0.0.5 --redis-password "$REDIS_PASSWORD" --json
```

3. On the node, copy the example state file.

```bash
sudo cp deploy/examples/node.example.yaml /etc/audiobook/node.yaml
```

4. Set these fields in the state file:

   - `node_id` to the id you invited
   - `queues` to match what you passed to invite
   - `coordinator_url` to the same URL you passed to invite
   - `coordinator_id` from the invite JSON
   - `node_api_key` from the invite JSON
   - `operator_api_key` to `API_KEY_OPERATOR`, so a later uninstall can drain the node
   - `redis.host` to the coordinator's private IP
   - `redis.password` to `REDIS_PASSWORD`
   - optional `llm:` block to seed `config/supervisor.yaml` and `config/worker-*.yaml`

5. Install as root.

```bash
sudo PYTHONPATH=. python3 -m cli node install --state /etc/audiobook/node.yaml
```

This installs `docker-ce` if it is missing, writes `/etc/audiobook/node.env`, and installs `audiobook-supervisor.service`. The service writes `/etc/audiobook/hardware.json` from `nvidia-smi` when the driver is present, builds the supervisor image, and runs that container with `/var/run/docker.sock` mounted.

6. Confirm the supervisor is running.

```bash
sudo systemctl status audiobook-supervisor.service --no-pager
sudo docker ps --filter name=audiobook-supervisor
```

### Convert node

Do everything in the prep section, with these differences.

- Install the NVIDIA driver before the CLI. `nvidia-smi` must print the GPU.
- Invite with `--queues convert`, or `--queues prep,convert` when the same machine does both. Set `queues` in the state file to match.
- On Linux, the package list adds `nvidia-container-toolkit`.
- On Windows, after install, set `gpu` in `C:\ProgramData\audiobook\hardware.json`. The installer writes `gpu` as null.

### Windows node

Windows can run a node only. Open PowerShell as Administrator and run the CLI from the checkout.

1. Build the supervisor image. The scheduled task does not build it.

```powershell
docker build -t audiobook-supervisor:local -f deploy/supervisor.Dockerfile .
```

2. Copy the example state file to `C:\ProgramData\audiobook\node.yaml` and set the same fields as on Linux.

3. Install.

```powershell
$env:PYTHONPATH = "."
python -m cli node install --state C:\ProgramData\audiobook\node.yaml
```

Exit code 4 means Docker Desktop was installed and Windows wants a reboot. Reboot, then run the same install again.

The task name is `AudiobookSupervisor`. It runs at startup as SYSTEM. Start Docker Desktop before you run the task, or the container exits.

## Updating code on an installed host

After changing code in the repo, deploy it with the CLI update commands — not with a manual `rsync`. Both build prefixes keep the Python package in a nested same-named directory (`/opt/audiobook/coordinator/coordinator/`, `/opt/audiobook/supervisor/supervisor/`) because the Dockerfiles build from the prefix root and `COPY` that directory. A flat `rsync` into the prefix top level silently deploys nothing: the build succeeds against the untouched nested copy (issue #53).

```bash
sudo PYTHONPATH=. python3 -m cli coordinator update --source .
sudo PYTHONPATH=. python3 -m cli node update --source .
```

Run them from the repo checkout you want to deploy (any branch); `--source` is that checkout.

What each command does:

1. Syncs package code into the dir the build actually reads — the nested package dir for `coordinator update`; the nested supervisor package plus the flat `worker/` and `recipe/` dirs for `node update`.
2. Copies the repo `deploy/` build inputs (Dockerfiles, `build-images.sh`) verbatim and stamps the prefix `REVISION` with the source commit.
3. Rebuilds and recreates:
   - `coordinator update` runs `docker compose build` then `up -d --force-recreate`, then verifies the running container's image equals the built image — plain `up -d` was once observed printing "Running" without recreating a container whose image was stale.
   - `node update` restarts `audiobook-supervisor.service`; the unit's `ExecStartPre` rebuilds the supervisor, worker, and recipe images and recreates the supervisor container. Running worker containers self-replace on the next supervisor heartbeat once their image tag moves.
4. Exits non-zero with a message on any failed build, failed recreate, or image mismatch.

What update never touches: `config/*.yaml`, the env files, and the installed `docker-compose.dev.yml` are owned by `coordinator/node install` and `apply`. Operator edits there (LLM endpoints, working hours, the Redis bind rewrite) survive an update. Changing the compose file or unit definitions is an install/apply change, not an update.

The update commands refuse to run on Windows, without root, against a source that is not the repo checkout, or on a host where the role was never installed.

## Usage

Job submission and polling live in the [root README](../README.md#usage). The coordinator API reference is in [docs/api/coordinator-api.md](../docs/api/coordinator-api.md).

## Accept checks

Run `deploy/accept-dev.sh` on a dev stand. It checks health, auth, and the node list. For a full accept procedure, see `docs/architecture/commissioning.md`.

## Artifacts in this directory

- `coordinator.Dockerfile` builds the coordinator image. The CLI copies it to the coordinator prefix during install.
- `supervisor.Dockerfile` builds the supervisor image. The CLI copies it to the node prefix and the systemd unit builds it on first start.
- `worker-prep.Dockerfile` builds the prep worker image (`audiobook-worker-prep:local`). The CLI copies it to the node prefix and `build-images.sh` builds it before the supervisor starts.
- `worker-tts.Dockerfile` builds the TTS worker image (`audiobook-worker-tts:local`). The TTS container mounts `/var/run/docker.sock` at runtime so it can launch recipe containers.
- `recipe-michael-cosyvoice.Dockerfile` builds the CosyVoice 3 michael voice recipe image (`audiobook-recipe-michael-cosyvoice:local`).
- `probe.Dockerfile` builds the probe image. The supervisor uses it for throwaway container checks.
- `build-images.sh` builds all local worker, recipe, and probe images. The node systemd unit calls it in `ExecStartPre` before starting the supervisor.
- `docker-compose.dev.yml` runs Redis, Postgres, and the coordinator. The CLI copies it to the coordinator prefix and rewrites the Redis bind address.
- The supervisor's systemd unit (`audiobook-supervisor.service`) is generated by the CLI at install time from `cli/flows.py` (`_node_unit`) — the same definition `node update` restarts. It includes the image-build `ExecStartPre` chain and a build-capable `TimeoutStartSec`. No static unit file ships in this directory; a stale static copy was removed because it had drifted from the generated unit (missing `build-images.sh`, missing `--network host`, `User=audiobook`).
- `accept-dev.sh` is a manual health check for a coordinator on localhost.
- `dev.env.example` is a template for the env file the compose project reads. The CLI generates the real env file; this template is for manual compose runs.

## Image tags and overrides

The supervisor reads image tags from `config/supervisor.yaml` under `docker.images`. Env vars override the YAML values:

| Env var | Default tag | Used by |
| --- | --- | --- |
| `PREP_IMAGE` | `audiobook-worker-prep:local` | Prep worker containers |
| `TTS_IMAGE` | `audiobook-worker-tts:local` | TTS worker containers |
| `RECIPE_IMAGE` | `audiobook-recipe-michael-cosyvoice:local` | Injected as `DEFAULT_RECIPE_IMAGE` into TTS workers |
| `PROBE_IMAGE` | `audiobook-probe:dev` | Probe containers |

Set these in `/etc/audiobook/node.env` to point at a different registry or tag.

## See also

- Root README for prerequisites, config, and usage.
- `docs/architecture/commissioning.md` for the credential model, node states, and decommission.
- `docs/api/coordinator-api.md` for the invite and register endpoints.
