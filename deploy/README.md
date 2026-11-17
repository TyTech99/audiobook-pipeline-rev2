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

## Accept checks

Run `deploy/accept-dev.sh` on a dev stand. It checks health, auth, and the node list. For a full accept procedure, see `docs/architecture/commissioning.md`.

## Artifacts in this directory

- `coordinator.Dockerfile` builds the coordinator image. The CLI copies it to the coordinator prefix during install.
- `supervisor.Dockerfile` builds the supervisor image. The CLI copies it to the node prefix and the systemd unit builds it on first start.
- `probe.Dockerfile` builds the probe image. The supervisor uses it for throwaway container checks.
- `docker-compose.dev.yml` runs Redis, Postgres, and the coordinator. The CLI copies it to the coordinator prefix and rewrites the Redis bind address.
- `audiobook-supervisor.service` is the systemd unit for the supervisor. The CLI installs it on Linux nodes.
- `accept-dev.sh` is a manual health check for a coordinator on localhost.
- `dev.env.example` is a template for the env file the compose project reads. The CLI generates the real env file; this template is for manual compose runs.

## See also

- Root README for prerequisites and the compact standalone recipe.
- `docs/architecture/commissioning.md` for the credential model, node states, and decommission.
- `docs/api/coordinator-api.md` for the invite and register endpoints.
