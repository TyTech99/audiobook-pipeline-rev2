from pathlib import Path

import pytest

from cli.flows import (
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


def test_update_keeps_env_and_copies_marker(tmp_path: Path):
    host = MemoryHost()
    install_coordinator(host, coordinator_state(), [])
    before = host.read(linux_paths()["coordinator_env"])
    (tmp_path / "MARKER").write_text("build-7")
    result = update_role(host, str(tmp_path), "coordinator", [])
    assert result["code"] == EXIT_OK
    assert host.read(linux_paths()["coordinator_prefix"] + "/MARKER") == "build-7"
    assert host.read(linux_paths()["coordinator_env"]) == before
    assert host.restarts[-1] == "audiobook-coordinator.service"


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
    state.update({"node_id": "local", "queues": ["prep"], "probe_image": "audiobook-probe:dev"})
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
    assert ("health", "http://127.0.0.1:8000/api/v1") in api.calls
    assert ("invite", "local") in api.calls


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
