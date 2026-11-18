from __future__ import annotations

import logging
import os
import signal
import sys
import time

from supervisor.config import load_settings
from supervisor.coordinator_client import CoordinatorClient
from supervisor.docker_manager import DockerManager, DockerProbeRunner
from supervisor.loop import run_loop


def setup_logging(settings) -> None:
    """Configure logging to both stdout and the configured log file."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    handlers = [logging.StreamHandler(sys.stdout)]

    log_file = settings.log_file
    if log_file:
        try:
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            handlers.append(logging.FileHandler(log_file))
        except OSError as e:
            print(f"Warning: cannot open log file {log_file}: {e}", file=sys.stderr)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    for h in handlers:
        h.setFormatter(fmt)
        h.setLevel(level)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = handlers


def main() -> None:
    path = os.environ.get("SUPERVISOR_CONFIG", "config/supervisor.yaml")
    settings = load_settings(path)
    setup_logging(settings)
    logger = logging.getLogger("supervisor")

    logger.info("Starting supervisor (config=%s)", path)
    logger.info("Coordinator: %s", settings.coordinator_url)
    logger.info("Node ID: %s", settings.node_id)

    stop = {"flag": False}

    def handle(_signum, _frame):
        logger.info("Received stop signal")
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
        logger.info("Supervisor shutting down")
        client.close()


if __name__ == "__main__":
    main()
