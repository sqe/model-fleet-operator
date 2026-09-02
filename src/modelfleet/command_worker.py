"""Apply durable Kafka commands to Model Fleet custom resources."""

from __future__ import annotations

import json
import logging
import os
import random
import signal
import time
from typing import Any

from kubernetes.client import ApiException

from modelfleet.kafka import KafkaConfig, Publisher, event_payload, validate_command
from modelfleet.metrics import COMMANDS
from modelfleet.slack import Command, FleetClient

LOG = logging.getLogger("modelfleet.command-worker")
RUNNING = True


def _stop(_signum: int, _frame: Any) -> None:
    global RUNNING
    RUNNING = False


def process(payload: dict[str, Any], fleet: FleetClient) -> dict[str, Any]:
    params = validate_command(payload)
    command = Command(
        params["verb"],
        params["kind"],
        params["namespace"],
        params["name"],
        bool(params.get("confirmed")),
    )
    fleet.apply(command, params["actor"], params["event_id"])
    return params


def main() -> None:  # pragma: no cover - process wiring
    from confluent_kafka import Consumer, KafkaError

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s"
    )
    config = KafkaConfig.from_env()
    if not config.enabled:
        raise RuntimeError("command worker requires KAFKA_ENABLED=true")
    publisher = Publisher(config)
    consumer = Consumer(
        {
            **config.client_config(),
            "group.id": config.consumer_group,
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
            "client.id": "model-fleet-command-worker",
        }
    )
    fleet = FleetClient(durable_commands=False)
    consumer.subscribe([config.command_topic])
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    try:
        while RUNNING:
            message = consumer.poll(1.0)
            if message is None:
                continue
            if message.error():
                if message.error().code() == KafkaError._PARTITION_EOF:
                    continue
                raise RuntimeError(str(message.error()))
            payload: dict[str, Any] | None = None
            try:
                payload = json.loads(message.value())
                params = validate_command(payload)
            except (ValueError, TypeError, json.JSONDecodeError) as command_error:
                publisher.send(
                    config.dlq_topic,
                    {
                        "command": payload,
                        "error": str(command_error)[:300],
                        "dead_lettered_at": time.time(),
                    },
                    str((payload or {}).get("id", "invalid")),
                )
                LOG.warning("dead-lettered invalid command: %s", command_error)
                COMMANDS.labels("invalid").inc()
                consumer.commit(message=message, asynchronous=False)
                continue

            terminal_error = ""
            for attempt in range(1, config.max_attempts + 1):
                try:
                    params = process(payload, fleet)
                    break
                except ApiException as api_error:
                    terminal_error = f"Kubernetes HTTP {api_error.status}: {api_error.reason}"
                    if api_error.status in {400, 403, 404, 422} or attempt == config.max_attempts:
                        break
                    time.sleep(min(30, 2**attempt + random.random()))
            if terminal_error:
                publisher.send(
                    config.dlq_topic,
                    {
                        "command": payload,
                        "error": terminal_error[:300],
                        "dead_lettered_at": time.time(),
                    },
                    str(params["event_id"]),
                )
                LOG.warning(
                    "dead-lettered rejected command %s: %s",
                    params["event_id"],
                    terminal_error,
                )
                COMMANDS.labels("rejected").inc()
            else:
                # A failed event publication exits without committing. Kafka then
                # replays the idempotent Kubernetes patch on the next worker.
                publisher.send(
                    config.event_topic,
                    event_payload(params, "applied"),
                    str(params["event_id"]),
                )
                LOG.info("applied command %s", params["event_id"])
                COMMANDS.labels("applied").inc()
            # Commit only after the Kubernetes effect and event, or durable DLQ publication.
            consumer.commit(message=message, asynchronous=False)
    finally:
        consumer.close()


if __name__ == "__main__":
    main()
