from __future__ import annotations

import ipaddress
from urllib.parse import urlparse


class PolicyError(Exception):
    pass


def _host_is_private(host: str) -> bool:
    if host in {"localhost"}:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local)


def validate_api_url(url: str) -> None:
    parsed = urlparse(url)
    host = parsed.hostname
    if not host or parsed.scheme not in {"http", "https"}:
        raise PolicyError("coordinator URL must be http or https with a host")
    if parsed.scheme == "https":
        return
    if not _host_is_private(host):
        raise PolicyError("http is only allowed for localhost and private addresses")


def validate_redis_advertise(redis_host: str, api_url: str) -> None:
    api_host = urlparse(api_url).hostname or ""
    if redis_host == api_host and not _host_is_private(api_host):
        raise PolicyError("Redis must not be published on the public coordinator name")


def api_root(url: str) -> str:
    trimmed = url.rstrip("/")
    if trimmed.endswith("/api/v1"):
        return trimmed[: -len("/api/v1")]
    return trimmed
