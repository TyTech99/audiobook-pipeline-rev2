import pytest

pytestmark = pytest.mark.usefixtures("clean_db")

OP = {"Authorization": "Bearer operator-secret"}


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
    depth = redis_client.llen("prep")
    assert depth == 1
    message = redis_client.lpop("prep")
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
    message = redis_client.lpop("prep")
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
    assert "page_range" in response.json()["detail"]


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
    assert redis_client.llen("convert") == 1
    import json

    msg = json.loads(redis_client.lpop("convert"))
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
