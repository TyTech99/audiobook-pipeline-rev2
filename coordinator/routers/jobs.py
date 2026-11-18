from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from psycopg import Connection

from coordinator.auth import Principal, get_conn, get_principal, require_operator
from coordinator.errors import ApiError
from coordinator.routers.schemas import JobCompleteIn, JobProgressIn, JobStartIn, JobSubmitIn
from coordinator.services import jobs as job_service

router = APIRouter()


def _node_principal(principal: Principal = Depends(get_principal)) -> Principal:
    if principal.key_class != "node" or not principal.node_id:
        raise ApiError(401, "unauthorized", "node key required")
    return principal


@router.post("/upload")
def upload_file(
    file: UploadFile = File(...),
    _: Principal = Depends(require_operator),
):
    """Upload a source document (PDF/EPUB) and get back an input_url for job submission."""
    import os
    import shutil

    root = os.environ.get("STORAGE_ROOT", "/var/lib/audiobook")
    input_dir = os.path.join(root, "input")
    os.makedirs(input_dir, exist_ok=True)

    # Sanitize filename and prepend a UUID to avoid collisions
    safe_name = os.path.basename(file.filename or "upload")
    dest_name = f"{uuid4().hex}-{safe_name}"
    dest_path = os.path.join(input_dir, dest_name)

    with open(dest_path, "wb") as out:
        shutil.copyfileobj(file.file, out)

    input_url = f"file://{dest_path}"
    return {"input_url": input_url, "filename": safe_name, "size": os.path.getsize(dest_path)}


@router.post("")
def submit_job(
    body: JobSubmitIn,
    request: Request,
    _: Principal = Depends(require_operator),
    conn: Connection = Depends(get_conn),
):
    payload = job_service.submit_job(conn, body.model_dump(), request.app.state.settings, request.app.state.redis)
    return Response(
        content=__import__("json").dumps(payload),
        media_type="application/json",
        status_code=201,
    )


@router.get("")
def list_jobs(
    request: Request,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    customer_id: str | None = None,
    _: Principal = Depends(require_operator),
    conn: Connection = Depends(get_conn),
):
    return job_service.list_jobs(conn, status, limit, offset, customer_id)


@router.get("/{job_id}")
def get_job(
    job_id: UUID,
    _: Principal = Depends(get_principal),
    conn: Connection = Depends(get_conn),
):
    return job_service.get_job(conn, job_id)


@router.post("/{job_id}/progress")
def report_progress(
    job_id: UUID,
    body: JobProgressIn,
    principal: Principal = Depends(_node_principal),
    conn: Connection = Depends(get_conn),
):
    return job_service.report_progress(conn, job_id, body.model_dump(), principal.node_id)


@router.post("/{job_id}/start")
def start_job(
    job_id: UUID,
    body: JobStartIn,
    principal: Principal = Depends(_node_principal),
    conn: Connection = Depends(get_conn),
):
    return job_service.mark_in_progress(conn, job_id, body.worker_id, body.stage)


def _any_principal(principal: Principal = Depends(get_principal)) -> Principal:
    return principal


@router.post("/{job_id}/complete")
def complete_job(
    job_id: UUID,
    body: JobCompleteIn,
    request: Request,
    principal: Principal = Depends(_any_principal),
    conn: Connection = Depends(get_conn),
):
    worker_id = principal.node_id or "operator"
    return job_service.complete_job(conn, job_id, body.model_dump(), worker_id, request.app.state.redis)


@router.post("/{job_id}/fail")
def fail_job(
    job_id: UUID,
    body: JobCompleteIn,
    request: Request,
    principal: Principal = Depends(_any_principal),
    conn: Connection = Depends(get_conn),
):
    worker_id = principal.node_id or "operator"
    return job_service.fail_job(
        conn, job_id, body.model_dump(), worker_id, request.app.state.redis, request.app.state.settings
    )


@router.post("/{job_id}/cancel")
def cancel_job(
    job_id: UUID,
    request: Request,
    _: Principal = Depends(require_operator),
    conn: Connection = Depends(get_conn),
):
    return job_service.cancel_job(conn, job_id, request.app.state.redis)
