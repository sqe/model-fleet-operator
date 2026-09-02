"""Kubernetes-backed agent registry HTTP service."""

import os
import re
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Response
from kubernetes import client, config
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

from .control_auth import require_control_token
from .protocol import AgentCard

GROUP, VERSION, PLURAL = "fleet.sqe.io", "v1alpha1", "agentregistrations"
REGISTRATIONS = Counter(
    "model_fleet_registry_operations_total", "Registry operations", ["operation"]
)


def _resource_name(name: str) -> str:
    value = re.sub(r"[^a-z0-9.-]+", "-", name.lower()).strip("-.")
    if not value:
        raise ValueError("agent name cannot form a Kubernetes resource name")
    return value[:253]


def _card(resource: dict[str, Any]) -> dict[str, Any]:
    return AgentCard.model_validate(resource["spec"]).model_dump()


class KubernetesRegistry:
    def __init__(self, api: Any, namespace: str) -> None:
        self.api, self.namespace = api, namespace

    def register(self, card: AgentCard) -> dict[str, Any]:
        name = _resource_name(card.name)
        body = {
            "apiVersion": f"{GROUP}/{VERSION}",
            "kind": "AgentRegistration",
            "metadata": {"name": name},
            "spec": card.model_dump(),
        }
        try:
            obj = self.api.create_namespaced_custom_object(
                GROUP, VERSION, self.namespace, PLURAL, body
            )
        except client.ApiException as exc:
            if exc.status != 409:
                raise
            obj = self.api.patch_namespaced_custom_object(
                GROUP, VERSION, self.namespace, PLURAL, name, body
            )
        REGISTRATIONS.labels("register").inc()
        return {"id": name, **_card(obj)}

    def list(self) -> list[dict[str, Any]]:
        objects = self.api.list_namespaced_custom_object(
            GROUP, VERSION, self.namespace, PLURAL
        ).get("items", [])
        return sorted(
            ({"id": item["metadata"]["name"], **_card(item)} for item in objects),
            key=lambda item: item["id"],
        )

    def get(self, agent_id: str) -> dict[str, Any]:
        try:
            obj = self.api.get_namespaced_custom_object(
                GROUP, VERSION, self.namespace, PLURAL, agent_id
            )
        except client.ApiException as exc:
            if exc.status == 404:
                raise KeyError(agent_id) from exc
            raise
        return {"id": agent_id, **_card(obj)}

    def search(self, query: str) -> list[dict[str, Any]]:
        terms = set(re.findall(r"[a-z0-9]+", query.lower()))
        ranked = []
        for item in self.list():
            searchable = " ".join(
                f"{skill['id']} {skill['name']} {skill['description']}" for skill in item["skills"]
            ).lower()
            score = sum(term in searchable for term in terms)
            if score:
                ranked.append((score, item["id"], item))
        return [item for _, _, item in sorted(ranked, key=lambda row: (-row[0], row[1]))]


def default_registry() -> KubernetesRegistry:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    return KubernetesRegistry(client.CustomObjectsApi(), os.getenv("POD_NAMESPACE", "default"))


def create_app(registry: KubernetesRegistry | None = None) -> FastAPI:
    app = FastAPI(title="Model Fleet agent registry")
    get_registry = lambda: registry or default_registry()  # noqa: E731

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/registry/register", dependencies=[Depends(require_control_token)])
    def register(card: AgentCard) -> dict[str, Any]:
        return get_registry().register(card)

    @app.get("/registry/agents", dependencies=[Depends(require_control_token)])
    def agents() -> list[dict[str, Any]]:
        return get_registry().list()

    @app.get("/registry/agents/{agent_id}", dependencies=[Depends(require_control_token)])
    def agent(agent_id: str) -> dict[str, Any]:
        try:
            return get_registry().get(agent_id)
        except KeyError as exc:
            raise HTTPException(404, "agent not found") from exc

    @app.get("/registry/search", dependencies=[Depends(require_control_token)])
    def search(query: str) -> list[dict[str, Any]]:
        return get_registry().search(query)

    return app


app = create_app()
