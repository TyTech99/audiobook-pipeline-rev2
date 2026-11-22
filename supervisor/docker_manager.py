from __future__ import annotations

import logging
import os
from uuid import uuid4
from supervisor.config import SupervisorSettings
from supervisor.coordinator_client import CoordinatorClient

logger = logging.getLogger(__name__)


class ProbeRunner:
    """Starts a named container and returns its exit code. Tests replace this."""

    def running(self, name: str) -> bool:
        return False

    def run(self, name: str, image: str, env: dict, timeout: int) -> int:
        raise NotImplementedError

    def start_worker(self, name: str, image: str, env: dict, gpu: bool, volumes: list[str], extra_devices: list[str]) -> None:
        raise NotImplementedError

    def stop_worker(self, name: str) -> None:
        raise NotImplementedError

    def worker_image_id(self, name: str) -> str | None:
        """Image ID of the named running worker, or None when unknown."""
        return None

    def image_id(self, tag: str) -> str | None:
        """Image ID the tag currently resolves to, or None when unknown."""
        return None


class DockerProbeRunner(ProbeRunner):
    def __init__(self):
        import docker

        self.client = docker.from_env()

    def running(self, name: str) -> bool:
        try:
            container = self.client.containers.get(name)
        except Exception:
            return False
        return container.status == "running"

    def run(self, name: str, image: str, env: dict, timeout: int) -> int:
        try:
            existing = self.client.containers.get(name)
            existing.remove(force=True)
        except Exception:
            pass
        container = self.client.containers.run(
            image,
            name=name,
            detach=True,
            environment=env,
        )
        try:
            result = container.wait(timeout=timeout)
            code = int(result.get("StatusCode", 1))
        except Exception:
            container.kill()
            code = 1
        finally:
            try:
                container.remove(force=True)
            except Exception:
                pass
        return code

    def start_worker(self, name: str, image: str, env: dict, gpu: bool, volumes: list[str], extra_devices: list[str]) -> None:
        try:
            existing = self.client.containers.get(name)
            existing.remove(force=True)
        except Exception:
            pass
        kwargs = {
            "image": image,
            "name": name,
            "detach": True,
            "environment": env,
            "restart_policy": {"Name": "no"},
            "volumes": volumes,
            "network_mode": "host",
        }
        if extra_devices:
            kwargs["devices"] = extra_devices
        if gpu:
            import docker

            device = os.environ.get("GPU_DEVICE", "").strip()
            if device and device.lower() != "all":
                kwargs["device_requests"] = [
                    docker.types.DeviceRequest(device_ids=[d.strip() for d in device.split(",") if d.strip()])
                ]
            else:
                kwargs["device_requests"] = [docker.types.DeviceRequest(count=-1)]
        self.client.containers.run(**kwargs)

    def stop_worker(self, name: str) -> None:
        try:
            container = self.client.containers.get(name)
            container.stop(timeout=30)
            container.remove(force=True)
        except Exception:
            pass

    def worker_image_id(self, name: str) -> str | None:
        try:
            container = self.client.containers.get(name)
            if container is None:
                return None
            # attrs["Image"] is the raw inspect value; it keeps working after
            # the original image record was re-tagged or removed, unlike
            # container.image, which 404s once the daemon forgets the image
            # (exactly the rebuild case this check exists for).
            return container.attrs.get("Image")
        except Exception:
            return None

    def image_id(self, tag: str) -> str | None:
        try:
            return self.client.images.get(tag).id
        except Exception:
            return None


class DockerManager:
    def __init__(self, settings: SupervisorSettings, runner: ProbeRunner, client: CoordinatorClient):
        self.settings = settings
        self.runner = runner
        self.client = client
        self.started: list[str] = []
        self.worker_started_at: dict[str, float] = {}
        self.last_queue_depth: dict[str, int] = {}

    def start_probe(self, probe_id: str, worker_type: str) -> None:
        name = f"audiobook-probe-{probe_id}"
        if self.runner.running(name):
            return
        self.started.append(name)
        env = {
            "NODE_ID": self.settings.node_id,
            "WORKER_ID": str(uuid4()),
            "WORKER_TYPE": worker_type,
            "API_KEY": self.settings.api_key,
            "COORDINATOR_URL": self.settings.coordinator_url,
            "COORDINATOR_ID": self.settings.coordinator_id,
            "PROBE": "1",
            "PROBE_ID": probe_id,
            "REDIS_HOST": self.settings.redis_host,
            "REDIS_PORT": str(self.settings.redis_port),
            "REDIS_PASSWORD": self.settings.redis_password,
            "S3_ENDPOINT": self.settings.s3_endpoint,
            "S3_BUCKET": self.settings.s3_bucket,
            "S3_ACCESS_KEY": self.settings.s3_access_key,
            "S3_SECRET_KEY": self.settings.s3_secret_key,
            **self._llm_env(),
        }
        code = self.runner.run(name, self.settings.probe_image, env, timeout=60)
        if code != 0:
            self.client.fail_probe(probe_id, f"probe container exited {code}")

    def start_worker(self, worker_type: str) -> None:
        name = f"audiobook-worker-{worker_type}"
        if self.runner.running(name) and not self._worker_image_stale(name, worker_type):
            return
        image = self.settings.prep_image if worker_type == "prep" else self.settings.tts_image
        if not image:
            return
        self._spawn_worker(worker_type, image)

    def _spawn_worker(self, worker_type: str, image: str) -> None:
        name = f"audiobook-worker-{worker_type}"
        logger.info("Starting worker: type=%s name=%s image=%s", worker_type, name, image)
        env = {
            "NODE_ID": self.settings.node_id,
            "WORKER_TYPE": worker_type,
            "API_KEY": self.settings.api_key,
            "COORDINATOR_URL": self.settings.coordinator_url,
            "COORDINATOR_ID": self.settings.coordinator_id,
            "REDIS_HOST": self.settings.redis_host,
            "REDIS_PORT": str(self.settings.redis_port),
            "REDIS_PASSWORD": self.settings.redis_password,
            "S3_ENDPOINT": self.settings.s3_endpoint,
            "S3_BUCKET": self.settings.s3_bucket,
            "S3_ACCESS_KEY": self.settings.s3_access_key,
            "S3_SECRET_KEY": self.settings.s3_secret_key,
            "STORAGE_ROOT": "/var/lib/audiobook",
            "GPU_DEVICE": os.environ.get("GPU_DEVICE", ""),
            **self._llm_env(),
        }
        if worker_type == "tts":
            env["DEFAULT_RECIPE_IMAGE"] = self.settings.recipe_image
        volumes = ["/var/lib/audiobook:/var/lib/audiobook"]
        extra_devices: list[str] = []
        if worker_type == "tts":
            # Mount docker.sock so the TTS worker can launch recipe containers.
            volumes.append("/var/run/docker.sock:/var/run/docker.sock")
        gpu = worker_type == "tts"
        self.runner.start_worker(name, image, env, gpu, volumes, extra_devices)
        import time

        self.worker_started_at[name] = time.time()

    def _llm_env(self) -> dict[str, str]:
        return {
            "LLM_BASE_URL": self.settings.llm_base_url,
            "LLM_API_KEY": self.settings.llm_api_key,
            "LLM_MODEL": self.settings.llm_model,
            "LLM_TIMEOUT_SECONDS": str(self.settings.llm_timeout_seconds),
            "LLM_SLOTS": str(self.settings.llm_slots),
        }

    def stop_worker(self, worker_type: str) -> None:
        name = f"audiobook-worker-{worker_type}"
        logger.info("Stopping worker: type=%s name=%s", worker_type, name)
        self.runner.stop_worker(name)
        self.worker_started_at.pop(name, None)

    def _worker_image_stale(self, name: str, worker_type: str) -> bool:
        """True when the running worker's image differs from the current tag (#41).

        A rebuilt image used to silently never reach a running worker: the
        running() guard returned early and the stale container kept serving
        jobs until the idle timeout stopped it. Now the drift check stops
        the stale container so the caller recreates it from the current
        image. Unknown image IDs (None) never trigger a recreate.
        """
        current = self.runner.worker_image_id(name)
        if current is None:
            return False
        tag = self.settings.prep_image if worker_type == "prep" else self.settings.tts_image
        expected = self.runner.image_id(tag) if tag else None
        if expected is None or current == expected:
            return False
        logger.info(
            "Worker %s runs stale image %s (current build: %s); recreating",
            name, current[:19], expected[:19],
        )
        self.stop_worker(worker_type)
        return True

    def check_worker_images(self, queue_depths: dict[str, int]) -> None:
        """Recreate running workers whose image tag has moved on (#41).

        Runs every heartbeat. The depth-triggered start_worker path alone
        cannot catch this: a running worker claims new jobs within its own
        poll window (~10s), faster than the supervisor's heartbeat (~30s),
        so a rebuilt image would never be reached until the idle timeout
        stopped the old container. Here the drift check runs each cycle and
        recreates the stale worker as soon as its queue is quiet — a
        rebuild now deploys within one heartbeat, and never kills a
        mid-flight job (busy queues are skipped until the next cycle).
        """
        for worker_type in ("prep", "tts"):
            name = f"audiobook-worker-{worker_type}"
            if not self.runner.running(name):
                continue
            queue = "prep" if worker_type == "prep" else "convert"
            if queue_depths.get(queue, 0) > 0 or queue_depths.get(f"{queue}_processing", 0) > 0:
                continue
            if not self._worker_image_stale(name, worker_type):
                continue
            image = self.settings.prep_image if worker_type == "prep" else self.settings.tts_image
            if not image:
                continue
            self._spawn_worker(worker_type, image)

    def check_idle(self, queue_depths: dict[str, int], now: float | None = None) -> None:
        import time

        now = now if now is not None else time.time()
        self.last_queue_depth = queue_depths
        for worker_type in ("prep", "tts"):
            name = f"audiobook-worker-{worker_type}"
            if not self.runner.running(name):
                continue
            queue = "prep" if worker_type == "prep" else "convert"
            depth = queue_depths.get(queue, 0)
            processing_depth = queue_depths.get(f"{queue}_processing", 0)
            if depth > 0 or processing_depth > 0:
                continue
            started = self.worker_started_at.get(name, now)
            if now - started >= self.settings.idle_timeout_seconds:
                self.stop_worker(worker_type)
