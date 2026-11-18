# Plan: Install audiobook-pipeline-rev2 (standalone) bound to GPU 1

## Goal

Install the repo's standalone product on this host so the coordinator and node run locally, with all GPU workloads (TTS worker and recipe containers) restricted to physical GPU 1, leaving GPU 0 (and its running LLM) untouched.

## Current context / assumptions

- Repo root: `/home/ty/projects/audiobook-pipeline-rev2` (branch `main`, clean).
- OS: Ubuntu (sudo available as user `ty`; user is in `docker` and `audiobook` groups).
- GPUs: two identical NVIDIA GeForce RTX 5060 Ti. GPU 0 runs `llama-server` (~15.4 GB) and GNOME; GPU 1 runs GNOME + small Python jobs. Target is GPU 1.
- Docker 29.8.0, nvidia-container-toolkit 1.20.2 installed. `nvidia-smi` works.
- No prior rev2 install: `/etc/audiobook` and `/opt/audiobook` do not exist.
- A *different* (old) audiobook stack is running: containers `audiobook-coordinator-1` (port 8000), `audiobook-worker-1` (GPU 0), `audiobook-kokoro-1` and `audiobook-voice-1` (GPU 1). Port 8000 is in use.
- CLI dependency gap: system `python3` (3.14) has PyYAML but not `httpx`; `python3-httpx` is available via apt.

## Architecture / proposed approach

Follow the documented standalone install exactly (copy example state file, keep `queues: [prep, convert]`, run `sudo PYTHONPATH=. python3 -m cli standalone install`, read operator key, verify containers). The only deviations are (1) installing the missing `python3-httpx` prerequisite and (2) changing the two `docker.types.GPURequest(count=-1)` call sites to `device_ids=["1"]` so containers see only GPU 1. These deviations are flagged below and require approval before execution.

## Deviations from documented install (require approval)

1. **GPU binding is not documented.** The code uses `count=-1` (all GPUs) in `supervisor/docker_manager.py:80` and `worker/tts/worker.py:45`. Binding to GPU 1 requires editing both. This is a code change, not a documented install step.
2. **Missing CLI dependency.** Documented install assumes `httpx` importable; this host lacks it. Installing `python3-httpx` (apt) is required.
3. **Port 8000 conflict.** The old stack holds port 8000. The documented standalone install binds the coordinator to 8000 and will fail unless the old stack is stopped or the port is changed (changing the port is itself a deviation). Decision needed before install.

## Step-by-step tasks

### Task 0 — Preflight and conflict gate (no changes)

Commands:
```bash
nvidia-smi
sudo ss -ltnp | grep ':8000' || true
sudo docker ps --filter name=audiobook --format '{{.Names}} {{.Ports}}'
```
Expected: nvidia-smi shows two GPUs; port 8000 listener present (old coordinator); old audiobook containers listed.

Decision gate: if the old stack is still running, STOP and ask the user whether to (a) stop the old stack first or (b) abort. Do not stop containers without explicit approval.

### Task 1 — Install missing CLI dependency (deviation)

Command:
```bash
sudo apt-get update && sudo apt-get install -y python3-httpx
```
Verify:
```bash
python3 -c 'import httpx, yaml; print("deps ok")'
```
Expected output: `deps ok`.

### Task 2 — Patch supervisor to request only GPU 1 (deviation, TDD)

File: `supervisor/docker_manager.py`, line 80.

Failing test first: add to `supervisor/tests/test_jit.py` a test that calls `DockerManager.start_worker("tts")` with a runner capturing `gpu`/device info and asserts the real `DockerProbeRunner` path requests device id `"1"`. (Existing tests use `FakeRunner` and do not exercise the real runner; the minimal change is the code edit below. Run `PYTHONPATH=. python3 -m pytest supervisor/tests/test_jit.py` to confirm baseline green before editing.)

Edit:
```python
# supervisor/docker_manager.py
if gpu:
    kwargs["device_requests"] = [docker.types.GPURequest(device_ids=["1"])]
```

Verify:
```bash
PYTHONPATH=. python3 -m pytest supervisor/tests/test_jit.py
```
Expected: all tests pass.

### Task 3 — Patch TTS worker recipe runner to request only GPU 1 (deviation, TDD)

File: `worker/tts/worker.py`, lines 43-45.

Edit:
```python
if os.path.exists("/dev/nvidia0"):
    kwargs["device_requests"] = [docker.types.GPURequest(device_ids=["1"])]
```

Verify:
```bash
PYTHONPATH=. python3 -m pytest worker/tts/tests/test_tts.py
```
Expected: all tests pass.

### Task 4 — (Optional, recommended) Make hardware report reflect GPU 1

File: `cli/flows.py`, `RECORD_HARDWARE` (the `nvidia-smi ... | head -n 1` line) and `supervisor/hardware.py` `_gpu()` (`nvmlDeviceGetHandleByIndex(0)`).

Change `head -n 1` to `sed -n '2p'` and the pynvml index to `1` so the recorded GPU model/VRAM is GPU 1. This is informational only (coordinator stores the GPU descriptor); skip if you want the report to describe GPU 0.

Verify by running the repo tests:
```bash
PYTHONPATH=. python3 -m pytest
```

### Task 5 — Copy and edit the standalone state file

Commands:
```bash
sudo cp deploy/examples/standalone.example.yaml /etc/audiobook/standalone.yaml
sudo python3 - <<'PY'
import re
p="/etc/audiobook/standalone.yaml"
s=open(p).read()
# keep queues as [prep, convert] (already default)
open(p,"w").write(s)
PY
```
Verify contents:
```bash
sudo cat /etc/audiobook/standalone.yaml
```
Expected: `queues:` lists `prep` and `convert`.

### Task 6 — Verify GPU (documented)

Command:
```bash
nvidia-smi
```
Expected: command prints both GPUs with no error.

### Task 7 — Run the documented standalone install

Command:
```bash
sudo PYTHONPATH=. python3 -m cli standalone install --state /etc/audiobook/standalone.yaml
```
Expected first run prints `coordinator installed` and `node installed`. If the coordinator container is still starting and the invite fails, rerun the same command; expected second run prints `coordinator unchanged` and retries the node.

### Task 8 — Read the operator key (documented)

Command:
```bash
sudo awk -F= '/^API_KEY_OPERATOR=/ {print $2}' /etc/audiobook/coordinator.env
```
Expected: a non-empty key string (do not paste into shared logs).

### Task 9 — Confirm containers (documented)

Commands:
```bash
sudo docker compose -f /opt/audiobook/coordinator/docker-compose.dev.yml ps
sudo systemctl status audiobook-supervisor.service --no-pager
sudo docker ps --filter name=audiobook-supervisor
```
Expected: coordinator compose services `Up`/healthy; supervisor service `active (running)`; an `audiobook-supervisor` container present.

### Task 10 — Acceptance check (documented)

Command:
```bash
sudo bash deploy/accept-dev.sh
```
Expected: health/auth/node checks pass (exit 0).

### Task 11 — Verify GPU 1 binding (post-install)

Commands:
```bash
sudo docker inspect audiobook-worker-tts --format '{{.HostConfig.DeviceRequests}}'
sudo docker ps --filter name=audiobook-worker-tts --format '{{.Names}}'
# After submitting a convert job, inspect the recipe container:
sudo docker inspect $(sudo docker ps -q --filter ancestor=audiobook-recipe-michael:local) --format '{{.HostConfig.DeviceRequests}}' 2>/dev/null || true
nvidia-smi --query-compute-apps=pid,process_name,used_gpu_memory --format=csv
```
Expected: DeviceRequests show device id `1` (not all/`0`); rev2 GPU processes appear only on GPU 1; GPU 0 process list is unchanged from pre-install baseline (llama-server still the only heavy process).

## Tests / validation

- Baseline before edits: `PYTHONPATH=. python3 -m pytest` (environment must have deps installed first; see Task 1 plus `pip install -r coordinator/requirements.txt worker/prep/requirements.txt worker/tts/requirements.txt supervisor/requirements.txt` if needed).
- After each code edit: rerun the affected test module, then the full suite.
- Post-install: container DeviceRequests inspection and `nvidia-smi` process check (Task 11).

## Risks, tradeoffs, and open questions

- **Port 8000 conflict with the old stack** (see deviation 3). The documented install will fail to bind 8000 if the old coordinator is running. Decision needed: stop old stack or abort. Do not stop without approval.
- **GPU 1 is shared with old stack** (kokoro/voice containers). Even after binding rev2 to GPU 1, both stacks will share GPU 1. If the intent is a repave, the old stack should be removed first (not part of documented install).
- **Image builds are slow.** The supervisor's `ExecStartPre` builds worker/recipe/probe images on first start; the prior failed service shows a 90s systemd timeout can kill the build. If this happens, do not edit the unit to "fix" it silently; report and discuss (e.g., pre-building images manually) before proceeding.
- **Hardware report.** `record-hardware.sh` reports GPU 0 by default; Task 4 optionally switches it to GPU 1. If skipped, the coordinator's node GPU descriptor describes GPU 0 even though work runs on GPU 1 (cosmetic).
- **No documented GPU selection.** Binding to GPU 1 is a code-level deviation; if a future upstream change alters `GPURequest` usage, these patches must be re-applied. Consider filing this as a GitHub issue on TyTech99/audiobook-pipeline-rev2 to request a documented `gpu_device` setting.
- **LLM endpoint.** The state file seeds `http://127.0.0.1:8080/v1`; if nothing listens there, prep jobs that need LLM analysis will fail at runtime, but install itself succeeds. Out of scope for this plan; configure later via the YAML files.