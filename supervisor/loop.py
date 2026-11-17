from __future__ import annotations

import time

from supervisor.coordinator_client import CoordinatorClient, IdentityMismatch
from supervisor.docker_manager import DockerManager
from supervisor.hardware import detect_hardware


def run_loop(client: CoordinatorClient, docker: DockerManager, sleep, stop, hardware=None) -> None:
    """Register, then heartbeat until stop() is true. SIGTERM is the caller's stop flag."""
    try:
        client.check_identity()
    except IdentityMismatch:
        raise
    info = hardware if hardware is not None else detect_hardware()
    client.register(info)
    while not stop():
        body = client.heartbeat()
        command = body.get("command")
        if command == "start_probe":
            docker.start_probe(body["probe_id"], body["worker_type"])
        queue_depths = body.get("queue_depths") or {}
        for worker_type in ("prep", "tts"):
            queue = "prep" if worker_type == "prep" else "convert"
            if queue_depths.get(queue, 0) > 0:
                docker.start_worker(worker_type)
        docker.check_idle(queue_depths, time.time())
        sleep()
