import os

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from coordinator.config import Settings
from coordinator.main import create_app

DSN = os.environ.get(
    "DATABASE_URL",
    "postgresql://audiobook:audiobook@127.0.0.1:5432/audiobook_test",
)


def _postgres_up() -> bool:
    try:
        with psycopg.connect(DSN, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


pytestmark_skip = pytest.mark.skipif(not _postgres_up(), reason="DATABASE_URL is not reachable")


@pytest.fixture(scope="session")
def settings():
    if not _postgres_up():
        pytest.skip("DATABASE_URL is not reachable")
    return Settings(
        coordinator_id="coord-test",
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


@pytest.fixture(scope="session")
def app(settings):
    application = create_app(settings)
    with TestClient(application) as client:
        yield client


@pytest.fixture
def clean_db(app):
    pool = app.app.state.pool
    redis_client = app.app.state.redis
    with pool.connection() as conn:
        conn.execute("TRUNCATE jobs, nodes, workers, probes CASCADE")
        conn.commit()
    redis_client.flushdb()
    yield


@pytest.fixture
def sql(app):
    pool = app.app.state.pool

    def run(query, args=()):
        with pool.connection() as conn:
            conn.row_factory = dict_row
            cur = conn.execute(query, args)
            rows = cur.fetchall() if cur.description else None
            conn.commit()
            return rows

    return run
