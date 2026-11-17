"""Tests for supervisor JIT worker start/stop."""

import httpx
import pytest

from supervisor.config import SupervisorSettings
from supervisor.coordinator_client import CoordinatorClient
from supervisor.docker_manager import DockerManager, ProbeRunner
from supervisor.loop import run_loop


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
        prep_image="audiobook-prep:latest",
        tts_image="audiobook-tts:latest",
        queues=["prep", "convert"],
        working_hours={"enabled": False, "start": "22:00", "end": "08:00", "timezone": "UTC"},
        heartbeat_interval_seconds=30,
        idle_timeout_seconds=300,
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
    def __init__(self, code=0):
        self.code = code
        self.running_containers: set[str] = set()
        self.runs = []
        self.stops = []

    def running(self, name: str) -> bool:
        return name in self.running_containers

    def run(self, name: str, image: str, env: dict, timeout: int) -> int:
        self.runs.append((name, image, env, timeout))
        self.running_containers.add(name)
        return self.code

    def start_worker(self, name: str, image: str, env: dict, gpu: bool) -> None:
        self.runs.append((name, image, env, None))
        self.running_containers.add(name)

    def stop_worker(self, name: str) -> None:
        self.stops.append(name)
        self.running_containers.discard(name)


def _fake_client():
    return CoordinatorClient(settings(), httpx.Client(transport=Scripted(lambda r: httpx.Response(200, json={}))))


def test_docker_manager_starts_prep_worker():
    runner = FakeRunner()
    client = _fake_client()
    docker = DockerManager(settings(), runner, client)

    docker.start_worker("prep")
    assert "audiobook-worker-prep" in runner.running_containers
    assert any(r[0] == "audiobook-worker-prep" and r[1] == "audiobook-prep:latest" for r in runner.runs)


def test_docker_manager_starts_tts_worker():
    runner = FakeRunner()
    client = _fake_client()
    docker = DockerManager(settings(), runner, client)

    docker.start_worker("tts")
    assert "audiobook-worker-tts" in runner.running_containers
    assert any(r[0] == "audiobook-worker-tts" and r[1] == "audiobook-tts:latest" for r in runner.runs)


def test_docker_manager_stops_worker():
    runner = FakeRunner()
    client = _fake_client()
    docker = DockerManager(settings(), runner, client)

    docker.start_worker("prep")
    assert "audiobook-worker-prep" in runner.running_containers

    docker.stop_worker("prep")
    assert "audiobook-worker-prep" not in runner.running_containers
    assert "audiobook-worker-prep" in runner.stops


def test_docker_manager_idempotent_start():
    runner = FakeRunner()
    client = _fake_client()
    docker = DockerManager(settings(), runner, client)

    docker.start_worker("prep")
    docker.start_worker("prep")
    prep_starts = [r for r in runner.runs if r[0] == "audiobook-worker-prep"]
    assert len(prep_starts) == 1


def test_loop_starts_worker_on_queue_depth():
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
                    json={
                        "command": "run",
                        "node_state": "commissioned",
                        "queue_depths": {"prep": 5, "convert": 0},
                    },
                )
            return httpx.Response(
                200,
                json={
                    "command": "run",
                    "node_state": "commissioned",
                    "queue_depths": {"prep": 0, "convert": 0},
                },
            )
        return httpx.Response(500)

    transport = Scripted(handler)
    client = CoordinatorClient(settings(), httpx.Client(transport=transport))
    runner = FakeRunner()
    docker = DockerManager(settings(), runner, client)
    ticks = {"n": 0}

    def stop():
        ticks["n"] += 1
        return ticks["n"] > 3

    run_loop(client, docker, sleep=lambda: None, stop=stop, hardware={"cpu": 2, "ram_gb": 4, "gpu": None})
    assert any(r[0] == "audiobook-worker-prep" for r in runner.runs)
