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
        llm_base_url="",
        llm_api_key="",
        llm_model="",
        llm_timeout_seconds=120,
        llm_slots=0,
        probe_image="audiobook-probe:dev",
        prep_image="",
        tts_image="",
        recipe_image="",
        queues=["prep"],
        working_hours={"enabled": False, "start": "22:00", "end": "08:00", "timezone": "UTC"},
        heartbeat_interval_seconds=30,
        idle_timeout_seconds=300,
        log_level="INFO",
        log_file="",
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


# --- #41: running workers are recreated when the image tag moved on ---

class ImageAwareRunner(FakeRunner):
    """FakeRunner plus image-ID resolution for the #41 drift tests."""

    def __init__(self, running_image, expected_image, **kwargs):
        super().__init__(**kwargs)
        self.running_image = running_image
        self.expected_image = expected_image
        self.stopped = []
        self.started = []

    def worker_image_id(self, name: str) -> str | None:
        return self.running_image

    def image_id(self, tag: str) -> str | None:
        return self.expected_image

    def start_worker(self, name, image, env, gpu, volumes, extra_devices):
        self.started.append((name, image))
        self.already.add(name)
        # A freshly created container runs the current image.
        self.running_image = self.expected_image

    def stop_worker(self, name):
        self.stopped.append(name)
        self.already.discard(name)


def _worker_manager(runner):
    import types
    from typing import cast

    client = cast(
        CoordinatorClient,
        types.SimpleNamespace(fail_probe=lambda *_a, **_k: None),
    )
    return DockerManager(
        settings(prep_image="audiobook-worker-prep:local"), runner, client,
    )


def test_running_worker_with_current_image_is_not_recreated():
    runner = ImageAwareRunner(
        running_image="sha256:cur", expected_image="sha256:cur",
        already=["audiobook-worker-prep"],
    )
    _worker_manager(runner).start_worker("prep")
    assert runner.stopped == []
    assert runner.started == []


def test_running_worker_with_stale_image_is_recreated():
    runner = ImageAwareRunner(
        running_image="sha256:old", expected_image="sha256:new",
        already=["audiobook-worker-prep"],
    )
    manager = _worker_manager(runner)
    manager.start_worker("prep")
    assert runner.stopped == ["audiobook-worker-prep"]
    assert runner.started == [("audiobook-worker-prep", "audiobook-worker-prep:local")]
    # Bookkeeping reset so the idle timer starts fresh.
    assert "audiobook-worker-prep" in manager.worker_started_at


def test_unknown_worker_image_id_does_not_recreate():
    runner = ImageAwareRunner(
        running_image=None, expected_image="sha256:new",
        already=["audiobook-worker-prep"],
    )
    _worker_manager(runner).start_worker("prep")
    assert runner.stopped == []
    assert runner.started == []


def test_stopped_worker_starts_fresh_from_current_image():
    runner = ImageAwareRunner(
        running_image="sha256:old", expected_image="sha256:new", already=[],
    )
    _worker_manager(runner).start_worker("prep")
    assert runner.stopped == []
    assert runner.started == [("audiobook-worker-prep", "audiobook-worker-prep:local")]


def test_heartbeat_recreates_stale_worker_when_queue_quiet():
    # A running worker claims new jobs faster than the heartbeat can react,
    # so the drift check also runs on every heartbeat cycle (#41).
    runner = ImageAwareRunner(
        running_image="sha256:old", expected_image="sha256:new",
        already=["audiobook-worker-prep"],
    )
    manager = _worker_manager(runner)
    manager.check_worker_images({"prep": 0, "prep_processing": 0})
    assert runner.stopped == ["audiobook-worker-prep"]
    assert runner.started == [("audiobook-worker-prep", "audiobook-worker-prep:local")]
    assert "audiobook-worker-prep" in manager.worker_started_at


def test_heartbeat_never_recreates_when_jobs_in_flight():
    runner = ImageAwareRunner(
        running_image="sha256:old", expected_image="sha256:new",
        already=["audiobook-worker-prep"],
    )
    manager = _worker_manager(runner)
    manager.check_worker_images({"prep": 1, "prep_processing": 0})
    manager.check_worker_images({"prep": 0, "prep_processing": 1})
    assert runner.stopped == []
    assert runner.started == []


def test_heartbeat_ignores_missing_and_fresh_workers():
    missing = ImageAwareRunner(running_image=None, expected_image="sha256:new", already=[])
    _worker_manager(missing).check_worker_images({})
    fresh = ImageAwareRunner(
        running_image="sha256:cur", expected_image="sha256:cur",
        already=["audiobook-worker-prep"],
    )
    _worker_manager(fresh).check_worker_images({"prep": 0, "prep_processing": 0})
    assert missing.stopped == [] and missing.started == []
    assert fresh.stopped == [] and fresh.started == []
