import subprocess
from pathlib import Path

import pytest
import yaml

import cli.flows as flows
from cli.flows import (
    EXIT_FAILED,
    EXIT_OK,
    EXIT_PRECONDITION,
    EXIT_REBOOT,
    _node_task,
    apply_node,
    coordinator_packages,
    install_coordinator,
    install_node,
    install_standalone,
    invite_node,
    node_packages,
    uninstall_coordinator,
    uninstall_node,
    update_role,
)
from cli.hosts import CommandRunner, linux_paths
from cli.policy import PolicyError, validate_api_url, validate_redis_advertise
from cli.state import parse_env
from cli.hosts import MemoryHost


class FakeApi:
    def __init__(self, coordinator_id="coord-fixed", health_id=None):
        self.coordinator_id = coordinator_id
        self.health_id = coordinator_id if health_id is None else health_id
        self.node_key = "node-secret"
        self.calls = []

    def health(self, url):
        self.calls.append(("health", url))
        return {"coordinator_id": self.health_id}

    def invite(self, url, operator_key, body):
        self.calls.append(("invite", body["node_id"]))
        return {"node_api_key": self.node_key, "coordinator_id": self.coordinator_id, "node_id": body["node_id"]}

    def patch_node(self, url, operator_key, node_id, body):
        self.calls.append(("patch_node", node_id, body))
        return {"node_id": node_id, **body}

    def drain(self, url, operator_key, node_id):
        self.calls.append(("drain", node_id))

    def delete(self, url, operator_key, node_id):
        self.calls.append(("delete", node_id))


def coordinator_state():
    return {
        "api_url": "http://127.0.0.1:8000/api/v1",
        "redis_bind": "127.0.0.1",
        "redis_advertise": "127.0.0.1",
        "coordinator_id": "coord-fixed",
        "operator_api_key": "",
        "s3_endpoint": "",
    }


def node_state(**overrides):
    base = {
        "node_id": "prep-1",
        "coordinator_url": "http://10.0.0.5:8000/api/v1",
        "coordinator_id": "coord-fixed",
        "node_api_key": "node-secret",
        "operator_api_key": "operator-secret",
        "queues": ["prep"],
        "working_hours": {"enabled": True, "start": "22:00", "end": "08:00", "timezone": "UTC"},
        "redis": {"host": "10.0.0.5", "port": 6379, "password": "redis-secret"},
        "probe_image": "audiobook-probe:dev",
    }
    base.update(overrides)
    return base


def test_coordinator_install_hides_the_operator_key_and_is_stable():
    host = MemoryHost()
    messages = []
    first = install_coordinator(host, coordinator_state(), messages)
    assert first["code"] == EXIT_OK and first["changed"] is True
    env = host.read(linux_paths()["coordinator_env"])
    assert "API_KEY_OPERATOR=" in env
    secret = parse_env(env)["API_KEY_OPERATOR"]
    assert secret not in " ".join(messages)
    assert linux_paths()["coordinator_unit"] in host.units
    assert host.packages == ["docker-ce"]
    unit = host.units[linux_paths()["coordinator_unit"]]
    assert "docker compose -f /opt/audiobook/coordinator/docker-compose.dev.yml up -d" in unit
    assert "coordinator.main" not in unit
    assert "venv" not in unit
    installed = host.read(linux_paths()["coordinator_prefix"] + "/docker-compose.dev.yml")
    assert "image: redis:7" in installed
    assert "image: postgres:16" in installed
    assert "dockerfile: coordinator.Dockerfile" in installed
    acme = coordinator_state()
    acme["tls"] = {"mode": "acme"}
    assert "certbot" not in host.packages
    again = install_coordinator(host, coordinator_state(), [])
    assert again["changed"] is False
    assert parse_env(host.read(linux_paths()["coordinator_env"]))["API_KEY_OPERATOR"] == secret


def test_node_apply_restarts_only_when_hours_change():
    host = MemoryHost(ips=["10.0.0.5"])
    api = FakeApi()
    assert apply_node(host, node_state(), api, [])["changed"] is True
    assert host.restarts == []
    same = apply_node(host, node_state(), api, [])
    assert same["changed"] is False
    assert host.restarts == []
    changed = node_state(working_hours={"enabled": True, "start": "21:00", "end": "07:00", "timezone": "UTC"})
    assert apply_node(host, changed, api, [])["changed"] is True
    assert host.restarts == ["audiobook-supervisor.service"]


def _update_source_tree(tmp_path: Path) -> Path:
    """A minimal repo-like checkout for update_role tests."""
    (tmp_path / "cli").mkdir()
    (tmp_path / "cli" / "flows.py").write_text("# cli\n")
    (tmp_path / "coordinator").mkdir()
    (tmp_path / "coordinator" / "main.py").write_text("MAIN = 'new'\n")
    (tmp_path / "supervisor").mkdir()
    (tmp_path / "supervisor" / "__init__.py").write_text("")
    (tmp_path / "supervisor" / "supervisor.py").write_text("SUPERVISOR = 'new'\n")
    (tmp_path / "worker").mkdir()
    (tmp_path / "worker" / "__init__.py").write_text("")
    (tmp_path / "worker" / "prep.py").write_text("PREP = 'new'\n")
    (tmp_path / "recipe").mkdir()
    (tmp_path / "recipe" / "voice.txt").write_text("voice\n")
    (tmp_path / "deploy").mkdir()
    for name in (
        "coordinator.Dockerfile",
        "supervisor.Dockerfile",
        "worker-prep.Dockerfile",
        "worker-tts.Dockerfile",
        "recipe-michael-cosyvoice.Dockerfile",
        "probe.Dockerfile",
        "build-images.sh",
    ):
        (tmp_path / "deploy" / name).write_text(f"# {name}\n")
    return tmp_path


def _fake_docker_run(calls: list, *, container_image: str, built_image: str):
    """Record deploy commands and answer the docker inspect/images queries."""

    def run(argv):
        calls.append(argv)
        if "inspect" in argv and "--format" in argv:
            image = container_image if argv[1] == "inspect" else built_image
            return subprocess.CompletedProcess(argv, 0, stdout=image + "\n", stderr="")
        if "ps" in argv and "-q" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout="container-id\n", stderr="")
        if "images" in argv and "-q" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout=built_image + "\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    return run


def test_update_coordinator_deploys_the_nested_package_and_verifies(monkeypatch, tmp_path):
    host = MemoryHost()
    install_coordinator(host, coordinator_state(), [])
    prefix = linux_paths()["coordinator_prefix"]
    compose = prefix + "/docker-compose.dev.yml"
    # Flat-rsync residue from the #53 incident: a stale copy at the top level.
    host.write(prefix + "/main.py", "MAIN = 'stale'\n")
    host.write(prefix + "/config/coordinator.yaml", "operator: edited\n")
    before_env = host.read(linux_paths()["coordinator_env"])
    before_compose = host.read(compose)
    monkeypatch.setattr(flows, "_git_revision", lambda cwd: "rev" + "a" * 40)
    calls: list = []
    monkeypatch.setattr(
        flows, "_run",
        _fake_docker_run(calls, container_image="sha256:" + "a" * 64, built_image="a" * 64),
    )
    messages = []
    result = update_role(host, str(_update_source_tree(tmp_path)), "coordinator", messages)
    assert result["code"] == EXIT_OK and result["changed"] is True
    # The package landed in the nested dir the build context reads; the flat
    # residue is untouched (its cleanup is a separate decision).
    assert host.read(prefix + "/coordinator/main.py") == "MAIN = 'new'\n"
    assert host.read(prefix + "/main.py") == "MAIN = 'stale'\n"
    assert host.read(prefix + "/REVISION") == "rev" + "a" * 40
    assert host.read(prefix + "/coordinator.Dockerfile") == "# coordinator.Dockerfile\n"
    # Runtime state owned by install/apply is untouched.
    assert host.read(linux_paths()["coordinator_env"]) == before_env
    assert host.read(compose) == before_compose
    assert host.read(prefix + "/config/coordinator.yaml") == "operator: edited\n"
    assert prefix + "/MARKER" not in host.files
    assert any("coordinator updated" in line for line in messages)
    # Build, force-recreate, then image-verify — in that order (#53).
    build_cmd = ["docker", "compose", "-f", compose, "build", "coordinator"]
    up_cmd = ["docker", "compose", "-f", compose, "up", "-d", "--force-recreate", "coordinator"]
    assert build_cmd in calls
    assert up_cmd in calls
    assert calls.index(up_cmd) > calls.index(build_cmd)
    assert any("inspect" in argv and argv[1] == "inspect" for argv in calls)


def test_update_coordinator_fails_when_the_image_does_not_match(monkeypatch, tmp_path):
    host = MemoryHost()
    install_coordinator(host, coordinator_state(), [])
    before_revision = host.read(linux_paths()["coordinator_prefix"] + "/REVISION")
    monkeypatch.setattr(flows, "_git_revision", lambda cwd: "rev" + "a" * 40)
    calls: list = []
    monkeypatch.setattr(
        flows, "_run",
        _fake_docker_run(calls, container_image="sha256:" + "b" * 64, built_image="a" * 64),
    )
    messages = []
    result = update_role(host, str(_update_source_tree(tmp_path)), "coordinator", messages)
    assert result["code"] == EXIT_FAILED and result["changed"] is True
    assert any("does not match" in line for line in messages)
    # The package synced, but REVISION still records the deployed (old) code.
    assert host.read(linux_paths()["coordinator_prefix"] + "/coordinator/main.py") == "MAIN = 'new'\n"
    assert host.read(linux_paths()["coordinator_prefix"] + "/REVISION") == before_revision


def test_update_node_syncs_restarts_and_keeps_operator_config(monkeypatch, tmp_path):
    host = MemoryHost(ips=["10.0.0.5"])
    install_node(host, node_state(), FakeApi(), [])
    prefix = linux_paths()["node_prefix"]
    unit = linux_paths()["node_unit"]
    # Operator-edited runtime config that update must never clobber.
    host.write(prefix + "/config/supervisor.yaml", "llm:\n  base_url: http://127.0.0.1:8081/v1\n")
    before_env = host.read(linux_paths()["node_env"])
    monkeypatch.setattr(flows, "_git_revision", lambda cwd: "rev" + "a" * 40)
    calls: list = []
    monkeypatch.setattr(
        flows, "_run",
        _fake_docker_run(calls, container_image="sha256:" + "a" * 64, built_image="a" * 64),
    )
    messages = []
    result = update_role(host, str(_update_source_tree(tmp_path)), "node", messages)
    assert result["code"] == EXIT_OK and result["changed"] is True
    # supervisor/ nested; worker/ and recipe/ flat (#53).
    assert host.read(prefix + "/supervisor/supervisor.py") == "SUPERVISOR = 'new'\n"
    assert host.read(prefix + "/worker/prep.py") == "PREP = 'new'\n"
    assert host.read(prefix + "/recipe/voice.txt") == "voice\n"
    for name in (
        "supervisor.Dockerfile",
        "worker-prep.Dockerfile",
        "worker-tts.Dockerfile",
        "recipe-michael-cosyvoice.Dockerfile",
        "probe.Dockerfile",
        "build-images.sh",
    ):
        assert host.read(prefix + "/" + name) == f"# {name}\n"
    assert host.read(prefix + "/REVISION") == "rev" + "a" * 40
    assert host.read(linux_paths()["node_env"]) == before_env
    supervisor_yaml = host.read(prefix + "/config/supervisor.yaml")
    assert supervisor_yaml is not None and "8081" in supervisor_yaml
    assert host.restarts == [unit]
    assert any("node updated" in line for line in messages)
    assert any("heartbeat" in line for line in messages)


def test_update_node_reports_a_unit_restart_failure(monkeypatch, tmp_path):
    class RestartFails(MemoryHost):
        def restart(self, name):
            raise RuntimeError("start-pre operation timed out")

    host = RestartFails(ips=["10.0.0.5"])
    install_node(host, node_state(), FakeApi(), [])
    before_revision = host.read(linux_paths()["node_prefix"] + "/REVISION")
    monkeypatch.setattr(flows, "_git_revision", lambda cwd: "rev" + "a" * 40)
    calls: list = []
    monkeypatch.setattr(
        flows, "_run",
        _fake_docker_run(calls, container_image="sha256:" + "a" * 64, built_image="a" * 64),
    )
    messages = []
    result = update_role(host, str(_update_source_tree(tmp_path)), "node", messages)
    assert result["code"] == EXIT_FAILED
    assert any("timed out" in line for line in messages)
    # Files were synced before the restart failed, so a rerun is safe — but
    # REVISION still records the deployed (old) code.
    assert host.read(linux_paths()["node_prefix"] + "/supervisor/supervisor.py") == "SUPERVISOR = 'new'\n"
    assert host.read(linux_paths()["node_prefix"] + "/REVISION") == before_revision


def test_update_node_retries_the_container_inspect(monkeypatch, tmp_path):
    host = MemoryHost(ips=["10.0.0.5"])
    install_node(host, node_state(), FakeApi(), [])
    monkeypatch.setattr(flows, "_git_revision", lambda cwd: "rev" + "a" * 40)
    state = {"inspects": 0}

    def run(argv):
        if "inspect" in argv and argv[1] == "inspect":
            state["inspects"] += 1
            if state["inspects"] == 1:
                return subprocess.CompletedProcess(argv, 1, stdout="", stderr="No such object")
            return subprocess.CompletedProcess(argv, 0, stdout="sha256:" + "a" * 64 + "\n", stderr="")
        if "inspect" in argv and argv[1] == "image":
            return subprocess.CompletedProcess(argv, 0, stdout="a" * 64 + "\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(flows, "_run", run)
    result = update_role(host, str(_update_source_tree(tmp_path)), "node", [])
    assert result["code"] == EXIT_OK
    assert state["inspects"] >= 2
    assert host.read(linux_paths()["node_prefix"] + "/REVISION") == "rev" + "a" * 40


def test_update_node_fails_when_the_container_never_appears(monkeypatch, tmp_path):
    host = MemoryHost(ips=["10.0.0.5"])
    install_node(host, node_state(), FakeApi(), [])
    monkeypatch.setattr(flows, "_git_revision", lambda cwd: "rev" + "a" * 40)
    monkeypatch.setattr(flows, "_VERIFY_WAIT_SECONDS", 0.0)

    def run(argv):
        if "inspect" in argv and argv[1] == "inspect":
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="No such object")
        if "inspect" in argv and argv[1] == "image":
            return subprocess.CompletedProcess(argv, 0, stdout="a" * 64 + "\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(flows, "_run", run)
    messages = []
    result = update_role(host, str(_update_source_tree(tmp_path)), "node", messages)
    assert result["code"] == EXIT_FAILED
    assert any("missing after restart" in line for line in messages)


def test_update_refuses_windows_non_root_bad_source_and_uninstalled(tmp_path):
    source = str(_update_source_tree(tmp_path))
    windows = MemoryHost(os_name="windows")
    assert update_role(windows, source, "coordinator", [])["code"] == EXIT_PRECONDITION
    non_root = MemoryHost(root=False)
    assert update_role(non_root, source, "node", [])["code"] == EXIT_PRECONDITION

    fresh = MemoryHost()
    assert update_role(fresh, source, "coordinator", [])["code"] == EXIT_PRECONDITION
    assert update_role(fresh, source, "node", [])["code"] == EXIT_PRECONDITION
    assert fresh.files == {}

    missing = tmp_path / "not-a-repo"
    missing.mkdir()
    installed = MemoryHost()
    install_coordinator(installed, coordinator_state(), [])
    before = dict(installed.files)
    result = update_role(installed, str(missing), "coordinator", [])
    assert result["code"] == EXIT_PRECONDITION
    assert installed.files == before


def test_uninstall_purge_and_local_only():
    host = MemoryHost(ips=["10.0.0.5"])
    api = FakeApi()
    install_node(host, node_state(), api, [])
    env = linux_paths()["node_env"]
    uninstall_node(host, node_state(), api, purge=False, local_only=False, messages=[])
    assert env in host.files
    assert "audiobook-supervisor.service" not in host.units
    assert ("drain", "prep-1") in api.calls
    host = MemoryHost(ips=["10.0.0.5"])
    api = FakeApi()
    install_node(host, node_state(), api, [])
    uninstall_node(host, node_state(), api, purge=True, local_only=True, messages=["keep"])
    assert env not in host.files
    assert not any(call[0] == "drain" for call in api.calls)


def test_windows_coordinator_is_refused_and_node_reboot_skips_the_task():
    assert install_coordinator(MemoryHost(os_name="windows"), coordinator_state(), [])["code"] == EXIT_PRECONDITION
    host = MemoryHost(os_name="windows", docker_ready=False, reboot_pending=True, gpu=False)
    result = install_node(host, node_state(queues=["prep"], coordinator_url="http://10.0.0.5:8000/api/v1"), FakeApi(), [])
    assert result["code"] == EXIT_REBOOT
    assert "Docker.DockerDesktop" in host.packages
    assert host.tasks == {}
    assert host.units == {}


def test_windows_node_task_and_prep_without_gpu():
    host = MemoryHost(os_name="windows", gpu=False, docker_ready=True)
    result = install_node(host, node_state(queues=["prep"]), FakeApi(), [])
    assert result["code"] == EXIT_OK
    assert "AudiobookSupervisor" in host.tasks
    assert host.tasks["AudiobookSupervisor"] == _node_task()
    assert "python.exe -m supervisor" not in host.tasks["AudiobookSupervisor"]
    assert "Python.Python.3.11" not in host.packages
    assert host.units == {}
    linux = MemoryHost(gpu=False, ips=["10.0.0.5"])
    messages = []
    refused = install_node(linux, node_state(queues=["convert"]), FakeApi(), messages)
    assert refused["code"] == EXIT_PRECONDITION
    assert "nvidia-driver" not in linux.packages
    assert any("driver" in line for line in messages)
    prep = MemoryHost(gpu=False, ips=["10.0.0.5"])
    assert install_node(prep, node_state(queues=["prep"]), FakeApi(), [])["code"] == EXIT_OK
    assert "nvidia-container-toolkit" not in prep.packages
    assert prep.packages == ["docker-ce"]
    node_unit = prep.units["audiobook-supervisor.service"]
    assert "/var/run/docker.sock" in node_unit
    assert "TimeoutStartSec=1800" in node_unit
    assert "venv" not in node_unit
    assert "--gpus" not in node_unit
    assert "python3" not in prep.packages


def test_non_root_writes_nothing():
    host = MemoryHost(root=False)
    assert install_coordinator(host, coordinator_state(), [])["code"] == EXIT_PRECONDITION
    assert host.files == {}


def test_node_install_refuses_wrong_coordinator_id():
    host = MemoryHost(ips=["10.0.0.5"])
    api = FakeApi(health_id="other")
    result = install_node(host, node_state(), api, [])
    assert result["code"] == EXIT_PRECONDITION
    assert api.calls == [("health", "http://10.0.0.5:8000/api/v1")]
    assert host.units == {}


def test_url_policy():
    with pytest.raises(PolicyError):
        validate_api_url("http://coordinator.example:8000/api/v1")
    with pytest.raises(PolicyError):
        validate_redis_advertise("coordinator.example", "https://coordinator.example:8000/api/v1")
    validate_api_url("http://10.0.0.5:8000/api/v1")


def test_invite_payload():
    api = FakeApi()
    payload = invite_node(api, "http://10.0.0.5:8000/api/v1", "operator-secret", "gpu-study", ["convert"], "10.0.0.5", 6379, "redis-secret")
    assert payload["coordinator_id"] == "coord-fixed"
    assert payload["coordinator_url"].startswith("http://10.0.0.5")
    assert payload["redis"] == {"host": "10.0.0.5", "port": 6379, "password": "redis-secret"}
    assert payload["node_api_key"] == "node-secret"


def test_coordinator_install_binds_redis_to_the_state_file():
    host = MemoryHost()
    state = coordinator_state()
    state["redis_bind"] = "10.0.0.5"
    state["redis_advertise"] = "10.0.0.5"
    install_coordinator(host, state, [])
    compose = host.read(linux_paths()["coordinator_prefix"] + "/docker-compose.dev.yml")
    assert '"10.0.0.5:6379:6379"' in compose
    assert '"127.0.0.1:6379:6379"' not in compose


def test_standalone_invites_a_local_node_and_checks_its_key():
    host = MemoryHost(gpu=False)
    api = FakeApi()
    state = coordinator_state()
    state.update({
        "node_id": "local",
        "queues": ["prep"],
        "probe_image": "audiobook-probe:dev",
        "llm": {
            "base_url": "http://127.0.0.1:8080/v1",
            "api_key": "llm-key",
            "model": "qwen-local",
            "timeout_seconds": 90,
            "slots": 4,
        },
    })
    result = install_standalone(host, state, api, [])
    assert result["code"] == EXIT_OK
    assert "audiobook-coordinator.service" in host.units
    assert "audiobook-supervisor.service" in host.units
    node_env = parse_env(host.read(linux_paths()["node_env"]))
    coordinator_env = parse_env(host.read(linux_paths()["coordinator_env"]))
    assert node_env["COORDINATOR_URL"].startswith("http://127.0.0.1")
    assert node_env["REDIS_HOST"] == "127.0.0.1"
    assert node_env["API_KEY"] == "node-secret"
    assert node_env["API_KEY"] != coordinator_env["API_KEY_OPERATOR"]
    assert node_env["LLM_BASE_URL"] == "http://127.0.0.1:8080/v1"
    assert node_env["LLM_API_KEY"] == "llm-key"
    assert node_env["LLM_MODEL"] == "qwen-local"
    assert node_env["LLM_TIMEOUT_SECONDS"] == "90"
    assert node_env["LLM_SLOTS"] == "4"
    supervisor = yaml.safe_load(host.read(linux_paths()["node_prefix"] + "/config/supervisor.yaml"))
    assert supervisor["llm"]["base_url"] == "http://127.0.0.1:8080/v1"
    assert supervisor["llm"]["model"] == "qwen-local"
    assert supervisor["llm"]["api_key"] == "llm-key"
    assert ("health", "http://127.0.0.1:8000/api/v1") in api.calls
    # Standalone install must align working_hours and allowed_queues with the
    # state file even if the node row already existed.
    patch_calls = [c for c in api.calls if c[0] == "patch_node"]
    assert len(patch_calls) == 1
    _, patched_id, patched_body = patch_calls[0]
    assert patched_id == "local"
    assert patched_body["allowed_queues"] == ["prep"]
    assert patched_body["working_hours"] == {"enabled": False, "start": "00:00", "end": "23:59", "timezone": "UTC"}


def test_node_install_writes_llm_into_config_yaml():
    host = MemoryHost(ips=["10.0.0.5"])
    state = node_state(
        llm={
            "base_url": "http://10.0.0.5:8080/v1",
            "api_key": "secret-llm",
            "model": "local-model",
            "slots": 8,
            "timeout_seconds": 90,
        }
    )
    assert install_node(host, state, FakeApi(), [])["code"] == EXIT_OK
    prefix = linux_paths()["node_prefix"]
    supervisor = yaml.safe_load(host.read(prefix + "/config/supervisor.yaml"))
    prep = yaml.safe_load(host.read(prefix + "/config/worker-prep.yaml"))
    tts = yaml.safe_load(host.read(prefix + "/config/worker-tts.yaml"))
    for doc in (supervisor, prep, tts):
        assert doc["llm"]["base_url"] == "http://10.0.0.5:8080/v1"
        assert doc["llm"]["model"] == "local-model"
        assert doc["llm"]["api_key"] == "secret-llm"
        assert int(doc["llm"]["slots"]) == 8
        assert int(doc["llm"]["timeout_seconds"]) == 90
    assert prep["llm"]["required"] is True
    env = parse_env(host.read(linux_paths()["node_env"]))
    assert env["LLM_BASE_URL"] == "http://10.0.0.5:8080/v1"
    assert env["LLM_MODEL"] == "local-model"
    assert env["LLM_SLOTS"] == "8"


def test_residue_packages_are_docker_and_the_toolkit():
    assert coordinator_packages({}) == ["docker-ce"]
    assert coordinator_packages({"tls": {"mode": "acme"}}) == ["docker-ce"]
    prep_code, prep_packages = node_packages({"queues": ["prep"]}, MemoryHost(gpu=False), False)
    assert prep_code == EXIT_OK
    assert prep_packages == ["docker-ce"]
    convert_code, convert_packages = node_packages({"queues": ["convert"]}, MemoryHost(gpu=True), False)
    assert convert_code == EXIT_OK
    assert convert_packages == ["docker-ce", "nvidia-container-toolkit"]
    assert "python3" not in convert_packages
    ready_code, ready_packages = node_packages({"queues": ["prep"]}, MemoryHost(os_name="windows", docker_ready=True, gpu=False), True)
    assert ready_code == EXIT_OK
    assert ready_packages == []
    missing = MemoryHost(os_name="windows", docker_ready=False, gpu=False)
    missing_code, missing_packages = node_packages({"queues": ["prep"]}, missing, True)
    assert missing_code == EXIT_OK
    assert missing_packages == ["Docker.DockerDesktop"]


def test_linux_package_command_is_apt():
    seen = []
    runner = CommandRunner(run=lambda argv: seen.append(argv))
    runner.os_name = "linux"
    runner.install_packages(["redis-server", "postgresql"])
    assert seen == [["apt-get", "install", "-y", "redis-server", "postgresql"]]


def test_coordinator_uninstall_keeps_env_unless_purged():
    host = MemoryHost()
    install_coordinator(host, coordinator_state(), [])
    uninstall_coordinator(host, purge=False, messages=[])
    assert linux_paths()["coordinator_env"] in host.files
    assert "audiobook-coordinator.service" not in host.units
    uninstall_coordinator(host, purge=True, messages=[])
    assert linux_paths()["coordinator_env"] not in host.files
