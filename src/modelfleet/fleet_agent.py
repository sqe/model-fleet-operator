"""Built-in agent for operating Model Fleet resources."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

import httpx

from .agent_runtime import AgentWorker, create_agent_app
from .protocol import AgentCard
from .slack import Command, FleetClient

LOG = logging.getLogger("modelfleet.fleet_agent")

CARD = AgentCard(
    name="model-fleet-operations",
    description="Inspects datasets and changes inference, training, and GPU capacity intent.",
    version="0.1.0",
    endpoint=os.getenv("FLEET_AGENT_ENDPOINT", "http://model-fleet-agent:8000"),
    kafka_topic="tasks.model-fleet-operations",
    kafka_result_topic="results.model-fleet-operations",
    max_concurrent_tasks=1,
    timeout_seconds=60,
    transports=[
        {"protocol": "kafka", "endpoint": "tasks.model-fleet-operations"},
        {"protocol": "http", "endpoint": "/v1/tasks:execute"},
        {"protocol": "grpc", "endpoint": "modelfleet.agent.v1.AgentService"},
    ],
    skills=[
        {
            "id": "fleet.status",
            "name": "Fleet status",
            "description": "List datasets, inference, training, and GPU capacity state.",
            "input_schema": {"type": "object", "properties": {"namespace": {"type": "string"}}},
        },
        {
            "id": "fleet.inference.control",
            "name": "Inference control",
            "description": "Wake, sleep, or return an InferenceService to automatic scaling.",
            "input_schema": {
                "type": "object",
                "required": ["namespace", "name", "action"],
                "properties": {"action": {"enum": ["wake", "auto", "sleep"]}},
            },
        },
        {
            "id": "fleet.training.control",
            "name": "Training control",
            "description": "Pause, resume, or cancel a TrainingRun.",
            "input_schema": {
                "type": "object",
                "required": ["namespace", "name", "action"],
                "properties": {"action": {"enum": ["pause", "resume", "cancel"]}},
            },
        },
        {
            "id": "fleet.agent.control",
            "name": "Agent control",
            "description": "Start or stop a managed AgentRegistration runtime.",
            "input_schema": {
                "type": "object",
                "required": ["namespace", "name", "action"],
                "properties": {"action": {"enum": ["wake", "sleep"]}},
            },
        },
    ],
)

_fleet: FleetClient | None = None


def _client() -> FleetClient:
    global _fleet
    if _fleet is None:
        _fleet = FleetClient(durable_commands=False)
    return _fleet


def _status(params: dict[str, Any]) -> dict[str, str]:
    return {"status": _client().status(params.get("namespace"))}


def _control(params: dict[str, Any], kind: str) -> dict[str, str]:
    action = str(params.get("action", ""))
    allowed = {
        "inference": {"wake", "auto", "sleep"},
        "training": {"pause", "resume", "cancel"},
        "agent": {"wake", "sleep"},
    }[kind]
    if action not in allowed:
        raise ValueError(f"invalid {kind} action")
    command = Command(
        action,
        kind,
        str(params["namespace"]),
        str(params["name"]),
        action in {"sleep", "cancel"} and bool(params.get("confirmed")),
    )
    if action in {"sleep", "cancel"} and not command.confirmed:
        raise ValueError("destructive action requires confirmed=true")
    return {"status": _client().apply(command, str(params.get("user_id", "agent-supervisor")))}


worker = AgentWorker(
    CARD,
    {
        "fleet.status": _status,
        "fleet.inference.control": lambda params: _control(params, "inference"),
        "fleet.training.control": lambda params: _control(params, "training"),
        "fleet.agent.control": lambda params: _control(params, "agent"),
    },
)
app = create_agent_app(CARD, worker)


def _register(registry: str) -> None:
    headers = {}
    if token := os.getenv("CONTROL_PLANE_API_KEY"):
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(1, 6):
        try:
            response = httpx.post(
                f"{registry.rstrip('/')}/registry/register",
                json=CARD.model_dump(),
                headers=headers,
                timeout=10,
            )
            response.raise_for_status()
            return
        except httpx.HTTPError:
            LOG.warning("agent registration attempt %d failed", attempt, exc_info=True)
            if attempt < 5:
                time.sleep(attempt * 2)
    LOG.error("agent registration failed after 5 attempts")


@app.on_event("startup")
def start() -> None:
    if os.getenv("AGENT_KAFKA_ENABLED", "true") == "true":
        threading.Thread(target=worker.run, daemon=True, name="fleet-agent-kafka").start()
    if registry := os.getenv("AGENT_REGISTRY_URL"):
        threading.Thread(
            target=_register, args=(registry,), daemon=True, name="fleet-agent-registration"
        ).start()
