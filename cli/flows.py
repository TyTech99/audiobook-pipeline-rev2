from __future__ import annotations

import secrets
import subprocess
import time
from pathlib import Path

import yaml

from cli.hosts import linux_paths, windows_paths
from cli.policy import PolicyError, validate_api_url, validate_redis_advertise
from cli.state import dump_env, parse_env

EXIT_OK = 0
EXIT_PRECONDITION = 2
EXIT_FAILED = 3
EXIT_REBOOT = 4


SUPERVISOR_IMAGE = "audiobook-supervisor:local"

# Seconds to wait for the recreated supervisor container to appear in
# `docker inspect` before declaring the deploy failed. systemctl returns as
# the unit's docker run forks, so the container registration can lag the
# restart call by a moment (observed live on ubuDual5060).
_VERIFY_WAIT_SECONDS = 15.0


def _git_revision(cwd: Path) -> str:
    """Return the git commit hash at cwd, or 'unknown' if unavailable."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=str(cwd),
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "unknown"


def _deployed_revision() -> str:
    """Return the git commit hash of the deployed code, or 'unknown' if unavailable."""
    return _git_revision(Path(__file__).resolve().parents[1])


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    """Run a deploy command and capture output without raising. Tests stub this."""
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def _tail(result: subprocess.CompletedProcess, limit: int = 400) -> str:
    text = (result.stderr or result.stdout or "").strip().replace("\n", " | ")
    return text[-limit:]


def _image_ids_match(container_image: str, built_image: str) -> bool:
    """Compare a container's image reference with a built image ID.

    `docker inspect --format {{.Image}}` returns `sha256:<full id>`;
    compose image listings may return a short id. Compare on the hex part.
    """
    a = (container_image or "").split("sha256:")[-1]
    b = (built_image or "").split("sha256:")[-1]
    return bool(a) and bool(b) and (a.startswith(b) or b.startswith(a))


def coordinator_packages(state: dict) -> list[str]:
    return ["docker-ce"]


def node_packages(state: dict, host, windows: bool) -> tuple[int, list[str]]:
    queues = list(state.get("queues") or [])
    if "convert" in queues and not host.nvidia_smi():
        return EXIT_PRECONDITION, []
    if windows:
        names = []
        if not host.docker_ready:
            names.append("Docker.DockerDesktop")
        return EXIT_OK, names
    names = ["docker-ce"]
    if "convert" in queues and host.nvidia_smi():
        names.append("nvidia-container-toolkit")
    return EXIT_OK, names


def _keep(existing: dict, state_value: str, generated: str) -> str:
    if state_value:
        return state_value
    if existing.get(generated):
        return existing[generated]
    return secrets.token_hex(32)


def render_coordinator_env(state: dict, existing: dict) -> dict:
    return {
        "COORDINATOR_ID": _keep(existing, state.get("coordinator_id") or "", "COORDINATOR_ID"),
        "API_KEY_OPERATOR": _keep(existing, state.get("operator_api_key") or "", "API_KEY_OPERATOR"),
        "POSTGRES_PASSWORD": _keep(existing, state.get("postgres_password") or "", "POSTGRES_PASSWORD"),
        "REDIS_PASSWORD": _keep(existing, state.get("redis_password") or "", "REDIS_PASSWORD"),
        "API_URL": state.get("api_url") or "http://127.0.0.1:8000/api/v1",
        "REDIS_BIND": state.get("redis_bind") or "127.0.0.1",
        "REDIS_ADVERTISE": state.get("redis_advertise") or state.get("redis_bind") or "127.0.0.1",
        "S3_ENDPOINT": state.get("s3_endpoint") or "",
    }


def resolve_llm_config(state: dict | None = None, existing: dict | None = None) -> dict:
    """LLM interface settings for config/*.yaml.

    Source order: install state `llm:` block, then any existing YAML llm block,
    then repo defaults. The live source of truth after install is
    config/supervisor.yaml (and the matching worker YAML copies).
    """
    state = state or {}
    existing = existing or {}
    from_state = state.get("llm") if isinstance(state.get("llm"), dict) else {}
    defaults = {
        "base_url": "http://127.0.0.1:8080/v1",
        "api_key": "",
        "model": "local-model",
        "timeout_seconds": 120,
        "slots": 4,
        "required": True,
    }
    out = dict(defaults)
    out.update({k: v for k, v in existing.items() if v is not None and v != ""})
    # Empty api_key from state is intentional; only skip missing keys.
    for key, value in from_state.items():
        if value is None:
            continue
        if key == "api_key" or value != "":
            out[key] = value
    # Flat state keys used by older callers.
    if state.get("llm_base_url"):
        out["base_url"] = state["llm_base_url"]
    if state.get("llm_api_key") is not None and "api_key" not in from_state:
        out["api_key"] = state["llm_api_key"]
    if state.get("llm_model"):
        out["model"] = state["llm_model"]
    if state.get("llm_timeout_seconds") is not None and "timeout_seconds" not in from_state:
        out["timeout_seconds"] = state["llm_timeout_seconds"]
    if state.get("llm_slots") is not None and "slots" not in from_state:
        out["slots"] = state["llm_slots"]
    try:
        out["timeout_seconds"] = int(out.get("timeout_seconds") or 120)
    except (TypeError, ValueError):
        out["timeout_seconds"] = 120
    try:
        out["slots"] = int(out.get("slots") or 0)
    except (TypeError, ValueError):
        out["slots"] = 0
    out["base_url"] = str(out.get("base_url") or "").strip()
    out["api_key"] = str(out.get("api_key") or "")
    out["model"] = str(out.get("model") or "").strip()
    out["required"] = bool(out.get("required", True))
    return out


def apply_llm_to_config_yaml(text: str, llm: dict, *, include_required: bool = False) -> str:
    """Replace or insert the top-level `llm:` mapping in a config YAML document."""
    data = yaml.safe_load(text) or {}
    block = {
        "base_url": llm.get("base_url") or "",
        "api_key": llm.get("api_key") or "",
        "model": llm.get("model") or "",
        "timeout_seconds": int(llm.get("timeout_seconds") or 120),
        "slots": int(llm.get("slots") or 0),
    }
    if include_required:
        block["required"] = bool(llm.get("required", True))
    data["llm"] = block
    return yaml.safe_dump(data, default_flow_style=False, sort_keys=False)


def apply_endpoints_to_config_yaml(text: str, coordinator_url: str, redis_host: str) -> str:
    """Rewrite coordinator.url and redis.host in a config YAML document.

    Used for standalone installs where the supervisor runs with --network host
    and must reach the coordinator/redis at 127.0.0.1 instead of Docker
    service names that don't resolve on the bridge network.
    """
    data = yaml.safe_load(text) or {}
    if coordinator_url:
        coord = data.get("coordinator") or {}
        coord["url"] = coordinator_url
        data["coordinator"] = coord
    if redis_host:
        redis = data.get("redis") or {}
        redis["host"] = redis_host
        data["redis"] = redis
    return yaml.safe_dump(data, default_flow_style=False, sort_keys=False)


def render_node_env(state: dict) -> dict:
    hours = state.get("working_hours") or {}
    redis_cfg = state.get("redis") or {}
    llm = resolve_llm_config(state)
    return {
        "NODE_ID": state.get("node_id") or "local",
        "API_KEY": state.get("node_api_key") or "",
        "OPERATOR_API_KEY": state.get("operator_api_key") or "",
        "COORDINATOR_URL": state.get("coordinator_url") or "",
        "COORDINATOR_ID": state.get("coordinator_id") or "",
        "REDIS_HOST": str(redis_cfg.get("host") or ""),
        "REDIS_PORT": str(redis_cfg.get("port") or 6379),
        "REDIS_PASSWORD": str(redis_cfg.get("password") or ""),
        "HOURS_ENABLED": str(bool(hours.get("enabled", False))).lower(),
        "HOURS_START": str(hours.get("start") or ""),
        "HOURS_END": str(hours.get("end") or ""),
        "HOURS_TIMEZONE": str(hours.get("timezone") or ""),
        "PROBE_IMAGE": state.get("probe_image") or "audiobook-probe:dev",
        # Mirrored for containers; primary operator edit surface is config/*.yaml.
        "LLM_BASE_URL": str(llm.get("base_url") or ""),
        "LLM_API_KEY": str(llm.get("api_key") or ""),
        "LLM_MODEL": str(llm.get("model") or ""),
        "LLM_TIMEOUT_SECONDS": str(llm.get("timeout_seconds") or 120),
        "LLM_SLOTS": str(llm.get("slots") or 0),
    }


def _check_linux_coordinator(host) -> int | None:
    if host.os_name == "windows":
        return EXIT_PRECONDITION
    if host.os_name == "linux" and host.distro not in {"debian", "ubuntu"}:
        return EXIT_PRECONDITION
    if not host.is_root():
        return EXIT_PRECONDITION
    return None


def _guard_urls(state: dict, api_url: str, redis_host: str) -> int | None:
    try:
        validate_api_url(api_url)
        validate_redis_advertise(redis_host, api_url)
    except PolicyError:
        return EXIT_PRECONDITION
    return None


def install_coordinator(host, state: dict, messages: list[str], force: bool = False) -> dict:
    blocked = _check_linux_coordinator(host)
    if blocked:
        return {"code": blocked, "changed": False}
    api_url = state.get("api_url") or "http://127.0.0.1:8000/api/v1"
    redis_host = state.get("redis_advertise") or state.get("redis_bind") or "127.0.0.1"
    blocked = _guard_urls(state, api_url, redis_host)
    if blocked:
        return {"code": blocked, "changed": False}
    paths = linux_paths()
    existing = parse_env(host.read(paths["coordinator_env"]))
    rendered = render_coordinator_env(state, existing)
    text = dump_env(rendered)
    unit = _coordinator_unit(paths["coordinator_prefix"])
    same = host.read(paths["coordinator_env"]) == text and paths["coordinator_unit"] in host.units
    if same and not force:
        messages.append("coordinator unchanged")
        return {"code": EXIT_OK, "changed": False}
    if same and force:
        messages.append("coordinator unchanged (forced redeploy)")
    host.install_packages(coordinator_packages(state))
    host.write(paths["coordinator_env"], text)
    # Also write a .env file in the coordinator prefix so manual `docker compose`
    # invocations (outside systemd) can find the credentials. Must be readable
    # by the user running docker compose (not just root).
    host.write(paths["coordinator_prefix"] + "/.env", text)
    host.chmod(paths["coordinator_prefix"] + "/.env", 0o644)
    host.write(paths["coordinator_prefix"] + "/REVISION", _deployed_revision())
    _install_coordinator_files(host, paths["coordinator_prefix"], state)
    host.install_unit(paths["coordinator_unit"], unit)
    messages.append("coordinator installed")
    return {"code": EXIT_OK, "changed": True}


def apply_coordinator(host, state: dict, messages: list[str], force: bool = False) -> dict:
    return install_coordinator(host, state, messages, force=force)


def install_node(host, state: dict, api, messages: list[str], force: bool = False) -> dict:
    if not host.is_root():
        return {"code": EXIT_PRECONDITION, "changed": False}
    windows = host.os_name == "windows"
    if not windows and host.distro not in {"debian", "ubuntu"}:
        return {"code": EXIT_PRECONDITION, "changed": False}
    api_url = state.get("coordinator_url") or ""
    redis_host = str((state.get("redis") or {}).get("host") or "")
    blocked = _guard_urls(state, api_url, redis_host)
    if blocked:
        return {"code": blocked, "changed": False}
    if windows and host.reboot_pending and not host.docker_ready:
        host.install_packages(["Docker.DockerDesktop"])
        return {"code": EXIT_REBOOT, "changed": False}
    code, packages = node_packages(state, host, windows)
    if code != EXIT_OK:
        messages.append("NVIDIA driver is missing. The installer does not install drivers.")
        return {"code": code, "changed": False}
    if windows and (not host.docker_ready) and host.reboot_pending:
        host.install_packages(packages)
        return {"code": EXIT_REBOOT, "changed": False}
    expected = state.get("coordinator_id") or ""
    if api is not None and expected:
        found = api.health(api_url).get("coordinator_id")
        if found != expected:
            messages.append("coordinator_id does not match. Refusing to register.")
            return {"code": EXIT_PRECONDITION, "changed": False}
    paths = windows_paths() if windows else linux_paths()
    env_path = paths["node_env"]
    text = dump_env(render_node_env(state))
    unit_name = paths.get("node_task") if windows else paths["node_unit"]
    same = host.read(env_path) == text and (
        (windows and unit_name in host.tasks) or (not windows and unit_name in host.units)
    )
    if same and not force:
        messages.append("node unchanged")
        return {"code": EXIT_OK, "changed": False}
    if same and force:
        messages.append("node unchanged (forced redeploy)")
    host.install_packages(packages)
    host.write(env_path, text)
    host.write(paths["node_prefix"] + "/REVISION", _deployed_revision())
    if windows:
        if host.reboot_pending:
            return {"code": EXIT_REBOOT, "changed": False}
        host.write(r"C:\ProgramData\audiobook\hardware.json", '{"cpu":1,"ram_gb":1.0,"gpu":null}\n')
        host.install_task(paths["node_task"], _node_task())
    else:
        _install_supervisor_files(host, paths["node_prefix"], state)
        host.install_unit(paths["node_unit"], _node_unit(paths["node_prefix"]))
    messages.append("node installed")
    return {"code": EXIT_OK, "changed": True}


def apply_node(host, state: dict, api, messages: list[str], force: bool = False) -> dict:
    paths = windows_paths() if host.os_name == "windows" else linux_paths()
    before = host.read(paths["node_env"])
    restarts_before = len(host.restarts)
    result = install_node(host, state, api, messages, force=force)
    if result["code"] != EXIT_OK:
        return result
    after = host.read(paths["node_env"])
    if before is not None and before != after:
        name = paths.get("node_task", paths["node_unit"])
        if host.os_name == "windows":
            host.install_task(name, host.tasks.get(name, ""))
        host.restart(name)
        result["changed"] = True
    elif before == after and len(host.restarts) == restarts_before:
        result["changed"] = False
    return result


def update_role(host, source: str, role: str, messages: list[str]) -> dict:
    """Deploy code from a repo checkout into an installed prefix, then rebuild.

    Syncs package code and the repo deploy/ build inputs only. Runtime state —
    config/*.yaml, env files, and the rewritten compose file — is owned by
    install/apply and never touched here, so an update cannot clobber
    operator-edited LLM endpoints or secrets.

    Coordinator: sync into the nested package dir, then compose build and
    `up -d --force-recreate`, then verify the running container's image
    equals the built image (issue #53: a flat top-level rsync deploys
    nothing, and plain `up -d` was observed leaving a stale container
    running with a success message).
    Node: sync supervisor (nested), worker and recipe (flat), then
    `systemctl restart` the supervisor unit — its ExecStartPre chain already
    rebuilds the supervisor/worker/recipe images and recreates the
    supervisor container. Running workers self-replace on the next
    heartbeat once their image tag moves (#56).
    """
    if host.os_name == "windows":
        messages.append("update is linux-only")
        return {"code": EXIT_PRECONDITION, "changed": False}
    if not host.is_root():
        messages.append("update must run as root (sudo)")
        return {"code": EXIT_PRECONDITION, "changed": False}
    root = Path(source).resolve()
    if not (root / "cli" / "flows.py").is_file():
        messages.append(f"source is not the repo checkout: {source}")
        return {"code": EXIT_PRECONDITION, "changed": False}
    revision = _git_revision(root)
    if role == "coordinator":
        return _update_coordinator(host, root, revision, messages)
    return _update_node(host, root, revision, messages)


def _update_coordinator(host, root: Path, revision: str, messages: list[str]) -> dict:
    paths = linux_paths()
    prefix = paths["coordinator_prefix"]
    compose = prefix + "/docker-compose.dev.yml"
    if host.read(paths["coordinator_env"]) is None or host.read(compose) is None:
        messages.append("coordinator is not installed; run 'coordinator install' first")
        return {"code": EXIT_PRECONDITION, "changed": False}
    package = root / "coordinator"
    if not (package / "main.py").is_file() or not (root / "deploy" / "coordinator.Dockerfile").is_file():
        messages.append("source is missing coordinator/ or deploy/coordinator.Dockerfile")
        return {"code": EXIT_PRECONDITION, "changed": False}
    # The compose build context only sees the nested package dir (#53).
    host.remove_prefix(prefix + "/coordinator")
    _copy_tree(host, package, prefix + "/coordinator")
    host.write(prefix + "/coordinator.Dockerfile", (root / "deploy" / "coordinator.Dockerfile").read_text())
    build = _run(["docker", "compose", "-f", compose, "build", "coordinator"])
    if build.returncode != 0:
        messages.append("coordinator build failed: " + _tail(build))
        return {"code": EXIT_FAILED, "changed": True}
    up = _run(["docker", "compose", "-f", compose, "up", "-d", "--force-recreate", "coordinator"])
    if up.returncode != 0:
        messages.append("coordinator recreate failed: " + _tail(up))
        return {"code": EXIT_FAILED, "changed": True}
    if not _verify_compose_service(compose, "coordinator", messages):
        return {"code": EXIT_FAILED, "changed": True}
    # REVISION records what is DEPLOYED — stamp it only after verification.
    host.write(prefix + "/REVISION", revision)
    messages.append(f"coordinator updated (revision {revision[:12]})")
    return {"code": EXIT_OK, "changed": True}


def _update_node(host, root: Path, revision: str, messages: list[str]) -> dict:
    paths = linux_paths()
    prefix = paths["node_prefix"]
    unit = paths["node_unit"]
    if host.read(paths["node_env"]) is None or unit not in host.units:
        messages.append("node is not installed; run 'node install' first")
        return {"code": EXIT_PRECONDITION, "changed": False}
    packages = {"supervisor": root / "supervisor", "worker": root / "worker", "recipe": root / "recipe"}
    for name, path in packages.items():
        if not path.is_dir() or not any(path.iterdir()):
            messages.append(f"source is missing the {name}/ package")
            return {"code": EXIT_PRECONDITION, "changed": False}
    # supervisor/ is nested; worker/ and recipe/ deploy flat (#53).
    host.remove_prefix(prefix + "/supervisor")
    _copy_tree(host, packages["supervisor"], prefix + "/supervisor")
    host.remove_prefix(prefix + "/worker")
    _copy_tree(host, packages["worker"], prefix + "/worker")
    host.remove_prefix(prefix + "/recipe")
    _copy_tree(host, packages["recipe"], prefix + "/recipe")
    _write_node_build_files(host, root, prefix)
    try:
        host.restart(unit)
    except Exception as exc:  # noqa: BLE001 - a failed systemctl restart is a deploy failure
        messages.append(f"supervisor unit restart failed: {exc}")
        return {"code": EXIT_FAILED, "changed": True}
    # systemctl returns as the new container is being created; docker inspect
    # can miss it for a moment. Bounded retry before declaring failure.
    deadline = time.monotonic() + _VERIFY_WAIT_SECONDS
    container = _run(["docker", "inspect", "audiobook-supervisor", "--format", "{{.Image}}"])
    while container.returncode != 0 and time.monotonic() < deadline:
        time.sleep(1)
        container = _run(["docker", "inspect", "audiobook-supervisor", "--format", "{{.Image}}"])
    built = _run(["docker", "image", "inspect", SUPERVISOR_IMAGE, "--format", "{{.Id}}"])
    if container.returncode != 0 or built.returncode != 0:
        messages.append("supervisor container or image is missing after restart")
        return {"code": EXIT_FAILED, "changed": True}
    if not _image_ids_match(container.stdout.strip(), built.stdout.strip()):
        messages.append("supervisor container does not run the built image; check journalctl -u " + unit)
        return {"code": EXIT_FAILED, "changed": True}
    # REVISION records what is DEPLOYED — stamp it only after verification.
    host.write(prefix + "/REVISION", revision)
    messages.append(f"node updated (revision {revision[:12]})")
    messages.append("worker images rebuilt; running workers self-replace on the next heartbeat")
    return {"code": EXIT_OK, "changed": True}


def _verify_compose_service(compose: str, service: str, messages: list[str]) -> bool:
    """Fail unless the service's running container uses the freshly built image.

    `docker compose up -d` was observed printing "Running" without
    recreating a container whose image was stale (issue #53) — image
    equality is the only trustworthy post-deploy check.
    """
    ps = _run(["docker", "compose", "-f", compose, "ps", "-q", service])
    cid = ps.stdout.strip().splitlines()[0].strip() if ps.stdout.strip() else ""
    if not cid:
        messages.append(service + " container is not running")
        return False
    container = _run(["docker", "inspect", cid, "--format", "{{.Image}}"])
    built = _run(["docker", "compose", "-f", compose, "images", "-q", service])
    built_id = built.stdout.strip().splitlines()[0].strip() if built.stdout.strip() else ""
    if not _image_ids_match(container.stdout.strip(), built_id):
        messages.append(
            service + " container image "
            + container.stdout.strip()[:19]
            + " does not match built image "
            + built_id[:19]
        )
        return False
    return True


def uninstall_node(host, state: dict, api, purge: bool, local_only: bool, messages: list[str]) -> dict:
    paths = windows_paths() if host.os_name == "windows" else linux_paths()
    if not local_only and api is not None and state.get("operator_api_key") and state.get("node_id"):
        api.drain(state["coordinator_url"], state["operator_api_key"], state["node_id"])
        api.delete(state["coordinator_url"], state["operator_api_key"], state["node_id"])
    elif local_only:
        messages.append("coordinator was not updated. The node row may still be there.")
    if host.os_name == "windows":
        host.stop_task(paths["node_task"])
    else:
        host.stop_unit(paths["node_unit"])
    host.remove_prefix(paths["node_prefix"])
    if purge:
        host.remove(paths["node_env"])
    messages.append("node uninstalled")
    return {"code": EXIT_OK, "changed": True}


def uninstall_coordinator(host, purge: bool, messages: list[str]) -> dict:
    blocked = _check_linux_coordinator(host)
    if blocked:
        return {"code": blocked, "changed": False}
    paths = linux_paths()
    host.stop_unit(paths["coordinator_unit"])
    host.remove_prefix(paths["coordinator_prefix"])
    if purge:
        host.remove(paths["coordinator_env"])
    messages.append("coordinator uninstalled")
    return {"code": EXIT_OK, "changed": True}


def _wait_for_coordinator(api, url: str, timeout: int = 120, interval: int = 2) -> bool:
    """Poll the coordinator health endpoint until it responds or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            api.health(url)
            return True
        except Exception:  # noqa: BLE001 - any failure means not ready yet
            time.sleep(interval)
    return False


def install_standalone(host, state: dict, api, messages: list[str], force: bool = False) -> dict:
    coordinator_state = dict(state)
    coordinator_state.setdefault("api_url", "http://127.0.0.1:8000/api/v1")
    coordinator_state.setdefault("redis_bind", "127.0.0.1")
    coordinator_state.setdefault("redis_advertise", "127.0.0.1")
    result = install_coordinator(host, coordinator_state, messages, force=force)
    if result["code"] != EXIT_OK:
        return result
    env = parse_env(host.read(linux_paths()["coordinator_env"]))
    if not _wait_for_coordinator(api, coordinator_state["api_url"]):
        messages.append("coordinator not ready; rerun install")
        return {"code": EXIT_FAILED, "changed": False}
    node_state = {
        "node_id": state.get("node_id") or "local",
        "coordinator_url": coordinator_state["api_url"],
        "coordinator_id": env["COORDINATOR_ID"],
        "node_api_key": "",
        "operator_api_key": env["API_KEY_OPERATOR"],
        "queues": list(state.get("queues") or ["prep"]),
        "working_hours": state.get("working_hours") or {"enabled": False, "start": "00:00", "end": "23:59", "timezone": "UTC"},
        "redis": {
            "host": "127.0.0.1",
            "port": 6379,
            "password": env["REDIS_PASSWORD"],
        },
        "probe_image": state.get("probe_image") or "audiobook-probe:dev",
        "llm": state.get("llm") if isinstance(state.get("llm"), dict) else {},
    }
    invited = api.invite(
        node_state["coordinator_url"],
        env["API_KEY_OPERATOR"],
        {
            "node_id": node_state["node_id"],
            "allowed_queues": node_state["queues"],
            "working_hours": node_state["working_hours"],
        },
    )
    if invited.get("already_exists"):
        # Node already exists — read the existing key from the node's env file.
        node_env = parse_env(host.read(linux_paths()["node_env"]))
        node_state["node_api_key"] = node_env.get("API_KEY", "")
        if not node_state["node_api_key"]:
            messages.append("node already exists but no API key found in env file")
            return {"code": EXIT_FAILED, "changed": False}
        messages.append(f"node '{node_state['node_id']}' already exists, reusing existing key")
    else:
        node_state["node_api_key"] = invited["node_api_key"]
    # Ensure working_hours and allowed_queues match the state file even if the
    # node row already existed (e.g. coordinator reinstalled, node row persisted).
    api.patch_node(
        node_state["coordinator_url"],
        env["API_KEY_OPERATOR"],
        node_state["node_id"],
        {
            "allowed_queues": node_state["queues"],
            "working_hours": node_state["working_hours"],
        },
    )
    node_result = install_node(host, node_state, api, messages, force=force)
    node_result["invited"] = True
    return node_result


def invite_node(api, coordinator_url: str, operator_key: str, node_id: str, queues: list[str], redis_host: str, redis_port: int, redis_password: str = "") -> dict:
    validate_api_url(coordinator_url)
    if not _wait_for_coordinator(api, coordinator_url):
        raise RuntimeError("coordinator not ready; rerun install")
    health = api.health(coordinator_url)
    created = api.invite(
        coordinator_url,
        operator_key,
        {"node_id": node_id, "allowed_queues": queues, "working_hours": {"enabled": False, "start": "00:00", "end": "23:59", "timezone": "UTC"}},
    )
    return {
        "node_api_key": created["node_api_key"],
        "coordinator_id": health.get("coordinator_id") or created.get("coordinator_id"),
        "coordinator_url": coordinator_url,
        "redis": {"host": redis_host, "port": redis_port, "password": redis_password},
    }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


#: Weight files travel as pinned HF downloads, never through the prefix copy.
#: Everything else ships with the tree — including binary voice assets
#: (base.wav / voices/*.wav) the recipe image bakes in (#91).
_WEIGHT_FILE_SUFFIXES = {".pt", ".pth", ".safetensors", ".bin", ".gguf", ".onnx"}


def _copy_tree(host, source: Path, dest_root: str) -> None:
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or "tests" in path.parts or path.suffix == ".pyc":
            continue
        if path.suffix in _WEIGHT_FILE_SUFFIXES:
            continue
        rel = dest_root + "/" + path.relative_to(source).as_posix()
        try:
            text = path.read_text()
        except (UnicodeDecodeError, ValueError):
            # Binary asset: copy as bytes, never drop it (#91) — the decode
            # failure is the signal to use the bytes path, not a skip reason.
            host.write_bytes(rel, path.read_bytes())
            continue
        host.write(rel, text)


def _install_coordinator_files(host, prefix: str, state: dict) -> None:
    root = _repo_root()
    compose = (root / "deploy" / "docker-compose.dev.yml").read_text()
    compose = compose.replace("context: ..", "context: .").replace(
        "dockerfile: deploy/coordinator.Dockerfile",
        "dockerfile: coordinator.Dockerfile",
    )
    bind = state.get("redis_bind") or "127.0.0.1"
    compose = compose.replace('      - "127.0.0.1:6379:6379"', f'      - "{bind}:6379:6379"')
    host.write(prefix + "/docker-compose.dev.yml", compose)
    host.write(prefix + "/coordinator.Dockerfile", (root / "deploy" / "coordinator.Dockerfile").read_text())
    _copy_tree(host, root / "coordinator", prefix + "/coordinator")
    host.write(prefix + "/config/coordinator.yaml", (root / "config" / "coordinator.yaml").read_text())


def _write_node_build_files(host, root: Path, prefix: str) -> None:
    """Copy the repo deploy/ build inputs into the node prefix, verbatim.

    One definition of which build files the node prefix needs — used by both
    install (_install_supervisor_files) and update (_update_node).
    """
    for name in (
        "supervisor.Dockerfile",
        "worker-prep.Dockerfile",
        "worker-tts.Dockerfile",
        "recipe-michael-cosyvoice.Dockerfile",
        "recipe-ggardner-indextts.Dockerfile",
        "probe.Dockerfile",
        "build-images.sh",
    ):
        host.write(prefix + "/" + name, (root / "deploy" / name).read_text())


def _install_supervisor_files(host, prefix: str, state: dict | None = None) -> None:
    root = _repo_root()
    _write_node_build_files(host, root, prefix)
    host.write(prefix + "/record-hardware.sh", RECORD_HARDWARE)
    _copy_tree(host, root / "supervisor", prefix + "/supervisor")
    _copy_tree(host, root / "worker", prefix + "/worker")
    llm = resolve_llm_config(state)
    coordinator_url = (state or {}).get("coordinator_url") or ""
    redis_cfg = (state or {}).get("redis") or {}
    if isinstance(redis_cfg, str):
        redis_cfg = {"host": redis_cfg}
    redis_host = str(redis_cfg.get("host") or "") if isinstance(redis_cfg, dict) else ""
    supervisor_yaml = (root / "config" / "supervisor.yaml").read_text()
    prep_yaml = (root / "config" / "worker-prep.yaml").read_text()
    tts_yaml = (root / "config" / "worker-tts.yaml").read_text()
    supervisor_yaml = apply_endpoints_to_config_yaml(supervisor_yaml, coordinator_url, redis_host)
    prep_yaml = apply_endpoints_to_config_yaml(prep_yaml, coordinator_url, redis_host)
    tts_yaml = apply_endpoints_to_config_yaml(tts_yaml, coordinator_url, redis_host)
    host.write(prefix + "/config/supervisor.yaml", apply_llm_to_config_yaml(supervisor_yaml, llm))
    host.write(
        prefix + "/config/worker-prep.yaml",
        apply_llm_to_config_yaml(prep_yaml, llm, include_required=True),
    )
    host.write(prefix + "/config/worker-tts.yaml", apply_llm_to_config_yaml(tts_yaml, llm))
    _copy_tree(host, root / "recipe", prefix + "/recipe")


def _coordinator_unit(prefix: str) -> str:
    return (
        "[Service]\n"
        "Type=oneshot\n"
        "RemainAfterExit=yes\n"
        f"ExecStart=docker compose -f {prefix}/docker-compose.dev.yml up -d --build\n"
        f"ExecStop=docker compose -f {prefix}/docker-compose.dev.yml down\n"
        "EnvironmentFile=/etc/audiobook/coordinator.env\n"
    )


def _node_task() -> str:
    return (
        "docker run --name audiobook-supervisor --rm "
        "-v //./pipe/docker_engine://./pipe/docker_engine "
        "--env-file C:\\ProgramData\\audiobook\\node.env "
        "-e HARDWARE_FILE=C:\\ProgramData\\audiobook\\hardware.json "
        f"{SUPERVISOR_IMAGE}"
    )


def _node_unit(prefix: str) -> str:
    return (
        "[Service]\n"
        # ExecStartPre rebuilds every image; the TTS build alone installs
        # torch + CUDA wheels and runs minutes past systemd's 90s default,
        # which kills the start job mid-build (#3). Give real builds room;
        # cached restarts still return in seconds.
        "TimeoutStartSec=1800\n"
        f"ExecStartPre=/bin/sh {prefix}/record-hardware.sh\n"
        f"ExecStartPre=docker build -t {SUPERVISOR_IMAGE} -f {prefix}/supervisor.Dockerfile {prefix}\n"
        f"ExecStartPre=/bin/sh {prefix}/build-images.sh\n"
        "ExecStartPre=docker rm -f audiobook-supervisor\n"
        "ExecStart=docker run --name audiobook-supervisor --rm --network host "
        "-v /var/run/docker.sock:/var/run/docker.sock "
        "-v /var/lib/audiobook:/var/lib/audiobook "
        "--env-file /etc/audiobook/node.env "
        "-e HARDWARE_FILE=/etc/audiobook/hardware.json "
        "-v /etc/audiobook/hardware.json:/etc/audiobook/hardware.json:ro "
        f"{SUPERVISOR_IMAGE}\n"
        "EnvironmentFile=/etc/audiobook/node.env\n"
    )


RECORD_HARDWARE = """#!/bin/sh
out=/etc/audiobook/hardware.json
cpu=$(nproc 2>/dev/null || echo 1)
ram=$(awk '/MemTotal/ {printf "%.1f", $2/1024/1024}' /proc/meminfo 2>/dev/null || echo 1.0)
gpu=null
if command -v nvidia-smi >/dev/null 2>&1; then
  info=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits 2>/dev/null | head -n 1)
  if [ -n "$info" ]; then
    name=$(printf '%s' "$info" | cut -d, -f1 | sed 's/^ *//;s/ *$//;s/"//g')
    vram=$(printf '%s' "$info" | cut -d, -f2 | sed 's/^ *//;s/ *$//')
    gb=$(awk -v m="$vram" 'BEGIN {printf "%.1f", m/1024}')
    gpu=$(printf '{"model":"%s","vram_gb":%s}' "$name" "$gb")
  fi
fi
mkdir -p /etc/audiobook
printf '{"cpu":%s,"ram_gb":%s,"gpu":%s}\\n' "$cpu" "$ram" "$gpu" > "$out"
"""
