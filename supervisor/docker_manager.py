from __future__ import annotations

from uuid import uuid4

from supervisor.config import SupervisorSettings
from supervisor.coordinator_client import CoordinatorClient


class ProbeRunner:
    """Starts a named container and returns its exit code. Tests replace this."""

    def running(self, name: str) -> bool:
        return False

    def run(self, name: str, image: str, env: dict, timeout: int) -> int:
        raise NotImplementedError


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


class DockerManager:
    def __init__(self, settings: SupervisorSettings, runner: ProbeRunner, client: CoordinatorClient):
        self.settings = settings
        self.runner = runner
        self.client = client
        self.started: list[str] = []

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
        }
        code = self.runner.run(name, self.settings.probe_image, env, timeout=60)
        if code != 0:
            self.client.fail_probe(probe_id, f"probe container exited {code}")
