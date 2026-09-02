"""Task routing and durable Kafka dispatch service."""

import json
import logging
import os
import threading
import uuid
from typing import Any

import httpx
from confluent_kafka import Producer
from fastapi import Depends, FastAPI, HTTPException, Response
from kubernetes import client, config
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from pydantic import BaseModel, ConfigDict, ValidationError

from .control_auth import require_control_token
from .protocol import JsonRpcTask, TaskSubmission
from .tracing import TaskTracer

GROUP, VERSION, TASK_PLURAL = "fleet.sqe.io", "v1alpha1", "agenttasks"
TASKS = Counter("model_fleet_supervisor_tasks_total", "Supervisor tasks", ["status"])
LOG = logging.getLogger("modelfleet.supervisor")


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent: str
    skill: str


def exact_skill(cards: list[dict[str, Any]], skill_id: str) -> Selection | None:
    matches = [
        Selection(agent=card["id"], skill=skill_id)
        for card in cards
        if any(skill["id"] == skill_id for skill in card["skills"])
    ]
    return sorted(matches, key=lambda value: value.agent)[0] if matches else None


def validate_selection(raw: str, cards: list[dict[str, Any]]) -> Selection:
    try:
        selection = Selection.model_validate_json(raw)
    except ValidationError as exc:
        raise ValueError("gateway returned an invalid routing decision") from exc
    card = next((item for item in cards if item["id"] == selection.agent), None)
    if card is None or not any(skill["id"] == selection.skill for skill in card["skills"]):
        raise ValueError("gateway selected an unavailable agent/skill pair")
    return selection


class Supervisor:
    def __init__(
        self,
        api: Any,
        producer: Any,
        http: httpx.AsyncClient,
        namespace: str,
        registry_url: str,
        gateway_url: str,
        gateway_model: str,
        tracer: TaskTracer | None = None,
    ) -> None:
        self.api, self.producer, self.http = api, producer, http
        self.namespace, self.registry_url, self.gateway_url = namespace, registry_url, gateway_url
        self.gateway_model, self.tracer = gateway_model, tracer or TaskTracer()

    async def cards(self) -> list[dict[str, Any]]:
        response = await self.http.get(
            f"{self.registry_url.rstrip('/')}/registry/agents",
            headers=self._control_headers(),
        )
        response.raise_for_status()
        return response.json()

    async def select(self, submission: TaskSubmission, cards: list[dict[str, Any]]) -> Selection:
        if submission.skill:
            chosen = exact_skill(cards, submission.skill)
            if chosen:
                return chosen
            raise ValueError("requested skill is unavailable")
        available = [
            {"agent": card["id"], "skill": skill["id"]}
            for card in cards
            for skill in card["skills"]
        ]
        if not available:
            raise ValueError("no agent skills are registered")
        response = await self.http.post(
            f"{self.gateway_url.rstrip('/')}/v1/chat/completions",
            headers=self._control_headers(),
            json={
                "model": self.gateway_model,
                "temperature": 0,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Choose exactly one available route. "
                            "Return only JSON with agent and skill."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"prompt": submission.prompt, "available": available}
                        ),
                    },
                ],
            },
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return validate_selection(content, cards)

    @staticmethod
    def _control_headers() -> dict[str, str]:
        token = os.getenv("CONTROL_PLANE_API_KEY")
        return {"Authorization": f"Bearer {token}"} if token else {}

    async def submit(self, submission: TaskSubmission) -> dict[str, Any]:
        cards = await self.cards()
        selection = await self.select(submission, cards)
        card = next(card for card in cards if card["id"] == selection.agent)
        task_id = str(uuid.uuid4())
        rpc = JsonRpcTask(
            id=task_id,
            params={
                "user_id": submission.user_id,
                "prompt": submission.prompt,
                "context": submission.context,
                "skill": selection.skill,
            },
        )
        body = {
            "apiVersion": f"{GROUP}/{VERSION}",
            "kind": "AgentTask",
            "metadata": {"name": task_id},
            "spec": {
                "agent": selection.agent,
                "skill": selection.skill,
            },
            "status": {"phase": "pending"},
        }
        with self.tracer.trace(task_id, selection.agent, selection.skill):
            self.api.create_namespaced_custom_object(
                GROUP, VERSION, self.namespace, TASK_PLURAL, body
            )
            self.producer.produce(
                card["kafka_topic"], key=task_id, value=rpc.model_dump_json().encode()
            )
            if self.producer.flush(15):
                raise RuntimeError("Kafka task publication timed out")
            self.api.patch_namespaced_custom_object_status(
                GROUP,
                VERSION,
                self.namespace,
                TASK_PLURAL,
                task_id,
                {"status": {"phase": "routed"}},
            )
        TASKS.labels("routed").inc()
        return {
            "id": task_id,
            "agent": selection.agent,
            "skill": selection.skill,
            "status": "routed",
        }

    def get(self, task_id: str) -> dict[str, Any]:
        return self.api.get_namespaced_custom_object(
            GROUP, VERSION, self.namespace, TASK_PLURAL, task_id
        )


def default_supervisor() -> Supervisor:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    producer = Producer(
        {
            "bootstrap.servers": os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            "enable.idempotence": True,
            "acks": "all",
        }
    )
    return Supervisor(
        client.CustomObjectsApi(),
        producer,
        httpx.AsyncClient(timeout=30),
        os.getenv("POD_NAMESPACE", "default"),
        os.getenv("AGENT_REGISTRY_URL", "http://agent-registry"),
        os.getenv("LLM_GATEWAY_URL", "http://llm-gateway"),
        os.getenv("SUPERVISOR_MODEL", "router"),
    )


def apply_result(api: Any, namespace: str, payload: bytes) -> str:
    """Validate a JSON-RPC result and durably mark its AgentTask complete."""
    from .protocol import JsonRpcResult

    result = JsonRpcResult.model_validate_json(payload)
    phase = "failed" if result.error else "completed"
    message = (
        str(result.error.get("message", "agent task failed"))
        if result.error
        else "result published to Kafka"
    )
    api.patch_namespaced_custom_object_status(
        GROUP,
        VERSION,
        namespace,
        TASK_PLURAL,
        result.id,
        {"status": {"phase": phase, "message": message}},
    )
    TASKS.labels(phase).inc()
    return result.id


def consume_results() -> None:  # pragma: no cover - long-running process wiring
    from confluent_kafka import Consumer, KafkaError

    namespace = os.getenv("POD_NAMESPACE", "default")
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    consumer = Consumer(
        {
            "bootstrap.servers": os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            "group.id": os.getenv("AGENT_RESULT_CONSUMER_GROUP", "model-fleet-supervisor-v1"),
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
        }
    )
    consumer.subscribe([os.getenv("AGENT_RESULT_TOPIC_PATTERN", "^results\\..+")])
    api = client.CustomObjectsApi()
    try:
        while True:
            message = consumer.poll(1)
            if message is None:
                continue
            if message.error():
                if message.error().code() == KafkaError._PARTITION_EOF:
                    continue
                raise RuntimeError(str(message.error()))
            try:
                apply_result(api, namespace, message.value())
            except client.ApiException as exc:
                if exc.status != 404:
                    raise
                LOG.debug("Ignoring result for a task owned by another supervisor")
            consumer.commit(message=message, asynchronous=False)
    finally:
        consumer.close()


def create_app(supervisor: Supervisor | None = None) -> FastAPI:
    app = FastAPI(title="Model Fleet supervisor")
    service = supervisor

    def get_service() -> Supervisor:
        nonlocal service
        service = service or default_supervisor()
        return service

    @app.on_event("startup")
    def start_result_consumer() -> None:
        if supervisor is None and os.getenv("SUPERVISOR_RESULTS_ENABLED", "true") == "true":
            threading.Thread(target=consume_results, daemon=True, name="agent-results").start()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/tasks/submit", dependencies=[Depends(require_control_token)])
    async def submit(submission: TaskSubmission) -> dict[str, Any]:
        try:
            return await get_service().submit(submission)
        except (ValueError, httpx.HTTPError, KeyError, IndexError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/tasks/{task_id}", dependencies=[Depends(require_control_token)])
    def task(task_id: str) -> dict[str, Any]:
        try:
            return get_service().get(task_id)
        except client.ApiException as exc:
            if exc.status == 404:
                raise HTTPException(404, "task not found") from exc
            raise

    return app


app = create_app()
