from __future__ import annotations

import httpx

from supervisor.config import SupervisorSettings


class IdentityMismatch(Exception):
    pass


class CoordinatorClient:
    """Talks to the coordinator. Refuses to send the node key to the wrong id."""

    def __init__(self, settings: SupervisorSettings, http: httpx.Client | None = None):
        self.settings = settings
        self._http = http or httpx.Client(verify=self._verify(), timeout=10)
        self._owns = http is None

    def _verify(self):
        if self.settings.ca_file:
            return self.settings.ca_file
        return True

    def close(self) -> None:
        if self._owns:
            self._http.close()

    def _url(self, path: str) -> str:
        base = self.settings.coordinator_url
        # coordinator_url may already end at /api/v1. Health is on the root.
        root = base[: -len("/api/v1")] if base.endswith("/api/v1") else base
        if path.startswith("/health"):
            return root + path
        return base + path

    def check_identity(self) -> str:
        response = self._http.get(self._url("/health"))
        response.raise_for_status()
        found = response.json().get("coordinator_id")
        expected = self.settings.coordinator_id
        if expected and found != expected:
            raise IdentityMismatch(f"coordinator_id {found} != {expected}")
        return found

    def _auth(self) -> dict:
        self.check_identity()
        return {"Authorization": f"Bearer {self.settings.api_key}"}

    def register(self, hardware: dict) -> dict:
        response = self._http.post(
            self._url(f"/nodes/{self.settings.node_id}/register"),
            headers=self._auth(),
            json={
                "queues": self.settings.queues,
                "hardware": hardware,
                "working_hours": self.settings.working_hours,
            },
        )
        response.raise_for_status()
        return response.json()

    def heartbeat(self) -> dict:
        response = self._http.post(
            self._url(f"/nodes/{self.settings.node_id}/heartbeat"),
            headers=self._auth(),
        )
        response.raise_for_status()
        return response.json()

    def fail_probe(self, probe_id: str, detail: str) -> None:
        response = self._http.post(
            self._url(f"/nodes/{self.settings.node_id}/probes/{probe_id}"),
            headers=self._auth(),
            json={"result": "failed", "detail": detail},
        )
        response.raise_for_status()
