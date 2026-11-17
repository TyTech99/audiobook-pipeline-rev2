import pytest
from uuid import uuid4

pytestmark = pytest.mark.usefixtures("clean_db")

HOURS = {"enabled": True, "start": "22:00", "end": "08:00", "timezone": "America/New_York"}
OP = {"Authorization": "Bearer operator-secret"}


def _invite(client, node_id="gpu-study", queues=None):
    response = client.post(
        "/api/v1/nodes",
        headers=OP,
        json={
            "node_id": node_id,
            "display_name": "study",
            "allowed_queues": queues or ["prep", "convert"],
            "working_hours": HOURS,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _register(client, node_id, key, queues=None, gpu=None):
    response = client.post(
        f"/api/v1/nodes/{node_id}/register",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "queues": queues or ["prep", "convert"],
            "hardware": {"cpu": 4, "ram_gb": 16, "gpu": gpu},
            "working_hours": HOURS,
        },
    )
    return response


def test_health_is_open(app):
    response = app.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["coordinator_id"] == "coord-test"
    assert body["postgres"] == "connected"
    assert body["s3"] == "disabled"
    assert "operator-secret" not in response.text


def test_nodes_require_a_key(app):
    assert app.get("/api/v1/nodes").status_code == 401
    assert app.get("/api/v1/workers", headers=OP).json()["total"] == 0


def test_node_key_cannot_invite(app):
    invited = _invite(app)
    status = app.post(
        "/api/v1/nodes",
        headers={"Authorization": f"Bearer {invited['node_api_key']}"},
        json={
            "node_id": "other-node",
            "display_name": "x",
            "allowed_queues": ["prep"],
            "working_hours": HOURS,
        },
    ).status_code
    assert status == 401


def test_invite_key_is_not_on_get(app):
    invited = _invite(app)
    assert invited["node_api_key"]
    body = app.get(f"/api/v1/nodes/{invited['node_id']}", headers=OP).json()
    assert "node_api_key" not in body
    assert body["state"] == "pending"


def test_register_is_idempotent_and_drops_convert_without_gpu(app):
    invited = _invite(app)
    first = _register(app, invited["node_id"], invited["node_api_key"])
    assert first.status_code == 200, first.text
    assert first.json()["state"] == "commissioned"
    assert first.json()["effective_queues"] == ["prep"]
    second = _register(app, invited["node_id"], invited["node_api_key"])
    assert second.status_code == 200
    shown = app.get(f"/api/v1/nodes/{invited['node_id']}", headers=OP).json()
    assert shown["allowed_queues"] == ["prep", "convert"]
    assert shown["effective_queues"] == ["prep"]


def test_gpu_keeps_convert(app):
    invited = _invite(app, queues=["convert"])
    response = _register(
        app,
        invited["node_id"],
        invited["node_api_key"],
        queues=["convert"],
        gpu={"model": "RTX 4090", "vram_gb": 24},
    )
    assert response.json()["effective_queues"] == ["convert"]


def test_stale_heartbeat_goes_offline_then_returns(app, sql):
    invited = _invite(app)
    assert _register(app, invited["node_id"], invited["node_api_key"]).status_code == 200
    sql(
        "UPDATE nodes SET last_heartbeat_at = now() - interval '5 minutes' WHERE node_id = %s",
        (invited["node_id"],),
    )
    assert app.get(f"/api/v1/nodes/{invited['node_id']}", headers=OP).json()["state"] == "offline"
    beat = app.post(
        f"/api/v1/nodes/{invited['node_id']}/heartbeat",
        headers={"Authorization": f"Bearer {invited['node_api_key']}"},
    )
    assert beat.status_code == 200
    assert beat.json()["node_state"] == "commissioned"
    assert beat.json()["command"] == "run"


def test_delete_rules(app, sql):
    pending = _invite(app, node_id="pending-box")
    assert app.delete(f"/api/v1/nodes/{pending['node_id']}", headers=OP).status_code == 200

    live = _invite(app, node_id="live-box")
    _register(app, live["node_id"], live["node_api_key"])
    assert app.delete(f"/api/v1/nodes/{live['node_id']}", headers=OP).status_code == 409
    sql(
        "UPDATE nodes SET last_heartbeat_at = now() - interval '5 minutes', state = 'commissioned' WHERE node_id = %s",
        (live["node_id"],),
    )
    assert app.get(f"/api/v1/nodes/{live['node_id']}", headers=OP).json()["state"] == "offline"
    assert app.delete(f"/api/v1/nodes/{live['node_id']}", headers=OP).status_code == 200
    assert _register(app, live["node_id"], live["node_api_key"]).status_code == 401


def test_rotate_key_overlap_then_expiry(app, sql):
    invited = _invite(app, node_id="rotate-box", queues=["prep"])
    old = invited["node_api_key"]
    _register(app, invited["node_id"], old, queues=["prep"])
    rotated = app.post(f"/api/v1/nodes/{invited['node_id']}/rotate-key", headers=OP)
    assert rotated.status_code == 200
    new = rotated.json()["node_api_key"]
    assert app.post(
        f"/api/v1/nodes/{invited['node_id']}/heartbeat",
        headers={"Authorization": f"Bearer {old}"},
    ).status_code == 200
    assert app.post(
        f"/api/v1/nodes/{invited['node_id']}/heartbeat",
        headers={"Authorization": f"Bearer {new}"},
    ).status_code == 200
    sql(
        "UPDATE nodes SET previous_key_expires_at = now() - interval '1 minute' WHERE node_id = %s",
        (invited["node_id"],),
    )
    assert app.post(
        f"/api/v1/nodes/{invited['node_id']}/heartbeat",
        headers={"Authorization": f"Bearer {old}"},
    ).status_code == 401
    assert app.post(
        f"/api/v1/nodes/{invited['node_id']}/heartbeat",
        headers={"Authorization": f"Bearer {new}"},
    ).status_code == 200


def test_unauthorized_worker_register_writes_nothing(app, sql):
    worker_id = str(uuid4())
    body = {
        "worker_id": worker_id,
        "node_id": "never-invited",
        "type": "prep",
        "queues": ["prep"],
    }
    assert app.post("/api/v1/workers/register", json=body).status_code == 401
    assert app.post("/api/v1/workers/register", headers=OP, json=body).status_code == 401
    assert sql("SELECT count(*) AS n FROM nodes")[0]["n"] == 0
    assert sql("SELECT count(*) AS n FROM workers")[0]["n"] == 0

    invited = _invite(app, node_id="prep-box", queues=["prep"])
    mismatch = app.post(
        "/api/v1/nodes/other-box/register",
        headers={"Authorization": f"Bearer {invited['node_api_key']}"},
        json={"queues": ["prep"], "hardware": {"cpu": 2, "ram_gb": 4, "gpu": None}, "working_hours": HOURS},
    )
    assert mismatch.status_code == 403
    assert sql("SELECT count(*) AS n FROM nodes")[0]["n"] == 1


def test_worker_register_rejected_until_node_is_up_and_not_draining(app):
    invited = _invite(app, node_id="prep-box", queues=["prep"])
    key = {"Authorization": f"Bearer {invited['node_api_key']}"}
    body = {
        "worker_id": str(uuid4()),
        "node_id": "prep-box",
        "type": "prep",
        "queues": ["prep"],
    }
    assert app.post("/api/v1/workers/register", headers=key, json=body).status_code == 409
    assert _register(app, "prep-box", invited["node_api_key"], queues=["prep"]).status_code == 200
    assert app.post(f"/api/v1/nodes/prep-box/drain", headers=OP).status_code == 200
    assert app.post("/api/v1/workers/register", headers=key, json=body).status_code == 409


def test_probe_pass_and_expiry(app, sql):
    invited = _invite(app, node_id="probe-box", queues=["prep"])
    key = {"Authorization": f"Bearer {invited['node_api_key']}"}
    assert _register(app, "probe-box", invited["node_api_key"], queues=["prep"]).status_code == 200
    created = app.post("/api/v1/nodes/probe-box/probe", headers=OP, json={"worker_type": "prep"})
    assert created.status_code == 202, created.text
    probe_id = created.json()["probe_id"]
    beat = app.post("/api/v1/nodes/probe-box/heartbeat", headers=key)
    assert beat.json()["command"] == "start_probe"
    assert beat.json()["probe_id"] == probe_id
    worker_id = str(uuid4())
    registered = app.post(
        "/api/v1/workers/register",
        headers=key,
        json={
            "worker_id": worker_id,
            "node_id": "probe-box",
            "type": "prep",
            "queues": ["prep"],
            "probe_id": probe_id,
        },
    )
    assert registered.status_code == 201, registered.text
    ok = app.post(
        f"/api/v1/workers/{worker_id}/heartbeat",
        headers=key,
        json={"status": "probe_ok", "current_job": None},
    )
    assert ok.status_code == 200
    app.delete(f"/api/v1/workers/{worker_id}", headers=key)
    listed = app.get("/api/v1/workers", headers=OP, params={"status": "online"}).json()
    assert listed["total"] == 0
    node = app.get("/api/v1/nodes/probe-box", headers=OP).json()
    assert node["last_probe"]["result"] == "passed"

    created = app.post("/api/v1/nodes/probe-box/probe", headers=OP, json={"worker_type": "prep"})
    probe_id = created.json()["probe_id"]
    sql(
        "UPDATE probes SET created_at = now() - interval '2 minutes' WHERE probe_id = %s",
        (probe_id,),
    )
    beat = app.post("/api/v1/nodes/probe-box/heartbeat", headers=key)
    assert beat.json()["command"] == "run"
    assert sql("SELECT status FROM probes WHERE probe_id = %s", (probe_id,))[0]["status"] == "expired"


def test_operator_key_on_register_is_unauthorized(app):
    invited = _invite(app, node_id="auth-box", queues=["prep"])
    response = _register(app, "auth-box", "operator-secret", queues=["prep"])
    assert response.status_code == 401
    assert app.get("/api/v1/nodes/auth-box", headers=OP).json()["state"] == "pending"
