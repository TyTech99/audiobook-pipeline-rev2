import pytest

pytestmark = pytest.mark.usefixtures("clean_db")

OP = {"Authorization": "Bearer operator-secret"}

from coordinator.services.jobs import PREP_QUEUE, CONVERT_QUEUE


def test_submit_prep_job_creates_record_and_queue_message(app, sql):
    response = app.post(
        "/api/v1/jobs",
        headers=OP,
        json={
            "type": "prep",
            "input_format": "pdf",
            "input_url": "s3://bucket/inputs/test/source.pdf",
            "metadata": {"title": "Test Book", "author": "Test Author"},
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "submitted"
    job_id = body["job_id"]

    rows = sql("SELECT * FROM jobs WHERE job_id = %s", (job_id,))
    assert len(rows) == 1
    assert rows[0]["status"] == "submitted"
    assert rows[0]["type"] == "prep"
    assert rows[0]["input_format"] == "pdf"
    assert rows[0]["input_url"] == "s3://bucket/inputs/test/source.pdf"

    redis_client = app.app.state.redis
    depth = redis_client.llen(PREP_QUEUE)
    assert depth == 1
    message = redis_client.lpop(PREP_QUEUE)
    import json

    msg = json.loads(message)
    assert msg["job_id"] == job_id
    assert msg["type"] == "prep"
    assert msg["input_url"] == "s3://bucket/inputs/test/source.pdf"


def test_submit_prep_job_with_page_range(app, sql):
    response = app.post(
        "/api/v1/jobs",
        headers=OP,
        json={
            "type": "prep",
            "input_format": "pdf",
            "input_url": "s3://bucket/inputs/test/source.pdf",
            "metadata": {"title": "Test Book", "author": "Test Author"},
            "page_range": [1, 50],
        },
    )
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]

    rows = sql("SELECT * FROM jobs WHERE job_id = %s", (job_id,))
    assert rows[0]["metadata"]["page_range"] == [1, 50]

    redis_client = app.app.state.redis
    message = redis_client.lpop(PREP_QUEUE)
    import json

    msg = json.loads(message)
    assert msg["page_range"] == [1, 50]


def test_submit_prep_job_invalid_page_range(app, sql):
    response = app.post(
        "/api/v1/jobs",
        headers=OP,
        json={
            "type": "prep",
            "input_format": "pdf",
            "input_url": "s3://bucket/inputs/test/source.pdf",
            "page_range": [50, 1],
        },
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "invalid_request"
    assert "page_range" in body["error"]["message"]


def test_submit_convert_job(app, sql):
    response = app.post(
        "/api/v1/jobs",
        headers=OP,
        json={
            "type": "convert",
            "input_format": "epub",
            "input_url": "file:///var/lib/audiobook/packages/prep-id/",
            "metadata": {"performance_style": "neutral"},
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "submitted"
    job_id = body["job_id"]

    rows = sql("SELECT * FROM jobs WHERE job_id = %s", (job_id,))
    assert rows[0]["package_url"] == "file:///var/lib/audiobook/packages/prep-id/"
    assert rows[0]["input_url"] == "file:///var/lib/audiobook/packages/prep-id/"

    redis_client = app.app.state.redis
    assert redis_client.llen(CONVERT_QUEUE) == 1
    import json

    msg = json.loads(redis_client.lpop(CONVERT_QUEUE))
    assert msg["package_url"] == "file:///var/lib/audiobook/packages/prep-id/"
    assert msg["input_url"] == msg["package_url"]
    assert msg["performance_style"] == "neutral"


def test_complete_convert_job_from_submitted(app, sql):
    created = app.post(
        "/api/v1/jobs",
        headers=OP,
        json={
            "type": "convert",
            "input_format": "epub",
            "input_url": "file:///var/lib/audiobook/packages/p1/",
            "metadata": {},
        },
    )
    job_id = created.json()["job_id"]
    response = app.post(
        f"/api/v1/jobs/{job_id}/complete",
        headers=OP,
        json={"output_url": "file:///var/lib/audiobook/audio/out/"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "complete"
    rows = sql("SELECT * FROM jobs WHERE job_id = %s", (job_id,))
    assert rows[0]["status"] == "complete"
    assert rows[0]["output_url"] == "file:///var/lib/audiobook/audio/out/"


def test_get_job(app):
    created = app.post(
        "/api/v1/jobs",
        headers=OP,
        json={"type": "prep", "input_format": "pdf", "input_url": "s3://bucket/test.pdf", "metadata": {}},
    )
    job_id = created.json()["job_id"]
    response = app.get(f"/api/v1/jobs/{job_id}", headers=OP)
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_id
    assert body["status"] == "submitted"


def test_get_job_not_found(app):
    response = app.get("/api/v1/jobs/00000000-0000-0000-0000-000000000000", headers=OP)
    assert response.status_code == 404


def test_list_jobs(app):
    for i in range(3):
        app.post(
            "/api/v1/jobs",
            headers=OP,
            json={"type": "prep", "input_format": "pdf", "input_url": f"s3://bucket/{i}.pdf", "metadata": {}},
        )
    response = app.get("/api/v1/jobs", headers=OP)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert len(body["jobs"]) == 3

    filtered = app.get("/api/v1/jobs?status=submitted", headers=OP)
    assert filtered.json()["total"] == 3


def test_cancel_job(app):
    created = app.post(
        "/api/v1/jobs",
        headers=OP,
        json={"type": "prep", "input_format": "pdf", "input_url": "s3://bucket/test.pdf", "metadata": {}},
    )
    job_id = created.json()["job_id"]
    response = app.post(f"/api/v1/jobs/{job_id}/cancel", headers=OP)
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"

    again = app.post(f"/api/v1/jobs/{job_id}/cancel", headers=OP)
    assert again.status_code == 409


def test_progress_requires_in_progress(app):
    created = app.post(
        "/api/v1/jobs",
        headers=OP,
        json={"type": "prep", "input_format": "pdf", "input_url": "s3://bucket/test.pdf", "metadata": {}},
    )
    job_id = created.json()["job_id"]
    response = app.post(
        f"/api/v1/jobs/{job_id}/progress",
        headers={"Authorization": "Bearer node-key"},
        json={"stage": "prep", "percent": 50},
    )
    assert response.status_code in (401, 409)


def test_health_shows_queue_depths(app):
    app.post(
        "/api/v1/jobs",
        headers=OP,
        json={"type": "prep", "input_format": "pdf", "input_url": "s3://bucket/test.pdf", "metadata": {}},
    )
    response = app.get("/health")
    assert response.status_code == 200
    assert response.json()["queue_depths"]["prep"] == 1


# --- #57: artifact listing + download ---


def _insert_finished_job(sql, job_id, package_url):
    sql(
        """
        INSERT INTO jobs (
            job_id, type, status, input_format, input_url, package_url, metadata,
            prep, convert, progress, error, retry_count, created_at, updated_at
        ) VALUES (
            %s, 'prep', 'prep_complete', 'pdf', 'file:///input/x.pdf', %s, '{}'::jsonb,
            '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, NULL, 0, now(), now()
        )
        """,
        (job_id, package_url),
    )


def test_list_and_download_artifacts(app, sql, tmp_path):
    pkg = tmp_path / "pkg"
    (pkg / "m4b").mkdir(parents=True)
    (pkg / "book.epub").write_bytes(b"EPUBDATA")
    (pkg / "cover.jpg").write_bytes(b"JPGDATA")
    (pkg / "m4b" / "audiobook.m4b").write_bytes(b"M4BDATA")
    job_id = "0e0e0e0e-0e0e-4e0e-8e0e-0e0e0e0e0e0a"
    _insert_finished_job(sql, job_id, f"file://{pkg}/")

    resp = app.get(f"/api/v1/jobs/{job_id}/artifacts", headers=OP)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "prep_complete"
    names = {a["filename"] for a in body["artifacts"]}
    assert names == {"book.epub", "cover.jpg", "m4b/audiobook.m4b"}
    media = {a["filename"]: a["media_type"] for a in body["artifacts"]}
    assert media["book.epub"] == "application/epub+zip"
    assert media["m4b/audiobook.m4b"] == "audio/mp4"
    assert media["cover.jpg"] == "image/jpeg"

    dl = app.get(f"/api/v1/jobs/{job_id}/artifacts/book.epub", headers=OP)
    assert dl.status_code == 200
    assert dl.content == b"EPUBDATA"
    assert dl.headers["content-type"].startswith("application/epub+zip")
    assert "attachment" in dl.headers.get("content-disposition", "")

    nested = app.get(f"/api/v1/jobs/{job_id}/artifacts/m4b/audiobook.m4b", headers=OP)
    assert nested.status_code == 200
    assert nested.content == b"M4BDATA"


def test_artifact_guards(app, sql, tmp_path):
    import pytest as _pytest

    from coordinator.errors import ApiError
    from coordinator.services.jobs import get_artifact

    pkg = tmp_path / "pkg2"
    pkg.mkdir()
    (pkg / "book.epub").write_bytes(b"EPUBDATA")
    job_id = "0e0e0e0e-0e0e-4e0e-8e0e-0e0e0e0e0e0b"
    _insert_finished_job(sql, job_id, f"file://{pkg}/")
    no_pkg_job = "0e0e0e0e-0e0e-4e0e-8e0e-0e0e0e0e0e0c"
    _insert_finished_job(sql, no_pkg_job, None)

    # Traversal and exotic filenames are rejected before any filesystem access.
    with app.app.state.pool.connection() as conn:
        with _pytest.raises(ApiError) as trav:
            get_artifact(conn, job_id, "../outside")
        assert trav.value.status == 400
        with _pytest.raises(ApiError) as weird:
            get_artifact(conn, job_id, "we;ird name")
        assert weird.value.status == 400
        with _pytest.raises(ApiError) as missing:
            get_artifact(conn, job_id, "nope.epub")
        assert missing.value.status == 404

    # Job with no package_url -> artifacts_not_found.
    resp = app.get(f"/api/v1/jobs/{no_pkg_job}/artifacts", headers=OP)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "artifacts_not_found"

    # Unknown job -> 404.
    resp = app.get("/api/v1/jobs/0e0e0e0e-0e0e-4e0e-8e0e-0e0e0e0e0e0f/artifacts", headers=OP)
    assert resp.status_code == 404
