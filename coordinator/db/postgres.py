from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def make_pool(dsn: str) -> ConnectionPool:
    return ConnectionPool(
        conninfo=dsn,
        min_size=1,
        max_size=8,
        kwargs={"row_factory": dict_row},
        open=True,
    )


def apply_schema(conn: psycopg.Connection) -> None:
    conn.execute(SCHEMA_PATH.read_text())
