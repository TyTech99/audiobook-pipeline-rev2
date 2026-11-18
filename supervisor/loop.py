from __future__ import annotations

import logging
import time

from supervisor.coordinator_client import CoordinatorClient, IdentityMismatch
from supervisor.docker_manager import DockerManager
from supervisor.hardware import detect_hardware

logger = logging.getLogger("supervisor.loop")


def run_loop(client: CoordinatorClient, docker: DockerManager, sleep, stop, hardware=None) -> None:
    """Register, then heartbeat until stop() is true. SIGTERM is the caller's stop flag."""
    try:
        client.check_identity()
    except IdentityMismatch:
        raise
    info = hardware if hardware is not None else detect_hardware()
    client.register(info)
    logger.info("Registered with coordinator, entering main loop")
    while not stop():
        body = client.heartbeat()
        command = body.get("command")
        if command == "start_probe":
            logger.info("Starting probe: %s (%s)", body["probe_id"], body["worker_type"])
            docker.start_probe(body["probe_id"], body["worker_type"])
        queue_depths = body.get("queue_depths") or {}
        logger.debug("Queue depths: %s", queue_depths)
        for worker_type in ("prep", "tts"):
            queue = "prep" if worker_type == "prep" else "convert"
            if queue_depths.get(queue, 0) > 0:
                logger.info("Queue '%s' has %d jobs, starting %s worker", queue, queue_depths[queue], worker_type)
                docker.start_worker(worker_type)
        docker.check_idle(queue_depths, time.time())
        sleep()
