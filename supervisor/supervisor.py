from __future__ import annotations

import os
import signal
import time

from supervisor.config import load_settings
from supervisor.coordinator_client import CoordinatorClient
from supervisor.docker_manager import DockerManager, DockerProbeRunner
from supervisor.loop import run_loop


def main() -> None:
    path = os.environ.get("SUPERVISOR_CONFIG", "config/supervisor.yaml")
    settings = load_settings(path)
    stop = {"flag": False}

    def handle(_signum, _frame):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)
    client = CoordinatorClient(settings)
    docker = DockerManager(settings, DockerProbeRunner(), client)
    try:
        run_loop(
            client,
            docker,
            sleep=lambda: time.sleep(settings.heartbeat_interval_seconds),
            stop=lambda: stop["flag"],
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()
