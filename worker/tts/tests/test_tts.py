"""Tests for TTS worker."""

import io
import json
import pathlib
import struct
import tempfile

import httpx
import pytest

from worker.tts.output_validator import validate_audiobook_output
from worker.tts.worker import RecipeRunner, TTSWorker


class StubRunner(RecipeRunner):
    """Stub recipe runner that writes fake output files."""

    def __init__(self, code=0):
        self.code = code
        self.calls = []

    def run(self, image: str, input_dir: str, output_dir: str, env: dict) -> int:
        self.calls.append({"image": image, "input": input_dir, "output": output_dir, "env": env})
        if self.code != 0:
            return self.code

        out = pathlib.Path(output_dir)
        (out / "m4b").mkdir(parents=True, exist_ok=True)
        (out / "mp3").mkdir(parents=True, exist_ok=True)

        m4b_path = out / "m4b" / "audiobook.m4b"
        m4b_data = self._make_fake_m4b()
        m4b_path.write_bytes(m4b_data)

        (out / "mp3" / "opening-credits.mp3").write_bytes(b"fake mp3")
        (out / "mp3" / "chapter-001.mp3").write_bytes(b"fake mp3")
        (out / "mp3" / "closing-credits.mp3").write_bytes(b"fake mp3")
        (out / "mp3" / "retail-sample.mp3").write_bytes(b"fake mp3")

        (out / "metadata.json").write_text(json.dumps({"job_id": "test"}))
        (out / "qc-report.json").write_text(json.dumps({"acx_compliant": True}))

        return 0

    def _make_fake_m4b(self) -> bytes:
        """Create a minimal M4B-like file with ftyp and stik atoms."""
        ftyp_size = struct.pack(">I", 24)
        ftyp = ftyp_size + b"ftyp" + b"M4B " + b"\x00\x00\x00\x00" + b"M4A isommp42"
        stik_size = struct.pack(">I", 8)
        stik = stik_size + b"stik" + struct.pack(">I", 2)
        return ftyp + stik


def test_validate_audiobook_output_valid(tmp_path):
    (tmp_path / "m4b").mkdir()
    (tmp_path / "mp3").mkdir()

    ftyp_size = struct.pack(">I", 24)
    ftyp = ftyp_size + b"ftyp" + b"M4B " + b"\x00\x00\x00\x00" + b"M4A isommp42"
    stik_size = struct.pack(">I", 8)
    stik = stik_size + b"stik" + struct.pack(">I", 2)
    m4b_data = ftyp + stik
    (tmp_path / "m4b" / "audiobook.m4b").write_bytes(m4b_data)
    (tmp_path / "mp3" / "opening-credits.mp3").write_bytes(b"mp3")
    (tmp_path / "mp3" / "chapter-001.mp3").write_bytes(b"mp3")
    (tmp_path / "mp3" / "closing-credits.mp3").write_bytes(b"mp3")
    (tmp_path / "mp3" / "retail-sample.mp3").write_bytes(b"mp3")
    (tmp_path / "metadata.json").write_text("{}")
    (tmp_path / "qc-report.json").write_text("{}")

    errors = validate_audiobook_output(str(tmp_path))
    assert errors == []


def test_validate_audiobook_output_missing_m4b(tmp_path):
    (tmp_path / "mp3").mkdir()
    (tmp_path / "mp3" / "chapter-001.mp3").write_bytes(b"mp3")
    (tmp_path / "mp3" / "opening-credits.mp3").write_bytes(b"mp3")
    (tmp_path / "mp3" / "closing-credits.mp3").write_bytes(b"mp3")
    (tmp_path / "mp3" / "retail-sample.mp3").write_bytes(b"mp3")
    (tmp_path / "metadata.json").write_text("{}")
    (tmp_path / "qc-report.json").write_text("{}")

    errors = validate_audiobook_output(str(tmp_path))
    assert any("audiobook.m4b" in e for e in errors)


def test_validate_m4b_stik_flag(tmp_path):
    (tmp_path / "m4b").mkdir()
    (tmp_path / "mp3").mkdir()
    (tmp_path / "mp3" / "chapter-001.mp3").write_bytes(b"mp3")
    (tmp_path / "mp3" / "opening-credits.mp3").write_bytes(b"mp3")
    (tmp_path / "mp3" / "closing-credits.mp3").write_bytes(b"mp3")
    (tmp_path / "mp3" / "retail-sample.mp3").write_bytes(b"mp3")
    (tmp_path / "metadata.json").write_text("{}")
    (tmp_path / "qc-report.json").write_text("{}")

    ftyp_size = struct.pack(">I", 24)
    bad_m4b = ftyp_size + b"ftyp" + b"MP42" + b"\x00\x00\x00\x00" + b"isommp42"
    (tmp_path / "m4b" / "audiobook.m4b").write_bytes(bad_m4b)

    errors = validate_audiobook_output(str(tmp_path))
    assert any("M4B" in e for e in errors)


def test_tts_worker_process_job(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "script.json").write_text(json.dumps({"chapters": []}))
    (input_dir / "cover.jpg").write_bytes(b"fake jpg")

    output_prefix = tmp_path / "output"
    output_prefix.mkdir()

    class FakeRedis:
        def brpop(self, keys, timeout=30):
            return None

    class FakeHttp:
        def __init__(self):
            self.calls = []

        def post(self, url, headers=None, json=None):
            self.calls.append({"url": url, "json": json})
            return type("R", (), {"status_code": 200, "raise_for_status": lambda: None})()

        def delete(self, url, headers=None):
            self.calls.append({"url": url})
            return type("R", (), {"status_code": 200, "raise_for_status": lambda: None})()

    http = FakeHttp()
    runner = StubRunner(code=0)
    settings = {
        "node_id": "test-node",
        "coordinator_url": "http://coordinator:8000/api/v1",
        "api_key": "key",
        "redis_host": "localhost",
        "redis_port": 6379,
        "output_prefix": str(output_prefix) + "/",
    }
    worker = TTSWorker(settings, runner=runner, http=http, redis_client=FakeRedis())

    job = {
        "job_id": "test-job",
        "package_url": "file://" + str(input_dir) + "/",
        "voice_recipe": {"name": "test", "version": "1.0", "image": "test-recipe:latest"},
        "performance_style": "neutral",
    }

    result = worker.process_job(job)
    assert result["job_id"] == "test-job"
    assert runner.calls[0]["image"] == "test-recipe:latest"
    assert any("complete" in c["url"] for c in http.calls)


def test_tts_worker_fails_on_nonzero_exit(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "script.json").write_text("{}")

    class FakeRedis:
        def brpop(self, keys, timeout=30):
            return None

    class FakeHttp:
        def __init__(self):
            self.calls = []

        def post(self, url, headers=None, json=None):
            self.calls.append({"url": url, "json": json})
            return type("R", (), {"status_code": 200, "raise_for_status": lambda: None})()

        def delete(self, url, headers=None):
            return type("R", (), {"status_code": 200, "raise_for_status": lambda: None})()

    http = FakeHttp()
    runner = StubRunner(code=1)
    settings = {
        "node_id": "test-node",
        "coordinator_url": "http://coordinator:8000/api/v1",
        "api_key": "key",
        "redis_host": "localhost",
        "redis_port": 6379,
        "output_prefix": str(tmp_path / "out") + "/",
    }
    worker = TTSWorker(settings, runner=runner, http=http, redis_client=FakeRedis())

    job = {
        "job_id": "test-job",
        "package_url": "file://" + str(input_dir) + "/",
        "voice_recipe": {"image": "test:latest"},
        "performance_style": "neutral",
    }

    with pytest.raises(RuntimeError, match="exited 1"):
        worker.process_job(job)
