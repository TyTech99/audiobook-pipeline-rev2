from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from psycopg import Connection

from coordinator.auth import Principal, get_conn, require_node_path, require_operator
from coordinator.errors import ApiError
from coordinator.routers.schemas import InviteIn, PatchIn, ProbeFailIn, ProbeIn, RegisterIn
from coordinator.services import nodes as node_service

router = APIRouter()


def _settings(request: Request):
    return request.app.state.settings


@router.post("")
def invite(
    body: InviteIn,
    request: Request,
    _: Principal = Depends(require_operator),
    conn: Connection = Depends(get_conn),
):
    payload = node_service.invite(conn, body.model_dump(), _settings(request))
    return Response(
        content=_json(payload),
        media_type="application/json",
        status_code=201,
    )


@router.get("")
def list_nodes(
    request: Request,
    state: str | None = None,
    _: Principal = Depends(require_operator),
    conn: Connection = Depends(get_conn),
):
    if state and state not in {"pending", "commissioned", "offline", "draining", "decommissioned"}:
        raise ApiError(400, "invalid_request", "unknown state")
    return node_service.list_nodes(conn, _settings(request), state)


@router.get("/{node_id}")
def get_node(
    node_id: str,
    request: Request,
    _: Principal = Depends(require_operator),
    conn: Connection = Depends(get_conn),
):
    return node_service.get_node(conn, node_id, _settings(request))


@router.patch("/{node_id}")
def patch_node(
    node_id: str,
    body: PatchIn,
    request: Request,
    _: Principal = Depends(require_operator),
    conn: Connection = Depends(get_conn),
):
    return node_service.patch_node(conn, node_id, body.model_dump(), _settings(request))


@router.post("/{node_id}/register")
def register(
    node_id: str,
    body: RegisterIn,
    request: Request,
    _: Principal = Depends(require_node_path),
    conn: Connection = Depends(get_conn),
):
    return node_service.register(conn, node_id, body.model_dump(), _settings(request))


@router.post("/{node_id}/heartbeat")
def heartbeat(
    node_id: str,
    request: Request,
    _: Principal = Depends(require_node_path),
    conn: Connection = Depends(get_conn),
):
    return node_service.heartbeat(conn, node_id, _settings(request))


@router.post("/{node_id}/probe")
def probe(
    node_id: str,
    body: ProbeIn,
    request: Request,
    _: Principal = Depends(require_operator),
    conn: Connection = Depends(get_conn),
):
    payload = node_service.create_probe(conn, node_id, body.worker_type, _settings(request))
    return Response(content=_json(payload), media_type="application/json", status_code=202)


@router.post("/{node_id}/probes/{probe_id}")
def fail_probe(
    node_id: str,
    probe_id: UUID,
    body: ProbeFailIn,
    request: Request,
    _: Principal = Depends(require_node_path),
    conn: Connection = Depends(get_conn),
):
    if body.result != "failed":
        raise ApiError(400, "invalid_request", "result must be failed")
    return node_service.fail_probe(conn, node_id, probe_id, body.detail, _settings(request))


@router.post("/{node_id}/rotate-key")
def rotate_key(
    node_id: str,
    request: Request,
    _: Principal = Depends(require_operator),
    conn: Connection = Depends(get_conn),
):
    return node_service.rotate_key(conn, node_id, _settings(request))


@router.post("/{node_id}/drain")
def drain(
    node_id: str,
    request: Request,
    _: Principal = Depends(require_operator),
    conn: Connection = Depends(get_conn),
):
    return node_service.drain(conn, node_id, _settings(request))


@router.delete("/{node_id}")
def decommission(
    node_id: str,
    request: Request,
    _: Principal = Depends(require_operator),
    conn: Connection = Depends(get_conn),
):
    return node_service.decommission(conn, node_id, _settings(request))


def _json(payload: dict) -> str:
    import json

    return json.dumps(payload)
