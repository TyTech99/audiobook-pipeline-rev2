from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from psycopg import Connection

from coordinator.auth import Principal, get_conn, get_principal, require_operator
from coordinator.errors import ApiError
from coordinator.routers.schemas import WorkerHeartbeatIn, WorkerRegisterIn
from coordinator.services import workers as worker_service

router = APIRouter()


def _node_principal(principal: Principal = Depends(get_principal)) -> Principal:
    if principal.key_class != "node" or not principal.node_id:
        raise ApiError(401, "unauthorized", "node key required")
    return principal


@router.post("/register")
def register_worker(
    body: WorkerRegisterIn,
    request: Request,
    principal: Principal = Depends(_node_principal),
    conn: Connection = Depends(get_conn),
):
    payload = worker_service.register_worker(
        conn, body.model_dump(), principal.node_id, request.app.state.settings
    )
    return Response(
        content=__import__("json").dumps(payload),
        media_type="application/json",
        status_code=201,
    )


@router.post("/{worker_id}/heartbeat")
def heartbeat_worker(
    worker_id: UUID,
    body: WorkerHeartbeatIn,
    request: Request,
    principal: Principal = Depends(_node_principal),
    conn: Connection = Depends(get_conn),
):
    return worker_service.heartbeat_worker(
        conn, worker_id, body.model_dump(), principal.node_id, request.app.state.settings
    )


@router.delete("/{worker_id}")
def unregister_worker(
    worker_id: UUID,
    principal: Principal = Depends(_node_principal),
    conn: Connection = Depends(get_conn),
):
    return worker_service.unregister_worker(conn, worker_id, principal.node_id)


@router.get("")
def list_workers(
    request: Request,
    status: str | None = None,
    type: str | None = None,
    node_id: str | None = None,
    _: Principal = Depends(require_operator),
    conn: Connection = Depends(get_conn),
):
    return worker_service.list_workers(conn, request.app.state.settings, status, type, node_id)
