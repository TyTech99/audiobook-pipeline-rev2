import httpx
import pytest

from supervisor.config import SupervisorSettings
from supervisor.coordinator_client import CoordinatorClient, IdentityMismatch
from supervisor.docker_manager import DockerManager, ProbeRunner
from supervisor.loop import run_loop
from supervisor.probe_client import run_probe


def settings(**overrides) -> SupervisorSettings:
    base = dict(
        node_id="dev-prep",
        display_name="dev",
        coordinator_url="http://coordinator:8000/api/v1",
        api_key="node-key",
        coordinator_id="coord-1",
        ca_file="",
        redis_host="127.0.0.1",
        redis_port=6379,
        redis_password="pw",
        redis_db=0,
        s3_endpoint="",
        s3_bucket="",
        s3_access_key="",
        s3_secret_key="",
        probe_image="audiobook-probe:dev",
        prep_image="",
        tts_image="",
        queues=["prep"],
        working_hours={"enabled": False, "start": "22:00", "end": "08:00", "timezone": "UTC"},
        heartbeat_interval_seconds=30,
    )
    base.update(overrides)
    return SupervisorSettings(**base)


class Scripted(httpx.BaseTransport):
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path, request.headers.get("authorization")))
        return self.handler(request)


class FakeRunner(ProbeRunner):
    def __init__(self, code=0, already=None):
        self.code = code
        self.already = set(already or [])
        self.runs = []

    def running(self, name: str) -> bool:
        return name in self.already

    def run(self, name: str, image: str, env: dict, timeout: int) -> int:
        self.runs.append((name, image, env, timeout))
        self.already.add(name)
        return self.code


def test_identity_mismatch_does_not_send_the_key():
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"coordinator_id": "other"})

    transport = Scripted(handler)
    client = CoordinatorClient(settings(), httpx.Client(transport=transport))
    with pytest.raises(IdentityMismatch):
        client.register({"cpu": 2, "ram_gb": 4, "gpu": None})
    assert all(auth is None for _m, _p, auth in transport.calls)


def test_loop_starts_one_probe_and_skips_a_second():
    calls = {"n": 0}

    def handler(request: httpx.Request):
        if request.url.path == "/health":
            return httpx.Response(200, json={"coordinator_id": "coord-1"})
        if request.url.path.endswith("/register"):
            return httpx.Response(200, json={"state": "commissioned", "effective_queues": ["prep"]})
        if request.url.path.endswith("/heartbeat"):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(
                    200,
                    json={"command": "start_probe", "probe_id": "p1", "worker_type": "prep", "node_state": "commissioned"},
                )
            return httpx.Response(
                200,
                json={"command": "start_probe", "probe_id": "p1", "worker_type": "prep", "node_state": "commissioned"},
            )
        if request.url.path.endswith("/probes/p1"):
            return httpx.Response(200, json={"status": "failed"})
        return httpx.Response(500)

    transport = Scripted(handler)
    client = CoordinatorClient(settings(), httpx.Client(transport=transport))
    runner = FakeRunner(code=0)
    docker = DockerManager(settings(), runner, client)
    ticks = {"n": 0}

    def stop():
        ticks["n"] += 1
        return ticks["n"] > 3

    run_loop(client, docker, sleep=lambda: None, stop=stop, hardware={"cpu": 2, "ram_gb": 4, "gpu": None})
    assert len(runner.runs) == 1
    assert runner.runs[0][0] == "audiobook-probe-p1"


def test_failed_container_reports_probe():
    posted = []

    def handler(request: httpx.Request):
        if request.url.path == "/health":
            return httpx.Response(200, json={"coordinator_id": "coord-1"})
        posted.append(request.url.path)
        return httpx.Response(200, json={"status": "failed"})

    client = CoordinatorClient(settings(), httpx.Client(transport=Scripted(handler)))
    runner = FakeRunner(code=1)
    DockerManager(settings(), runner, client).start_probe("p9", "prep")
    assert posted[-1].endswith("/probes/p9")


def test_drain_does_not_start_a_container():
    def handler(request: httpx.Request):
        if request.url.path == "/health":
            return httpx.Response(200, json={"coordinator_id": "coord-1"})
        if request.url.path.endswith("/register"):
            return httpx.Response(200, json={"state": "draining"})
        return httpx.Response(200, json={"command": "drain", "node_state": "draining"})

    client = CoordinatorClient(settings(), httpx.Client(transport=Scripted(handler)))
    runner = FakeRunner()
    docker = DockerManager(settings(), runner, client)
    ticks = {"n": 0}

    def stop():
        ticks["n"] += 1
        return ticks["n"] > 2

    run_loop(client, docker, sleep=lambda: None, stop=stop, hardware={"cpu": 1, "ram_gb": 1, "gpu": None})
    assert runner.runs == []


def test_probe_client_refuses_wrong_coordinator():
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"coordinator_id": "nope"})

    code = run_probe(
        {
            "COORDINATOR_URL": "http://coordinator:8000/api/v1",
            "API_KEY": "k",
            "COORDINATOR_ID": "coord-1",
            "WORKER_ID": "w",
            "NODE_ID": "n",
            "WORKER_TYPE": "prep",
            "PROBE_ID": "p",
        },
        httpx.Client(transport=Scripted(handler)),
    )
    assert code == 2


def test_hardware_without_nvml_has_null_gpu():
    from supervisor.hardware import detect_hardware

    info = detect_hardware()
    assert info["gpu"] is None
    assert info["cpu"] >= 1
