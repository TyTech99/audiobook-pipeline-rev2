from fastapi import APIRouter, Depends, Request
from psycopg import Connection

from coordinator.auth import get_conn
from coordinator.services.workers import count_online

router = APIRouter()


@router.get("/health")
def health(request: Request, conn: Connection = Depends(get_conn)):
    settings = request.app.state.settings
    redis_status = "connected"
    try:
        request.app.state.redis.ping()
    except Exception:
        redis_status = "error"
    s3_status = "configured" if settings.s3_endpoint else "disabled"
    online = count_online(conn, settings)
    status = "healthy" if redis_status == "connected" else "degraded"
    return {
        "status": status,
        "coordinator_id": settings.coordinator_id,
        "redis": redis_status,
        "postgres": "connected",
        "s3": s3_status,
        "workers_online": online,
        "queue_depths": {"prep": 0, "convert": 0, "failed": 0},
    }
