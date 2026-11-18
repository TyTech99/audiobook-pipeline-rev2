"""End-to-end test: coordinator + Redis + prep worker.

Boots the coordinator with real Redis, submits a prep job, runs the prep worker,
and asserts the job reaches prep_complete with a valid page ledger.
"""

import io
import json
import os
import pathlib
import tempfile

import fitz
import pytest
from fastapi.testclient import TestClient

from coordinator.config import Settings
from coordinator.main import create_app
from worker.prep.worker import PrepWorker

DSN = os.environ.get(
    "DATABASE_URL",
    "postgresql://audiobook:audiobook@127.0.0.1:5432/audiobook_test",
)

OP = {"Authorization": "Bearer operator-secret"}


def _make_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(
        fitz.Rect(50, 50, 500, 700),
        "Chapter 1\n\nThis is the first page of the book. It has some content to read.",
        fontsize=12,
    )
    page = doc.new_page()
    page.insert_textbox(
        fitz.Rect(50, 50, 500, 700),
        "This is the second page. It continues the story with more text.",
        fontsize=12,
    )
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


@pytest.fixture(scope="module")
def e2e_app():
    import psycopg

    try:
        with psycopg.connect(DSN, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
    except Exception:
        pytest.skip("DATABASE_URL is not reachable")

    settings = Settings(
        coordinator_id="coord-e2e",
        operator_api_keys=["operator-secret"],
        postgres_dsn=DSN,
        redis_host="127.0.0.1",
        redis_port=6379,
        redis_password=os.environ.get("REDIS_PASSWORD", "audiobook-test"),
        redis_db=0,
        s3_endpoint="",
        heartbeat_timeout_seconds=120,
        probe_timeout_seconds=90,
    )
    application = create_app(settings)
    with TestClient(application) as client:
        yield client


@pytest.fixture
def clean_e2e(e2e_app):
    pool = e2e_app.app.state.pool
    redis_client = e2e_app.app.state.redis
    with pool.connection() as conn:
        conn.execute("TRUNCATE jobs, nodes, workers, probes CASCADE")
        conn.commit()
    redis_client.flushdb()
    yield


def test_e2e_prep_job(e2e_app, clean_e2e, tmp_path):
    pdf_data = _make_pdf()
    pdf_path = tmp_path / "source.pdf"
    pdf_path.write_bytes(pdf_data)

    response = e2e_app.post(
        "/api/v1/jobs",
        headers=OP,
        json={
            "type": "prep",
            "input_format": "pdf",
            "input_url": f"file://{pdf_path}",
            "metadata": {"title": "E2E Test Book", "author": "E2E Author"},
        },
    )
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]

    initial = e2e_app.get(f"/api/v1/jobs/{job_id}", headers=OP)
    assert initial.status_code == 200
    assert initial.json()["status"] == "submitted"

    redis_client = e2e_app.app.state.redis
    assert redis_client.llen("prep") == 1

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    class FakeHttp:
        def __init__(self, base_url):
            self.base_url = base_url
            self.calls = []

        def post(self, url, headers=None, json=None):
            self.calls.append({"url": url, "json": json})
            return type("R", (), {"status_code": 200, "raise_for_status": lambda: None})()

        def delete(self, url, headers=None):
            self.calls.append({"url": url})
            return type("R", (), {"status_code": 200, "raise_for_status": lambda: None})()

        def get(self, url, headers=None):
            return type("R", (), {"status_code": 200, "content": b"", "raise_for_status": lambda: None})()

    worker_settings = {
        "node_id": "e2e-node",
        "coordinator_url": "http://localhost:8000/api/v1",
        "api_key": "node-key",
        "redis_host": "127.0.0.1",
        "redis_port": 6379,
        "redis_password": os.environ.get("REDIS_PASSWORD", "audiobook-test"),
        "redis_db": 0,
        "output_prefix": str(output_dir) + "/",
        # Offline e2e does not reach a live model; require=false skips analysis.
        "llm": {"base_url": "", "required": False},
    }

    http = FakeHttp("http://localhost:8000/api/v1")
    worker = PrepWorker(worker_settings, http=http, redis_client=redis_client)

    job = worker.claim_job(timeout=5)
    assert job is not None
    assert job["job_id"] == job_id

    package = worker.process_job(job)

    response = e2e_app.post(
        f"/api/v1/jobs/{job_id}/complete",
        headers={"Authorization": "Bearer operator-secret"},
        json={
            "package_url": str(output_dir) + "/",
            "total_pages": 2,
            "kept_pages": 2,
            "skipped_pages": 0,
        },
    )
    if response.status_code == 409:
        e2e_app.post(
            f"/api/v1/jobs/{job_id}/progress",
            headers={"Authorization": "Bearer operator-secret"},
            json={"stage": "prep", "percent": 100},
        )
        response = e2e_app.post(
            f"/api/v1/jobs/{job_id}/complete",
            headers={"Authorization": "Bearer operator-secret"},
            json={
                "package_url": str(output_dir) + "/",
                "total_pages": 2,
                "kept_pages": 2,
                "skipped_pages": 0,
            },
        )
    assert response.status_code == 200, response.text

    assert package["manifest"]["validation"]["page_parity"] is True
    assert package["manifest"]["statistics"]["total_pages"] == 2
    assert len(package["page_ledger"]["pages"]) == 2

    ledger_errors = []
    for entry in package["page_ledger"]["pages"]:
        if entry["action"] not in ("kept", "skipped"):
            ledger_errors.append(f"Invalid action: {entry['action']}")
        if not entry.get("reason"):
            ledger_errors.append("Missing reason")
        if not entry.get("section"):
            ledger_errors.append("Missing section")
    assert ledger_errors == []

    assert (output_dir / "book.epub").exists()
    assert (output_dir / "script.json").exists()
    assert (output_dir / "page-ledger.json").exists()
    assert (output_dir / "metadata.json").exists()
    assert (output_dir / "manifest.json").exists()
    assert (output_dir / "cover.jpg").exists()
    cover_data = (output_dir / "cover.jpg").read_bytes()
    assert cover_data[:2] == b"\xff\xd8", "cover.jpg should be a JPEG"

    final = e2e_app.get(f"/api/v1/jobs/{job_id}", headers=OP)
    assert final.status_code == 200
    body = final.json()
    assert body["status"] == "prep_complete"
    assert body["package_url"] is not None
    assert body["prep"]["total_pages"] == 2


def test_e2e_convert_job_with_stub_runner(e2e_app, clean_e2e, tmp_path):
    """Submit a convert job, run it with StubRunner, and verify completion."""
    import json
    import struct

    # Create a fake prep package directory
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    (package_dir / "script.json").write_text(json.dumps({"chapters": []}))
    (package_dir / "cover.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
    (package_dir / "metadata.json").write_text(json.dumps({"title": "Test", "author": "Test"}))

    # Invite a node so we have a valid node API key for progress/complete calls
    node_response = e2e_app.post(
        "/api/v1/nodes",
        headers=OP,
        json={
            "node_id": "e2e-convert-node",
            "display_name": "E2E Convert Node",
            "allowed_queues": ["prep", "convert"],
            "working_hours": {"enabled": False, "start": "00:00", "end": "23:59", "timezone": "UTC"},
        },
    )
    assert node_response.status_code == 201, node_response.text
    node_api_key = node_response.json()["node_api_key"]
    NODE = {"Authorization": f"Bearer {node_api_key}"}

    # Submit convert job
    response = e2e_app.post(
        "/api/v1/jobs",
        headers=OP,
        json={
            "type": "convert",
            "input_format": "epub",
            "input_url": f"file://{package_dir}/",
            "metadata": {},
        },
    )
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]

    redis_client = e2e_app.app.state.redis
    assert redis_client.llen("convert") == 1

    # Claim the job. Convert queue messages carry package_url = input_url.
    message = redis_client.lpop("convert")
    job = json.loads(message)
    assert job["job_id"] == job_id
    assert job["type"] == "convert"
    assert job["input_url"] == f"file://{package_dir}/"
    assert job["package_url"] == f"file://{package_dir}/"

    # Use StubRunner to simulate TTS
    from worker.tts.worker import RecipeRunner, TTSWorker

    class StubRunner(RecipeRunner):
        def run(self, image, input_dir, output_dir, env):
            import pathlib
            out = pathlib.Path(output_dir)
            (out / "m4b").mkdir(parents=True, exist_ok=True)
            (out / "mp3").mkdir(parents=True, exist_ok=True)
            ftyp = struct.pack(">I", 24) + b"ftyp" + b"M4B " + b"\x00\x00\x00\x00" + b"M4A isommp42"
            stik = struct.pack(">I", 8) + b"stik" + struct.pack(">I", 2)
            (out / "m4b" / "audiobook.m4b").write_bytes(ftyp + stik)
            (out / "mp3" / "chapter-001.mp3").write_bytes(b"mp3")
            (out / "mp3" / "opening-credits.mp3").write_bytes(b"mp3")
            (out / "mp3" / "closing-credits.mp3").write_bytes(b"mp3")
            (out / "mp3" / "retail-sample.mp3").write_bytes(b"mp3")
            (out / "metadata.json").write_text("{}")
            (out / "qc-report.json").write_text("{}")
            return 0

    class FakeRedis:
        def brpop(self, keys, timeout=30):
            return None

    class FakeHttp:
        def __init__(self, base_url):
            self.base_url = base_url
            self.calls = []

        def post(self, url, headers=None, json=None):
            self.calls.append({"url": url, "json": json})
            return type("R", (), {"status_code": 200, "raise_for_status": lambda: None})()

        def delete(self, url, headers=None):
            self.calls.append({"url": url})
            return type("R", (), {"status_code": 200, "raise_for_status": lambda: None})()

    output_dir = tmp_path / "audio"
    output_dir.mkdir()

    worker_settings = {
        "node_id": "e2e-node",
        "coordinator_url": "http://localhost:8000/api/v1",
        "api_key": "node-key",
        "redis_host": "127.0.0.1",
        "redis_port": 6379,
        "redis_password": os.environ.get("REDIS_PASSWORD", "audiobook-test"),
        "redis_db": 0,
        "output_prefix": str(output_dir) + "/",
        "default_recipe_image": "audiobook-recipe-michael:local",
    }

    http = FakeHttp("http://localhost:8000/api/v1")
    worker = TTSWorker(worker_settings, runner=StubRunner(), http=http, redis_client=FakeRedis())

    result = worker.process_job(job)
    assert result["job_id"] == job_id

    # Verify output files
    assert (output_dir / "m4b" / "audiobook.m4b").exists()
    assert (output_dir / "mp3" / "chapter-001.mp3").exists()

    # Convert jobs complete from submitted (or converting) with output_url.
    response = e2e_app.post(
        f"/api/v1/jobs/{job_id}/complete",
        headers=NODE,
        json={"output_url": str(output_dir) + "/"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "complete"

    final = e2e_app.get(f"/api/v1/jobs/{job_id}", headers=OP)
    assert final.status_code == 200
    body = final.json()
    assert body["status"] == "complete"
    assert body["output_url"] is not None
