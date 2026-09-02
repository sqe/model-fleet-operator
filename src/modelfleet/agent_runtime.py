"""Reusable Python runtime for Model Fleet agents."""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from typing import Any

from fastapi import Depends, FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from .control_auth import require_control_token
from .protocol import AgentCard, JsonRpcResult, JsonRpcTask
from .tracing import TaskTracer

TASKS = Counter("model_fleet_agent_tasks_total", "Agent tasks", ["agent", "skill", "status"])
LATENCY = Histogram(
    "model_fleet_agent_task_duration_seconds", "Agent task latency", ["agent", "skill"]
)
ACTIVE = Gauge("model_fleet_agent_active_tasks", "Active agent tasks", ["agent"])
LOG = logging.getLogger("modelfleet.agent")


def create_agent_app(card: AgentCard, worker: AgentWorker | None = None) -> FastAPI:
    app = FastAPI(title=card.name)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "healthy", "agent": card.name, "version": card.version}

    @app.get("/.well-known/agent.json")
    def agent_card() -> dict[str, Any]:
        return card.model_dump()

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    if worker:

        @app.post("/v1/tasks:execute", dependencies=[Depends(require_control_token)])
        def execute(task: JsonRpcTask) -> JsonRpcResult:
            return worker.process(task.model_dump_json().encode())

    return app


class AgentWorker:
    """Manual-commit Kafka worker with replay-safe result publication."""

    def __init__(
        self,
        card: AgentCard,
        handlers: dict[str, Callable[[dict[str, Any]], Any]],
        *,
        tracer: TaskTracer | None = None,
    ) -> None:
        self.card, self.handlers = card, handlers
        self.tracer = tracer or TaskTracer()

    def process(self, payload: bytes) -> JsonRpcResult:
        task = JsonRpcTask.model_validate_json(payload)
        skill = str(task.params.get("skill", ""))
        if skill not in self.handlers:
            return JsonRpcResult(id=task.id, error={"code": -32601, "message": "unknown skill"})
        started = time.monotonic()
        ACTIVE.labels(self.card.name).inc()
        try:
            with self.tracer.trace(task.id, self.card.name, skill):
                result = self.handlers[skill](task.params)
            TASKS.labels(self.card.name, skill, "completed").inc()
            return JsonRpcResult(id=task.id, result=result)
        except Exception as error:
            TASKS.labels(self.card.name, skill, "failed").inc()
            LOG.exception("agent task %s failed", task.id)
            return JsonRpcResult(id=task.id, error={"code": -32000, "message": str(error)[:300]})
        finally:
            ACTIVE.labels(self.card.name).dec()
            LATENCY.labels(self.card.name, skill).observe(time.monotonic() - started)

    def run(self) -> None:  # pragma: no cover - long-running process wiring
        from confluent_kafka import Consumer, Producer

        brokers = os.environ["KAFKA_BOOTSTRAP_SERVERS"]
        producer = Producer(
            {"bootstrap.servers": brokers, "acks": "all", "enable.idempotence": True}
        )
        consumer = Consumer(
            {
                "bootstrap.servers": brokers,
                "group.id": os.getenv("AGENT_CONSUMER_GROUP", self.card.name),
                "enable.auto.commit": False,
                "auto.offset.reset": "earliest",
            }
        )
        consumer.subscribe([self.card.kafka_topic])
        try:
            while True:
                message = consumer.poll(1)
                if message is None or message.error():
                    continue
                result = self.process(message.value())
                producer.produce(
                    self.card.kafka_result_topic,
                    key=result.id,
                    value=result.model_dump_json(exclude_none=True).encode(),
                )
                if producer.flush(15):
                    raise RuntimeError("Kafka result publication timed out")
                consumer.commit(message=message, asynchronous=False)
        finally:
            consumer.close()
            producer.flush(5)


def load_card(path: str) -> AgentCard:
    with open(path, encoding="utf-8") as stream:
        return AgentCard.model_validate(json.load(stream))
