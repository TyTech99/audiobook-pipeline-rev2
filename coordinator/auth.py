from dataclasses import dataclass

from fastapi import Depends, Request
from psycopg import Connection

from coordinator.errors import ApiError
from coordinator.services.rules import hash_key


@dataclass
class Principal:
    key_class: str
    node_id: str | None = None


def get_conn(request: Request):
    with request.app.state.pool.connection() as conn:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ApiError(401, "unauthorized", "missing or invalid API key")
    return token.strip()


def get_principal(request: Request, conn: Connection = Depends(get_conn)) -> Principal:
    token = _bearer(request)
    digest = hash_key(token)
    operator_hashes = request.app.state.operator_hashes
    for operator_hash in operator_hashes:
        if _digest_match(digest, operator_hash):
            return Principal(key_class="operator")
    row = conn.execute(
        """
        SELECT node_id
        FROM nodes
        WHERE key_hash = %s
           OR (previous_key_hash = %s AND previous_key_expires_at > now())
        """,
        (digest, digest),
    ).fetchone()
    if row is None:
        raise ApiError(401, "unauthorized", "missing or invalid API key")
    return Principal(key_class="node", node_id=row["node_id"])


def _digest_match(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left, right)


def require_operator(principal: Principal = Depends(get_principal)) -> Principal:
    if principal.key_class != "operator":
        raise ApiError(401, "unauthorized", "operator key required")
    return principal


def require_node_path(node_id: str, principal: Principal = Depends(get_principal)) -> Principal:
    if principal.key_class != "node":
        raise ApiError(401, "unauthorized", "node key required")
    if principal.node_id != node_id:
        raise ApiError(403, "forbidden", "node key does not match this node")
    return principal
