from __future__ import annotations

import argparse
import json
import sys

from cli.flows import (
    apply_coordinator,
    apply_node,
    install_coordinator,
    install_node,
    install_standalone,
    invite_node,
    uninstall_coordinator,
    uninstall_node,
    update_role,
)
from cli.realhost import detect_host
from cli.state import load_state


class LiveApi:
    """HTTP API used when an agent runs the CLI for real."""

    def health(self, url: str) -> dict:
        import httpx

        from cli.policy import api_root

        response = httpx.get(api_root(url) + "/health", timeout=10)
        response.raise_for_status()
        return response.json()

    def invite(self, url: str, operator_key: str, body: dict) -> dict:
        import httpx

        response = httpx.post(
            url.rstrip("/") + "/nodes",
            headers={"Authorization": f"Bearer {operator_key}"},
            json={
                "node_id": body["node_id"],
                "display_name": body.get("display_name") or body["node_id"],
                "allowed_queues": body["allowed_queues"],
                "working_hours": body["working_hours"],
            },
            timeout=10,
        )
        if response.status_code == 409:
            # Node already exists — return a sentinel indicating no new key was issued.
            # The caller should use the existing key from the node's env file.
            return {"node_api_key": "__EXISTS__", "already_exists": True}
        response.raise_for_status()
        return response.json()

    def patch_node(self, url: str, operator_key: str, node_id: str, body: dict) -> dict:
        import httpx

        response = httpx.patch(
            url.rstrip("/") + f"/nodes/{node_id}",
            headers={"Authorization": f"Bearer {operator_key}"},
            json=body,
            timeout=10,
        )
        response.raise_for_status()
        return response.json()

    def drain(self, url: str, operator_key: str, node_id: str) -> None:
        import httpx

        httpx.post(
            url.rstrip("/") + f"/nodes/{node_id}/drain",
            headers={"Authorization": f"Bearer {operator_key}"},
            timeout=10,
        ).raise_for_status()

    def delete(self, url: str, operator_key: str, node_id: str) -> None:
        import httpx

        httpx.delete(
            url.rstrip("/") + f"/nodes/{node_id}",
            headers={"Authorization": f"Bearer {operator_key}"},
            timeout=10,
        ).raise_for_status()


def _emit(messages: list[str], payload: dict, as_json: bool) -> int:
    if as_json:
        print(json.dumps({"messages": messages, **payload}))
    else:
        for line in messages:
            print(line)
    return int(payload.get("code", 0))


def main(argv: list[str] | None = None, host=None, api=None) -> int:
    parser = argparse.ArgumentParser(prog="audiobookctl")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="group", required=True)

    coordinator = sub.add_parser("coordinator")
    csub = coordinator.add_subparsers(dest="action", required=True)
    for name in ("install", "apply", "uninstall"):
        cmd = csub.add_parser(name)
        if name != "uninstall":
            cmd.add_argument("--state", required=True)
            cmd.add_argument("--force", action="store_true", help="Force redeploy even if unchanged")
        else:
            cmd.add_argument("--purge", action="store_true")
    update = csub.add_parser("update")
    update.add_argument("--source", required=True)

    node = sub.add_parser("node")
    nsub = node.add_subparsers(dest="action", required=True)
    for name in ("install", "apply"):
        cmd = nsub.add_parser(name)
        cmd.add_argument("--state", required=True)
        cmd.add_argument("--force", action="store_true", help="Force redeploy even if unchanged")
    nupdate = nsub.add_parser("update")
    nupdate.add_argument("--source", required=True)
    nun = nsub.add_parser("uninstall")
    nun.add_argument("--state", required=True)
    nun.add_argument("--purge", action="store_true")
    nun.add_argument("--local-only", action="store_true")
    invite = nsub.add_parser("invite")
    invite.add_argument("--coordinator-url", required=True)
    invite.add_argument("--operator-api-key", required=True)
    invite.add_argument("--node-id", required=True)
    invite.add_argument("--queues", default="prep")
    invite.add_argument("--redis-host", required=True)
    invite.add_argument("--redis-port", type=int, default=6379)
    invite.add_argument("--redis-password", default="")

    standalone = sub.add_parser("standalone")
    ssub = standalone.add_subparsers(dest="action", required=True)
    sinstall = ssub.add_parser("install")
    sinstall.add_argument("--state", required=True)
    sinstall.add_argument("--force", action="store_true", help="Force redeploy even if unchanged")

    args = parser.parse_args(argv)
    host = host if host is not None else detect_host()
    api = api if api is not None else LiveApi()
    messages: list[str] = []

    if args.group == "coordinator" and args.action == "install":
        payload = install_coordinator(host, load_state(args.state), messages, force=args.force)
    elif args.group == "coordinator" and args.action == "apply":
        payload = apply_coordinator(host, load_state(args.state), messages, force=args.force)
    elif args.group == "coordinator" and args.action == "update":
        payload = update_role(host, args.source, "coordinator", messages)
    elif args.group == "coordinator" and args.action == "uninstall":
        payload = uninstall_coordinator(host, args.purge, messages)
    elif args.group == "node" and args.action == "install":
        payload = install_node(host, load_state(args.state), api, messages, force=args.force)
    elif args.group == "node" and args.action == "apply":
        payload = apply_node(host, load_state(args.state), api, messages, force=args.force)
    elif args.group == "node" and args.action == "update":
        payload = update_role(host, args.source, "node", messages)
    elif args.group == "node" and args.action == "uninstall":
        payload = uninstall_node(host, load_state(args.state), api, args.purge, args.local_only, messages)
    elif args.group == "node" and args.action == "invite":
        try:
            payload = invite_node(
                api,
                args.coordinator_url,
                args.operator_api_key,
                args.node_id,
                [item.strip() for item in args.queues.split(",") if item.strip()],
                args.redis_host,
                args.redis_port,
                args.redis_password,
            )
            payload["code"] = 0
        except Exception as exc:
            messages.append(str(exc))
            payload = {"code": 3}
    elif args.group == "standalone" and args.action == "install":
        payload = install_standalone(host, load_state(args.state), api, messages, force=args.force)
    else:
        payload = {"code": 2}
        messages.append("unsupported command")
    return _emit(messages, payload, args.json)


if __name__ == "__main__":
    sys.exit(main())
