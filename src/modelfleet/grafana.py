"""Grafana dashboard rendering for Slack snapshots."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import quote

import httpx


@dataclass(frozen=True)
class GrafanaRenderer:
    base_url: str
    token: str = ""
    dashboard_uid: str = "model-fleet-operations"
    dashboard_slug: str = "model-fleet-operations"
    timeout_seconds: float = 60

    @classmethod
    def from_env(cls) -> GrafanaRenderer | None:
        if not (base_url := os.getenv("GRAFANA_URL", "").strip()):
            return None
        return cls(
            base_url=base_url,
            token=os.getenv("GRAFANA_SERVICE_ACCOUNT_TOKEN", ""),
            dashboard_uid=os.getenv("GRAFANA_DASHBOARD_UID", "model-fleet-operations"),
            dashboard_slug=os.getenv("GRAFANA_DASHBOARD_SLUG", "model-fleet-operations"),
            timeout_seconds=float(os.getenv("GRAFANA_RENDER_TIMEOUT_SECONDS", "60")),
        )

    def render(self, namespace: str | None = None) -> bytes:
        path = "/render/d/{}/{}".format(
            quote(self.dashboard_uid, safe=""), quote(self.dashboard_slug, safe="")
        )
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        response = httpx.get(
            f"{self.base_url.rstrip('/')}{path}",
            headers=headers,
            params={
                "orgId": os.getenv("GRAFANA_ORG_ID", "1"),
                "from": os.getenv("GRAFANA_SNAPSHOT_FROM", "now-6h"),
                "to": "now",
                "width": os.getenv("GRAFANA_SNAPSHOT_WIDTH", "1800"),
                "height": os.getenv("GRAFANA_SNAPSHOT_HEIGHT", "1000"),
                "tz": "UTC",
                "var-namespace": namespace or ".*",
                "var-workload": ".*",
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        if response.headers.get("content-type", "").split(";", 1)[0] != "image/png":
            raise ValueError("Grafana renderer did not return a PNG image")
        if len(response.content) > 10 * 1024 * 1024:
            raise ValueError("Grafana dashboard image exceeds 10 MiB")
        return response.content
