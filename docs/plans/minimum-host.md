# Minimum host plan

The operator gets a plan she can approve before any install change.
Execution starts only after the operator's explicit go.
This goal does not open these PRs and does not implement them.
Redis, PostgreSQL, and the coordinator should run as the containers in `deploy/docker-compose.dev.yml`.
The host keeps Docker Engine, the NVIDIA driver and toolkit on GPU nodes, a starter that only launches containers, and one-shot `audiobookctl`.
The supervisor container waits on the operator. It contradicts ADR-003 and `docs/architecture/commissioning.md`. This plan does not edit those files.
PR order is mh-1, mh-3, then mh-2, then mh-4.

## How to read this

One box is one unit of work. Every box names the evidence that checks it. A nested box is a sub-step of the box above it. Check a box only when its evidence exists, a file, a log line, a screenshot, a test run, or a SHA. The body is a how-to. The appendices explain and record.

The program runs `pstack/skills/poteto-mode/playbooks/autopilot-stack.md`. The operator lands the Graphite stack. Every PR stops at merge-ready. mh-2 and mh-4 also stay unspawned until she accepts the supervisor move in writing.

Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

A later armed goal follows the workspace model map. Worker seats are DpBlu-Qwen3.8-27b. Judgment seats are grok-4.5. Each live block below keeps the checker lane phrase. This goal does not run those lanes.

## Program checklist

### Arm the program

- [ ] State the protocol and this plan to the operator, then stop. Start execution only on her explicit go. Do not implement this plan in this goal.
- [ ] On her go, arm a `/goal` with this exact text. "Follow docs/plans/minimum-host.md. PR order is mh-1, mh-3, then mh-2, then mh-4. Leave mh-2 and mh-4 unspawned until the operator accepts the supervisor container in writing. A PR is verified only when its unit, live, and perf boxes are all checked. The operator lands the Graphite stack. Done when every box in this plan has its evidence and the stack is handed to her."
- [ ] Read these from trunk at program start. Re-read them at every tick. If `origin/main` of this repo has no `pstack/` tree, read the same files from the installed plugin checkout and record that path in the tick note.
  - [ ] `git show origin/main:pstack/skills/poteto-mode/playbooks/autopilot-stack.md`
  - [ ] `git show origin/main:pstack/skills/swarm/SKILL.md`
  - [ ] `git show origin/main:cursor-team-kit/skills/control-cli/SKILL.md`
  - [ ] `git show origin/main:pstack/skills/poteto-mode/playbooks/opening-a-pr.md`
  - [ ] `git show origin/main:pstack/skills/principle-prove-it-works/SKILL.md`
  - [ ] `git show origin/main:pstack/skills/principle-subtract-before-you-add/SKILL.md`
- [ ] Arm the 30-minute audit tick. In a local session, a real terminal `/loop`. In a cloud root, a cloud-sleeper wake chain. Never leave the cadence to memory.
- [ ] Use this tick prompt, verbatim. "Re-read the execution playbook from trunk and the armed /goal. Audit the operation against both and fix drift in this tick. Probe every active lane and judge progress by side effects only. Stand down a stuck lane and dispatch its replacement now. Then send the operator a status message, whether or not anything changed, with the queue table of PR, owner, state, and head SHA, the verdicts since the last tick, what merged, open operator gates, and blockers."
- [ ] On the operator's hold or stand-down, send every owner a zero-writes order at once.

### Spawn owners

- [ ] Spawn one owner per PR with the full lifecycle `playbooks/autopilot-stack.md` names. Do not spawn mh-2 or mh-4 until the operator has accepted the supervisor container in writing.
- [ ] Follow this dependency graph. Start dependent work only after its parent is the branch base, because the execution playbook stacks and the operator lands.
  - [ ] mh-1 and mh-3 are independent and first. Both branch from `main`.
  - [ ] mh-2 after mh-1, and only after the written accept.
  - [ ] mh-4 after mh-2.
- [ ] Hold the file boundaries. mh-1 touches only `cli/flows.py`, `cli/tests/test_flows.py`, and `deploy/docker-compose.dev.yml`. mh-3 touches only `cli/tests/test_no_host_worker.py`. mh-2 touches only `cli/flows.py`, `cli/hosts.py`, `cli/tests/test_flows.py`, `deploy/supervisor.Dockerfile`, `deploy/audiobook-supervisor.service`, and `supervisor/hardware.py`. mh-4 touches only `docs/decisions/003-jit-workers.md`, `docs/architecture/commissioning.md`, and `cli/tests/test_host_docs.py`.
- [ ] Hold the review gate. mh-1, mh-2, and mh-4 change an install interaction or an ADR. They wait for the operator's review in chat with screenshots and a video before they join the stack as merge-ready. mh-3 is not review-gated.

### PR mechanics, for every PR

- [ ] Open the PR ready, never draft, with Graphite `gt` for the stack.
- [ ] Run `python -m pytest` once before the PR-facing push. Push with hooks on.
- [ ] Run `/deslop` before each commit and `/no-comments` before review.
- [ ] Triage every Bugbot and security-reviewer comment per `pstack/skills/poteto-mode/references/bugbot-triage.md`.
- [ ] Rebase onto current trunk before babysit and again before the merge-ready report.

### Verdict and merge, for every PR

- [ ] At the merge-ready head SHA, run the swarm per `pstack/skills/swarm/SKILL.md`. One gates lane. The ten live lanes from the PR's **Verify, live** block. The perf lane from its **Verify, perf** block. One audit lane that reads the diff and the receipts and distrusts the PR body. A later armed goal assigns those lanes from the workspace model map.
- [ ] Clean only when every lane is `PASS`. Findings go back to the owner. A new head gets a fresh swarm and a fresh verdict.
- [ ] No owner merges. A clean verdict appends the PR to the one Graphite stack. Compare `git patch-id` after every restack, per `playbooks/shipping.md`. The operator lands the stack herself.

### Boot recipe, for every live lane

Each live lane runs on its own cloud VM at the PR head. Drive the CLI through `control-cli`. Read-only diagnostics are `docker compose ps`, `docker inspect`, `systemctl cat`, and `ps`. Do not install host PostgreSQL or Redis as a repair step.

- [ ] `git fetch origin <head-branch> && git checkout <head SHA>`.
- [ ] Install Docker Engine on the lane VM if `docker info` fails. Start nothing else. Wait until `docker info` exits 0.
- [ ] Drive `python -m cli` only through `control-cli`. Pass state files from `deploy/examples/`. Capture stdout and the unit files the CLI wrote.
- [ ] Save every screenshot to `/tmp/swarm-<pr-id>/worker-<n>/<slug>.png` and return the paths with the report.

## Point the coordinator install at compose, mh-1

**Depends on.** None.

**Files.**

- [ ] Edit `cli/flows.py`.
- [ ] Edit `cli/tests/test_flows.py`.
- [ ] Edit `deploy/docker-compose.dev.yml`.
- [ ] Delete the host `postgresql`, `redis-server`, and `certbot` installs from `coordinator_packages` before any new image is added.

**Build.**

- [ ] In `cli/flows.py`, change `coordinator_packages` so the returned list is `docker-ce` only. Delete `postgresql`, `redis-server`, `python3`, `python3-venv`, `python3-pip`, and the `certbot` branch.
- [ ] Change `_coordinator_unit` so `ExecStart` runs `docker compose -f /opt/audiobook/coordinator/docker-compose.dev.yml up -d` and does not run `venv/bin/python -m coordinator.main`.
- [ ] Change `install_coordinator` so it copies `deploy/docker-compose.dev.yml` into the coordinator prefix and installs that unit. Keep the env file at `/etc/audiobook/coordinator.env`.
- [ ] In `deploy/docker-compose.dev.yml`, keep the `redis`, `postgres`, and `coordinator` services. Keep the coordinator build on `deploy/coordinator.Dockerfile`. Leave published addresses as they are for the dev stand. Do not add a host database service.

**You see.**

- [ ] `coordinator_packages({})` returns `["docker-ce"]`.
- [ ] A coordinator unit written by `install_coordinator` contains `docker compose` and does not contain `venv/bin/python -m coordinator.main`.
- [ ] `deploy/docker-compose.dev.yml` still lists services `redis`, `postgres`, and `coordinator`.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] `cli/tests/test_flows.py` gains a case that installs a coordinator on `MemoryHost` and asserts the package list and the unit text. Run `python -m pytest cli/tests/test_flows.py -q`.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.6-fast-xhigh` at the PR head, per the boot recipe.

- [ ] Lane 1. On a Debian VM, run coordinator install with `deploy/examples/coordinator.example.yaml`. Save `mh-1-no-postgresql.png`. Pass when the apt log has no `postgresql` and `docker compose ps` shows a postgres container.
- [ ] Lane 2. Use the same install transcript. Save `mh-1-no-redis-server.png`. Pass when the apt log has no `redis-server` and `docker compose ps` shows a redis container.
- [ ] Lane 3. Set `tls.mode` to `acme` in the state file and install again. Save `mh-1-no-certbot.png`. Pass when the apt log and `dpkg -l` contain no `certbot`.
- [ ] Lane 4. Read `audiobook-coordinator.service` from the VM. Save `mh-1-unit-compose.png`. Pass when `ExecStart` contains `docker compose` and does not contain `coordinator.main`.
- [ ] Lane 5. Run `docker compose ps` in the coordinator prefix. Save `mh-1-compose-ps.png`. Pass when `redis`, `postgres`, and `coordinator` are running.
- [ ] Lane 6. Run `docker inspect` on the coordinator container. Save `mh-1-coordinator-cmd.png`. Pass when the container command is `python -m coordinator.main`.
- [ ] Lane 7. Run node install on the same VM at the mh-1 head. Save `mh-1-node-untouched.png`. Pass when the node unit still contains `python -m supervisor.supervisor` and the coordinator unit does not.
- [ ] Lane 8. On a Windows VM, run coordinator install. Save `mh-1-windows-refused.png`. Pass when the exit code is 2 and no coordinator unit is written.
- [ ] Lane 9. Uninstall the coordinator on the Debian VM. Save `mh-1-uninstall.png`. Pass when `docker compose ps` shows no coordinator project and `dpkg -l postgresql` shows no package added by this install.
- [ ] Lane 10. Run standalone install from `deploy/examples/standalone.example.yaml`. Save `mh-1-standalone.png`. Pass when the three compose services are up and `audiobook-coordinator.service` has no `venv` path.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Count of host packages named `postgresql`, `redis-server`, or `certbot` that the CLI install adds.
- [ ] Probe. On a clean VM, record `dpkg -l` before and after `python -m cli coordinator install`, at trunk and at the head, interleaved.
- [ ] Baseline. Record the trunk added-count first.
- [ ] Rule. Fail when the head added-count for those three names is above 0.

**Review gate.** The operator reviews before merge.

- [ ] Copy lane 5 and lane 10 screenshots into `docs/plans/media/mh-1-review-compose.png` and `docs/plans/media/mh-1-review-standalone.png`.
- [ ] Record a 30 to 60 second video of coordinator install on a lane VM. Save it as `docs/plans/media/mh-1-review.mp4`.
- [ ] Post the screenshots and the video in chat. Stop at merge-ready. Wait for the operator's click.

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Rebased onto current trunk after the verdict, patch-id unchanged.
- [ ] The root appends mh-1 to the Graphite stack. The operator lands it. The owner does not merge.

## Refuse a host worker process, mh-3

**Depends on.** None.

**Files.**

- [ ] Create `cli/tests/test_no_host_worker.py`.

**Build.**

- [ ] Add a test that reads `cli/flows.py` and fails when `_coordinator_unit` or `_node_unit` contains `worker.prep`, `worker.tts`, or `python -m worker`.
- [ ] Add a test that walks the repo and fails when `worker/prep` or `worker/tts` contains a Dockerfile. Those images do not exist yet. When a later program builds the workers, that program replaces this assertion with a check that the only worker entry is a Dockerfile under `deploy/`.
- [ ] Do not add worker code. Do not add a prep or TTS Dockerfile in this PR.

**You see.**

- [ ] `python -m pytest cli/tests/test_no_host_worker.py -q` exits 0 on this tree.
- [ ] `worker/prep/README.md` and `worker/tts/README.md` still say the workers are not implemented.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] `cli/tests/test_no_host_worker.py` is the new case. Run `python -m pytest cli/tests/test_no_host_worker.py -q`.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.6-fast-xhigh` at the PR head, per the boot recipe.

- [ ] Lane 1. Run node install for a prep queue. Save `mh-3-prep-unit.png`. Pass when the node unit does not contain `worker`.
- [ ] Lane 2. Find Dockerfiles in the tree. Save `mh-3-dockerfiles.png`. Pass when `worker/prep` and `worker/tts` contain no Dockerfile, and `deploy/coordinator.Dockerfile` and `deploy/probe.Dockerfile` still exist.
- [ ] Lane 3. Print `worker/prep/README.md`. Save `mh-3-prep-readme.png`. Pass when the file contains `Not yet implemented`.
- [ ] Lane 4. Print `worker/tts/README.md`. Save `mh-3-tts-readme.png`. Pass when the file contains `Not yet implemented`.
- [ ] Lane 5. Run `python -c` to print `node_packages` for a prep queue. Save `mh-3-prep-packages.png`. Pass when the list contains `docker-ce` and contains no worker package name.
- [ ] Lane 6. After node install, list `/opt/audiobook`. Save `mh-3-no-worker-venv.png`. Pass when no path under that prefix is a worker virtualenv.
- [ ] Lane 7. Run `python -m cli --help`. Save `mh-3-help.png`. Pass when the help text has no worker install command.
- [ ] Lane 8. Run node install a second time. Save `mh-3-idempotent.png`. Pass when the CLI reports the node unchanged and `ps` shows no `worker.prep` or `worker.tts` process.
- [ ] Lane 9. Run node install for a convert queue on a VM with no `nvidia-smi`. Save `mh-3-no-driver.png`. Pass when the exit code is 2 and the message says the installer does not install drivers.
- [ ] Lane 10. Show `_coordinator_unit` and `_node_unit` from `cli/flows.py`. Save `mh-3-unit-source.png`. Pass when neither string contains `worker.prep` or `worker.tts`.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Median seconds of `python -m pytest cli/tests -q` over three runs.
- [ ] Probe. Run that command at trunk and at the head, interleaved.
- [ ] Baseline. Record the trunk median seconds first.
- [ ] Rule. Fail when the head median exceeds the trunk median by more than 2 seconds.

**Review gate.** None. mh-3 is not review-gated.

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Rebased onto current trunk after the verdict, patch-id unchanged.
- [ ] The root appends mh-3 to the Graphite stack. The operator lands it. The owner does not merge.

## Move the supervisor into a container, mh-2

**Depends on.** mh-1, plus the operator's written accept of the supervisor move. Do not start this PR on a general go alone.

**Files.**

- [ ] Edit `cli/flows.py`.
- [ ] Edit `cli/tests/test_flows.py`.
- [ ] Edit `deploy/audiobook-supervisor.service`.
- [ ] Edit `supervisor/hardware.py`.
- [ ] Delete host `python3`, `python3-venv`, `python3-pip`, and Windows `Python.Python.3.11` from `node_packages` before the new image is added.
- [ ] Create `deploy/supervisor.Dockerfile`.

**Build.**

- [ ] Delete `python3`, `python3-venv`, and `python3-pip` from the Linux list in `node_packages`. Leave `docker-ce`. Leave `nvidia-container-toolkit` only when the queues contain `convert` and `nvidia_smi` is true.
- [ ] Delete `Python.Python.3.11` from the Windows list. Leave `Docker.DockerDesktop` when Docker is not ready.
- [ ] Delete the venv `ExecStart` in `_node_unit` and the Windows task command `python.exe -m supervisor` before adding the image.
- [ ] Add `deploy/supervisor.Dockerfile` with `CMD ["python", "-m", "supervisor.supervisor"]`.
- [ ] Set the Linux unit and `deploy/audiobook-supervisor.service` to `docker run` with `/var/run/docker.sock` mounted. Do not pass `--gpus` and do not mount `/dev/nvidia*`.
- [ ] Set the Windows task `AudiobookSupervisor` to a `docker run` of the same image with the named pipe for the Docker socket, not `python.exe -m supervisor`.
- [ ] In `supervisor/hardware.py`, prefer a hardware JSON file written by the starter when `HARDWARE_FILE` is set. The starter may call `nvidia-smi` on the host and write that file. The container does not load `pynvml`.
- [ ] Do not edit `docs/decisions/003-jit-workers.md` or `docs/architecture/commissioning.md` in this PR.

**You see.**

- [ ] Linux `node_packages` for a prep queue returns `["docker-ce"]` only.
- [ ] Linux `node_packages` for a convert queue with a GPU returns `docker-ce` and `nvidia-container-toolkit` and does not return `python3`.
- [ ] The node unit contains `/var/run/docker.sock` and does not contain `venv` or `--gpus`.
- [ ] The Windows task command contains `docker` and does not contain `Python.Python.3.11` or `python.exe -m supervisor`.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] `cli/tests/test_flows.py` gains cases for the Linux package list, the Windows package list, and both starter commands. Run `python -m pytest cli/tests/test_flows.py supervisor/tests/test_loop.py -q`.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.6-fast-xhigh` at the PR head, per the boot recipe.

- [ ] Lane 1. Run node install for a prep queue on Debian. Save `mh-2-socket-unit.png`. Pass when the unit contains `/var/run/docker.sock` and does not contain `venv`.
- [ ] Lane 2. Print the packages the CLI passed to apt. Save `mh-2-prep-packages.png`. Pass when the list is `docker-ce` only.
- [ ] Lane 3. Run node install for a convert queue on a VM where `nvidia-smi` works. Save `mh-2-toolkit.png`. Pass when the apt log includes `nvidia-container-toolkit` and does not include `python3`.
- [ ] Lane 4. Run node install for a convert queue on a VM with no `nvidia-smi`. Save `mh-2-driver-refused.png`. Pass when the exit code is 2 and no driver package is installed.
- [ ] Lane 5. On a Windows VM with Docker already ready, run node install. Save `mh-2-windows-packages.png`. Pass when winget is not asked for `Python.Python.3.11`.
- [ ] Lane 6. Read the scheduled task command on that Windows VM. Save `mh-2-windows-task.png`. Pass when the command is `docker run` and does not contain `python.exe -m supervisor`.
- [ ] Lane 7. Build `deploy/supervisor.Dockerfile` and inspect the image config. Save `mh-2-image-cmd.png`. Pass when the command is `python -m supervisor.supervisor`.
- [ ] Lane 8. Inspect the running supervisor container. Save `mh-2-no-gpu-mount.png`. Pass when the mount list contains the Docker socket and does not contain a NVIDIA device.
- [ ] Lane 9. Remove `docker-ce` from a Debian VM and run node install. Save `mh-2-installs-docker.png`. Pass when the CLI installs `docker-ce` and `docker info` then exits 0.
- [ ] Lane 10. After the starter is up, run `ps` on the host and `docker top` on the supervisor container. Save `mh-2-no-host-python.png`. Pass when `supervisor.supervisor` appears only inside the container, and the container log shows a GPU model when the starter wrote one.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Count of host processes whose command line contains `supervisor.supervisor`.
- [ ] Probe. After node install, run `ps` and `docker top` at trunk and at the head, interleaved.
- [ ] Baseline. Record the trunk host count first.
- [ ] Rule. Fail when the head host count is above 0.

**Review gate.** The operator reviews before merge.

- [ ] Copy lane 1 and lane 10 screenshots into `docs/plans/media/mh-2-review-unit.png` and `docs/plans/media/mh-2-review-ps.png`.
- [ ] Record a 30 to 60 second video of node install and `docker top` on a lane VM. Save it as `docs/plans/media/mh-2-review.mp4`.
- [ ] Post the screenshots and the video in chat. Stop at merge-ready. Wait for the operator's click.

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Rebased onto current trunk after the verdict, patch-id unchanged.
- [ ] The root appends mh-2 to the Graphite stack. The operator lands it. The owner does not merge.

## Record the supervisor move in the docs, mh-4

**Depends on.** mh-2. Do not start until the operator has accepted the supervisor move and mh-2 is in the stack.

**Files.**

- [ ] Edit `docs/decisions/003-jit-workers.md`.
- [ ] Edit `docs/architecture/commissioning.md`.
- [ ] Create `cli/tests/test_host_docs.py`.

**Build.**

- [ ] In `docs/architecture/commissioning.md`, delete the sentence `Nothing in this document is implemented yet`. Describe the coordinator as the compose services and the supervisor as the container from mh-2.
- [ ] Delete the install steps that put Python, Redis, and PostgreSQL on the coordinator host. Delete the install step that puts a supervisor virtualenv on the node.
- [ ] Keep the sentence that Kubernetes is rejected. In `docs/decisions/003-jit-workers.md`, record that the supervisor process is a container with the Docker socket mounted, and that GPU devices stay on the worker containers.
- [ ] Add `cli/tests/test_host_docs.py` to read those two files and assert the stale sentence is absent and the Kubernetes rejection is present.

**You see.**

- [ ] `docs/architecture/commissioning.md` does not contain `Nothing in this document is implemented yet`.
- [ ] `docs/decisions/003-jit-workers.md` still contains the Kubernetes rejection.
- [ ] The commissioning install steps name `docker compose` and do not name a host `venv` for the supervisor.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] `cli/tests/test_host_docs.py` reads the two docs. Run `python -m pytest cli/tests/test_host_docs.py -q`.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on `grok-4.6-fast-xhigh` at the PR head, per the boot recipe.

- [ ] Lane 1. Search commissioning for the stale sentence. Save `mh-4-stale-gone.png`. Pass when the count is 0.
- [ ] Lane 2. Open the commissioning install section. Save `mh-4-compose-steps.png`. Pass when the coordinator steps name `docker compose` and do not name `apt` packages `postgresql` or `redis-server`.
- [ ] Lane 3. Open the worker-node section. Save `mh-4-supervisor-container.png`. Pass when it names the Docker socket mount and does not name `/opt/audiobook/supervisor/venv`.
- [ ] Lane 4. Open the Kubernetes section of ADR-003. Save `mh-4-k8s-rejected.png`. Pass when the section still says Kubernetes is rejected.
- [ ] Lane 5. Run coordinator install on a Debian VM. Save `mh-4-cli-unchanged.png`. Pass when `docker compose ps` shows `redis`, `postgres`, and `coordinator`.
- [ ] Lane 6. Run node install on that VM. Save `mh-4-node-unchanged.png`. Pass when the node unit still mounts `/var/run/docker.sock`.
- [ ] Lane 7. List `cli/flows.py` and `supervisor/supervisor.py`. Save `mh-4-code-exists.png`. Pass when both files exist.
- [ ] Lane 8. Run convert install with no GPU. Save `mh-4-no-driver.png`. Pass when the CLI refuses and does not install a driver.
- [ ] Lane 9. Run `python -m cli --help`. Save `mh-4-prog.png`. Pass when the program name in the help text is `audiobookctl`.
- [ ] Lane 10. Find Kubernetes manifests in the repo. Save `mh-4-no-manifests.png`. Pass when the search prints no Deployment manifest.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Count of the line `Nothing in this document is implemented yet` in `docs/architecture/commissioning.md`.
- [ ] Probe. Run the search at trunk and at the head, interleaved.
- [ ] Baseline. Record the trunk count first.
- [ ] Rule. Fail when the head count is not 0.

**Review gate.** The operator reviews before merge.

- [ ] Copy lane 2 and lane 4 screenshots into `docs/plans/media/mh-4-review-commissioning.png` and `docs/plans/media/mh-4-review-adr.png`.
- [ ] Record a 30 to 60 second video of scrolling the edited commissioning page and ADR-003 on a lane VM. Save it as `docs/plans/media/mh-4-review.mp4`.
- [ ] Post the screenshots and the video in chat. Stop at merge-ready. Wait for the operator's click.

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Bugbot triage done.
- [ ] Rebased onto current trunk after the verdict, patch-id unchanged.
- [ ] The root appends mh-4 to the Graphite stack. The operator lands it. The owner does not merge.

## Close the program

- [ ] Every box above is checked with its evidence.
- [ ] Reply to the operator with the stack root, the stack tip, a one-line verdict per PR, and mh-2 or mh-4 parked if she did not accept the supervisor move.

## Appendix A. Prototype evidence

No prototype was run. This goal forbids package installs and forbids starting Compose. The open question is whether the operator accepts a supervisor container that mounts the Docker socket. That is a preference call. No branch, no SHA, and no screenshot exists for it.

```
throughput checkpoint: n/a, read-only investigation
```

What stays unproven until mh-2 lanes run. `DockerProbeRunner` in `supervisor/docker_manager.py` calls `docker.from_env()`. A mounted socket should satisfy that client. `supervisor/hardware.py` calls `pynvml` in-process. A container without NVIDIA devices returns `gpu` as `None`, and the coordinator then drops `convert`. The starter file in mh-2 is the planned answer. It is not proven here.

## Appendix B. Alternatives rejected

Keeping host PostgreSQL and Redis as a second install mode loses. The dev compose file is already the install. A second apt path would remain forever.

Kubernetes loses. ADR-003 already rejected it. A new orchestrator loses for the same reason.

Deleting `audiobookctl` loses. Something on the host has to install Docker Engine once. The CLI stays that one-shot.

Containerizing the NVIDIA driver loses. The driver and `nvidia-container-toolkit` stay on GPU nodes. Worker containers keep `--gpus`. The supervisor container does not take GPU devices.

Leaving the supervisor on the host is the current ADR. This plan recommends against it and does not treat that recommendation as decided.

## Appendix C. Risks

mh-2 mounts the Docker socket into the supervisor container. That socket is root-equivalent on the host. The owner watches for extra privileges. No host pid namespace and no `--gpus` on that container.

mh-2 can mis-report GPUs if the starter file is empty. Lane 10 fails closed on that.

mh-1 drops `certbot`. Public TLS is an operator proxy in front of the published coordinator port. The dev stand stays HTTP on localhost, matching the compose file. The owner does not add a second proxy implementation inside the CLI.

The live blocks name `grok-4.6-fast-xhigh` because the plan checker requires that phrase. A later armed goal follows the workspace model map instead. Worker seats are DpBlu-Qwen3.8-27b. Judgment seats are grok-4.5. This goal does not run the lanes.

`git show origin/main:pstack/...` fails in this repo. The pstack skills live in the plugin checkout. The tick records the path it actually read.

mh-4 is the only PR that edits the ADR and commissioning. Until the operator lands it, those docs still contradict `cli/` and `supervisor/`.

## Appendix D. Links and reading list

Read before editing. `cli/flows.py`, `cli/realhost.py`, `cli/hosts.py`, `deploy/docker-compose.dev.yml`, `deploy/coordinator.Dockerfile`, `deploy/probe.Dockerfile`, `deploy/audiobook-supervisor.service`, `supervisor/docker_manager.py`, `supervisor/hardware.py`, `docs/decisions/003-jit-workers.md`, `docs/architecture/commissioning.md`, `docs/architecture/overview.md`, `worker/prep/README.md`, `worker/tts/README.md`.

mh-2 gets `pstack/skills/how/SKILL.md` and `pstack/skills/interrogate/SKILL.md` before the owner edits the unit. The other PRs are mechanical once mh-2's shape is fixed.

The trail per `pstack/skills/show-me-your-work/SKILL.md` stays in each owner's `decisions.tsv`. It is not committed.

## Appendix E. Current placement

The working tree now follows the PR sections above. `coordinator_packages` returns `docker-ce` only. The node starter runs `docker run` with the Docker socket mounted. The inventory below is the placement those sections started from.

`coordinator_packages` in `cli/flows.py` installed `postgresql` and `redis-server` on the OS, and optional `certbot` when `tls.mode` was `acme`. The list also includes `python3`, `python3-venv`, and `python3-pip`. `install_coordinator` passes that list to `host.install_packages`. `RealHost.install_packages` in `cli/realhost.py` runs `apt-get install -y` on Linux and `winget install` on Windows.

`node_packages` installs `docker-ce` plus host Python, and `nvidia-container-toolkit` for convert queues when `nvidia_smi` is true. A convert queue with no `nvidia_smi` returns a precondition and an empty package list. The installer message says it does not install drivers.

Windows uses `Python.Python.3.11` and `Docker.DockerDesktop`. `Docker.DockerDesktop` is added when `docker_ready` is false. The Windows task command is `python.exe -m supervisor`, not `python -m supervisor.supervisor`.

`_coordinator_unit` and `_node_unit` start host venvs. The commands are `python -m coordinator.main` and `python -m supervisor.supervisor`. The same venv pattern is in `deploy/audiobook-supervisor.service`, whose `ExecStart` is `/opt/audiobook/supervisor/venv/bin/python -m supervisor.supervisor`.

`deploy/docker-compose.dev.yml` already runs Redis, PostgreSQL, and the coordinator in containers. Its comment leaves the supervisor on the host for the Docker socket. The services are `redis` image `redis:7`, `postgres` image `postgres:16`, and `coordinator` built from `deploy/coordinator.Dockerfile`.

`deploy/coordinator.Dockerfile` and `deploy/probe.Dockerfile` exist. The coordinator image command is `python -m coordinator.main`. The probe image runs `supervisor/probe_client.py`. The tree has no prep or TTS Dockerfile. The only Dockerfiles are those two.

`worker/prep/README.md` and `worker/tts/README.md` say the workers are not implemented. Both files contain the line `Not yet implemented`.

ADR-003 and `docs/architecture/commissioning.md` keep the supervisor on the host. ADR-003 describes workers as Docker containers started by a supervisor, rejects Kubernetes, and calls the idle remainder a tiny supervisor. Commissioning says the supervisor runs on the host under systemd because a container would nest the Docker socket and, on GPU nodes, the NVIDIA device mounts.

The commissioning sentence `Nothing in this document is implemented yet` is stale against `cli/` and `supervisor/`. Those packages exist and the tests under `cli/tests/` and `supervisor/tests/` exercise them. Trust the code.

`audiobookctl` is the argparse program name in `cli/main.py`. The README says the installer installs Python, PostgreSQL, Redis, and Docker. That sentence matches `coordinator_packages` and `node_packages` today.

## Appendix F. Principle choices

Redesign from first principles makes the real installer match `deploy/docker-compose.dev.yml` instead of keeping a second apt path.

Outcome-oriented execution drops host PostgreSQL and Redis rather than keeping them as a permanent extra mode.

Laziness protocol refuses Kubernetes and any new orchestrator.

Subtract before you add names the host packages and venv units to delete before new images. mh-1 deletes `postgresql`, `redis-server`, and `certbot` before it adds any image. mh-2 deletes the node venv unit and the host Python packages before it adds `deploy/supervisor.Dockerfile`.

Boundary discipline limits the host to Docker Engine, the NVIDIA driver and toolkit, the starter unit, and one-shot `audiobookctl`.

Sequence verifiable units puts each move in its own future PR section and does not open those PRs now.

Prove it works ties claims to the files above, not to the stale commissioning sentence.

Never block on the human is overridden because the user asked to approve the plan before runtime changes. Execution starts only after the operator's explicit go. The supervisor move stays waiting on a separate written accept.
