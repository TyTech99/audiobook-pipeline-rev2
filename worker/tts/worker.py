"""Worker loop for the TTS worker. Claims jobs from Redis, runs recipe container, reports to coordinator."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid

import httpx
import redis

from worker.common.llm_client import llm_env, llm_settings_from_mapping
from worker.common.settings import flatten_worker_settings
from worker.common.storage import download_dir, upload_dir

logger = logging.getLogger("worker.tts")


class RecipeRunner:
    """Runs the recipe container. Tests replace this with a stub."""

    def run(self, image: str, input_dir: str, output_dir: str, env: dict) -> int:
        raise NotImplementedError


class DockerRecipeRunner(RecipeRunner):
    def __init__(self):
        import docker

        self.client = docker.from_env()

    def run(self, image: str, input_dir: str, output_dir: str, env: dict) -> int:
        import docker.types

        kwargs = {
            "image": image,
            "detach": True,
            "environment": env,
            "volumes": {
                input_dir: {"bind": "/input", "mode": "ro"},
                output_dir: {"bind": "/output", "mode": "rw"},
            },
        }
        # Use GPU when the host has one; otherwise run CPU-only.
        if os.path.exists("/dev/nvidia0"):
            device = os.environ.get("GPU_DEVICE", "").strip()
            if device and device.lower() != "all":
                kwargs["device_requests"] = [
                    docker.types.GPURequest(device_ids=[d.strip() for d in device.split(",") if d.strip()])
                ]
            else:
                kwargs["device_requests"] = [docker.types.GPURequest(count=-1)]
        try:
            container = self.client.containers.run(**kwargs)
        except Exception as exc:
            if "nvidia" in str(exc).lower() or "gpu" in str(exc).lower():
                # Retry without GPU if the request failed.
                container = self.client.containers.run(
                    image,
                    detach=True,
                    environment=env,
                    volumes={
                        input_dir: {"bind": "/input", "mode": "ro"},
                        output_dir: {"bind": "/output", "mode": "rw"},
                    },
                )
            else:
                raise
        try:
            result = container.wait(timeout=7200)
            return int(result.get("StatusCode", 1))
        finally:
            container.remove(force=True)


class TTSWorker:
    def __init__(
        self,
        settings: dict,
        runner: RecipeRunner | None = None,
        http: httpx.Client | None = None,
        redis_client: redis.Redis | None = None,
    ):
        self.settings = settings
        self.runner = runner or DockerRecipeRunner()
        self.http = http or httpx.Client(timeout=30)
        self.redis = redis_client or redis.Redis(
            host=settings["redis_host"],
            port=settings["redis_port"],
            password=settings.get("redis_password") or None,
            db=settings.get("redis_db", 0),
        )
        self.worker_id = str(uuid.uuid4())
        self.node_id = settings["node_id"]
        self.coordinator_url = settings["coordinator_url"].rstrip("/")
        self.api_key = settings["api_key"]

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def register(self) -> None:
        response = self.http.post(
            f"{self.coordinator_url}/workers/register",
            headers=self._headers(),
            json={
                "worker_id": self.worker_id,
                "node_id": self.node_id,
                "type": "tts",
                "queues": ["convert"],
                "capabilities": {
                    "llm_slots": llm_settings_from_mapping(self.settings).slots,
                },
            },
        )
        response.raise_for_status()

    def heartbeat(self, current_job: str | None = None) -> None:
        response = self.http.post(
            f"{self.coordinator_url}/workers/{self.worker_id}/heartbeat",
            headers=self._headers(),
            json={"status": "idle", "current_job": current_job},
        )
        response.raise_for_status()

    def claim_job(self, timeout: int = 30) -> dict | None:
        result = self.redis.blmove(
            "convert:pending", "convert:processing",
            timeout=timeout,
            src="RIGHT", dest="LEFT"
        )
        if result is None:
            return None
        job = json.loads(result)
        job_id = job.get("job_id")
        # Record claim time for the reaper
        self.redis.hset("job:claims", job_id, time.time())
        return job

    def ack_job(self, job: dict) -> None:
        """Remove job from processing queue after successful completion."""
        job_id = job.get("job_id")
        self.redis.lrem("convert:processing", 1, json.dumps(job, sort_keys=True))
        self.redis.hdel("job:claims", job_id)

    def nack_job(self, job: dict, error: str) -> None:
        """Remove job from processing queue after failure."""
        job_id = job.get("job_id")
        self.redis.lrem("convert:processing", 1, json.dumps(job, sort_keys=True))
        self.redis.hdel("job:claims", job_id)

    def process_job(self, job: dict) -> dict:
        job_id = job["job_id"]
        recipe = job.get("voice_recipe", {})
        image = recipe.get("image") or self.settings.get("default_recipe_image") or os.environ.get("DEFAULT_RECIPE_IMAGE") or "audiobook-recipe-michael:local"

        import pathlib
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            input_dir = pathlib.Path(tmp) / "input"
            output_dir = pathlib.Path(tmp) / "output"
            input_dir.mkdir()
            output_dir.mkdir()

            package_url = job.get("package_url") or job.get("input_url") or ""
            if not package_url:
                raise RuntimeError("convert job missing package_url and input_url")
            download_dir(package_url, input_dir)

            env = {
                "RECIPE_VERSION": recipe.get("version", ""),
                "PERFORMANCE_STYLE": job.get("performance_style", "neutral"),
                "COVER_ART": "/input/cover.jpg",
            }
            env.update(llm_env(llm_settings_from_mapping(self.settings)))

            code = self.runner.run(image, str(input_dir), str(output_dir), env)
            if code != 0:
                raise RuntimeError(f"Recipe container exited {code}")

            root = str(self.settings.get("storage_root") or "/var/lib/audiobook").rstrip("/")
            output_prefix = self.settings.get("output_prefix") or f"file://{root}/audio/{job_id}/"
            output_url = upload_dir(output_dir, output_prefix)

        self.http.post(
            f"{self.coordinator_url}/jobs/{job_id}/complete",
            headers=self._headers(),
            json={"output_url": output_url},
        )

        return {"job_id": job_id, "output_url": output_url}

    def run(self, max_jobs: int | None = None) -> None:
        self.register()
        jobs_processed = 0
        while max_jobs is None or jobs_processed < max_jobs:
            try:
                job = self.claim_job(timeout=10)
            except Exception:
                time.sleep(5)
                continue
            if job is None:
                self.heartbeat()
                continue
            jobs_processed += 1
            try:
                job_id = job["job_id"]
                # Check if job is already complete (idempotency for at-least-once delivery)
                try:
                    response = self.http.get(
                        f"{self.coordinator_url}/jobs/{job_id}",
                        headers=self._headers(),
                        timeout=10,
                    )
                    response.raise_for_status()
                    job_data = response.json()
                    if job_data["status"] in ("complete", "cancelled"):
                        logger.info("Job %s already %s, skipping", job_id, job_data["status"])
                        self.ack_job(job)
                        continue
                    job = job_data
                except Exception as e:
                    logger.warning("Could not check job status: %s", e)
                self.heartbeat(job_id)
                self.process_job(job)
                self.ack_job(job)
            except Exception as exc:
                logger.error("Job %s failed: %s", job.get("job_id"), exc)
                try:
                    self.http.post(
                        f"{self.coordinator_url}/jobs/{job['job_id']}/fail",
                        headers=self._headers(),
                        json={"error": str(exc)},
                    )
                except Exception:
                    pass
                self.nack_job(job, str(exc))
        self.http.delete(f"{self.coordinator_url}/workers/{self.worker_id}", headers=self._headers())


def _expand_env(value):
    import re

    pattern = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-([^}]*))?\}")

    def repl(match):
        var = match.group(1)
        default = match.group(3)
        return os.environ.get(var, default if default is not None else "")

    if isinstance(value, str):
        return pattern.sub(repl, value)
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    return value


def main() -> None:
    import yaml

    path = os.environ.get("TTS_WORKER_CONFIG", "config/worker-tts.yaml")
    with open(path) as f:
        raw = _expand_env(yaml.safe_load(f))
    settings = flatten_worker_settings(raw)
    worker = TTSWorker(settings)
    worker.run()


if __name__ == "__main__":
    main()
