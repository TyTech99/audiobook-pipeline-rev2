"""Worker loop for the TTS worker. Claims jobs from Redis, runs recipe container, reports to coordinator."""

from __future__ import annotations

import json
import os
import time
import uuid

import httpx
import redis


class RecipeRunner:
    """Runs the recipe container. Tests replace this with a stub."""

    def run(self, image: str, input_dir: str, output_dir: str, env: dict) -> int:
        raise NotImplementedError


class DockerRecipeRunner(RecipeRunner):
    def __init__(self):
        import docker

        self.client = docker.from_env()

    def run(self, image: str, input_dir: str, output_dir: str, env: dict) -> int:
        container = self.client.containers.run(
            image,
            detach=True,
            environment=env,
            volumes={
                input_dir: {"bind": "/input", "mode": "ro"},
                output_dir: {"bind": "/output", "mode": "rw"},
            },
            devices=["/dev/nvidia0"],
        )
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
        result = self.redis.brpop(["convert"], timeout=timeout)
        if result is None:
            return None
        _, raw = result
        return json.loads(raw)

    def process_job(self, job: dict) -> dict:
        job_id = job["job_id"]
        recipe = job.get("voice_recipe", {})
        image = recipe.get("image", self.settings.get("default_recipe_image", "audiobook-recipe:latest"))

        import pathlib
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            input_dir = pathlib.Path(tmp) / "input"
            output_dir = pathlib.Path(tmp) / "output"
            input_dir.mkdir()
            output_dir.mkdir()

            package_url = job.get("package_url", "")
            if package_url.startswith("file://"):
                src = pathlib.Path(package_url[len("file://"):])
                for f in src.iterdir():
                    (input_dir / f.name).write_bytes(f.read_bytes())
            else:
                raise NotImplementedError("S3 download not implemented in this slice")

            env = {
                "RECIPE_VERSION": recipe.get("version", ""),
                "PERFORMANCE_STYLE": job.get("performance_style", "neutral"),
                "COVER_ART": "/input/cover.jpg",
            }

            code = self.runner.run(image, str(input_dir), str(output_dir), env)
            if code != 0:
                raise RuntimeError(f"Recipe container exited {code}")

            output_url = self._upload(job_id, output_dir)

        self.http.post(
            f"{self.coordinator_url}/jobs/{job_id}/complete",
            headers=self._headers(),
            json={"output_url": output_url},
        )

        return {"job_id": job_id, "output_url": output_url}

    def _upload(self, job_id: str, output_dir) -> str:
        import pathlib
        import shutil

        output_prefix = self.settings.get("output_prefix") or f"s3://bucket/audio/{job_id}/"
        if output_prefix.startswith("file://"):
            dest = pathlib.Path(output_prefix[len("file://"):])
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copytree(output_dir, dest, dirs_exist_ok=True)
            return str(dest) + "/"
        if output_prefix.endswith("/"):
            dest = pathlib.Path(output_prefix)
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copytree(output_dir, dest, dirs_exist_ok=True)
            return str(dest) + "/"
        return f"s3://bucket/audio/{job_id}/"

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
                self.heartbeat(job["job_id"])
                self.process_job(job)
            except Exception as exc:
                try:
                    self.http.post(
                        f"{self.coordinator_url}/jobs/{job['job_id']}/fail",
                        headers=self._headers(),
                        json={"error": str(exc)},
                    )
                except Exception:
                    pass
        self.http.delete(f"{self.coordinator_url}/workers/{self.worker_id}", headers=self._headers())


def main() -> None:
    import yaml

    path = os.environ.get("TTS_WORKER_CONFIG", "config/worker-tts.yaml")
    with open(path) as f:
        settings = yaml.safe_load(f)
    worker = TTSWorker(settings)
    worker.run()


if __name__ == "__main__":
    main()
