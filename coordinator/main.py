from __future__ import annotations

import os
from contextlib import asynccontextmanager

import redis
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from coordinator.config import Settings, load_settings
from coordinator.db.postgres import apply_schema, make_pool
from coordinator.errors import ApiError
from coordinator.logging_setup import setup_logging
from coordinator.routers import health, nodes, workers
from coordinator.services.rules import hash_key as hash_key_fn


def create_app(settings: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool = make_pool(settings.postgres_dsn)
        client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password or None,
            db=settings.redis_db,
            socket_connect_timeout=2,
        )
        with pool.connection() as conn:
            apply_schema(conn)
            conn.commit()
        client.ping()
        app.state.pool = pool
        app.state.redis = client
        app.state.settings = settings
        app.state.operator_hashes = [hash_key_fn(key) for key in settings.operator_api_keys]
        try:
            yield
        finally:
            client.close()
            pool.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(nodes.router, prefix="/api/v1/nodes")
    app.include_router(workers.router, prefix="/api/v1/workers")

    @app.exception_handler(ApiError)
    def api_error(_request: Request, exc: ApiError):
        return JSONResponse(
            status_code=exc.status,
            content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
        )

    @app.exception_handler(RequestValidationError)
    def bad_request(_request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": "Malformed request body",
                    "details": {"errors": _safe_errors(exc)},
                }
            },
        )

    return app


def _safe_errors(exc: RequestValidationError) -> list:
    cleaned = []
    for item in exc.errors():
        cleaned.append({key: str(value) for key, value in item.items() if key != "input"})
    return cleaned


def main() -> None:
    path = os.environ.get("COORDINATOR_CONFIG", "config/coordinator.yaml")
    settings = load_settings(path)
    setup_logging(settings.log_level)
    import uvicorn

    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
