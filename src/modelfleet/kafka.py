"""Kafka transport for durable Model Fleet control commands."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

METHOD = "model-fleet.command.requested"
SCHEMA_VERSION = 1
VALID_VERBS = {"wake", "auto", "sleep", "pause", "resume", "cancel"}
VALID_KINDS = {"agent", "inference", "training"}


@dataclass(frozen=True)
class KafkaConfig:
    enabled: bool
    bootstrap_servers: str
    command_topic: str
    event_topic: str
    dlq_topic: str
    consumer_group: str
    max_attempts: int

    @classmethod
    def from_env(cls) -> KafkaConfig:
        config = cls(
            enabled=os.getenv("KAFKA_ENABLED", "false").lower() == "true",
            bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "").strip(),
            command_topic=os.getenv("KAFKA_COMMAND_TOPIC", "model-fleet.commands.v1").strip(),
            event_topic=os.getenv("KAFKA_EVENT_TOPIC", "model-fleet.events.v1").strip(),
            dlq_topic=os.getenv("KAFKA_DLQ_TOPIC", "model-fleet.commands.dlq.v1").strip(),
            consumer_group=os.getenv("KAFKA_CONSUMER_GROUP", "model-fleet-controller-v1").strip(),
            max_attempts=max(1, int(os.getenv("KAFKA_MAX_ATTEMPTS", "3"))),
        )
        if config.enabled and (
            not config.bootstrap_servers
            or not config.consumer_group
            or len({config.command_topic, config.event_topic, config.dlq_topic}) != 3
        ):
            raise RuntimeError("invalid Model Fleet Kafka configuration")
        return config

    def client_config(self) -> dict[str, Any]:
        result: dict[str, Any] = {"bootstrap.servers": self.bootstrap_servers}
        optional = {
            "security.protocol": "KAFKA_SECURITY_PROTOCOL",
            "sasl.mechanism": "KAFKA_SASL_MECHANISM",
            "sasl.username": "KAFKA_SASL_USERNAME",
            "sasl.password": "KAFKA_SASL_PASSWORD",
            "ssl.ca.location": "KAFKA_SSL_CA_LOCATION",
        }
        for key, environment_name in optional.items():
            if value := os.getenv(environment_name):
                result[key] = value
        return result


def new_command(command: Any, actor: str) -> dict[str, Any]:
    event_id = uuid.uuid4().hex
    return {
        "jsonrpc": "2.0",
        "method": METHOD,
        "id": event_id,
        "params": {
            "schema_version": SCHEMA_VERSION,
            "event_id": event_id,
            "created_at": datetime.now(UTC).isoformat(),
            "actor": actor[:80],
            "verb": command.verb,
            "kind": command.kind,
            "namespace": command.namespace,
            "name": command.name,
            "confirmed": command.confirmed,
        },
    }


def validate_command(payload: Mapping[str, Any]) -> dict[str, Any]:
    params = payload.get("params")
    if payload.get("jsonrpc") != "2.0" or payload.get("method") != METHOD:
        raise ValueError("unsupported JSON-RPC command")
    if not isinstance(params, dict):
        raise ValueError("command params must be an object")
    event_id = params.get("event_id")
    if payload.get("id") != event_id or not isinstance(event_id, str) or len(event_id) != 32:
        raise ValueError("invalid command identity")
    if params.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported command schema")
    if params.get("verb") not in VALID_VERBS or params.get("kind") not in VALID_KINDS:
        raise ValueError("invalid command action")
    required_strings = ("actor", "namespace", "name")
    if not all(isinstance(params.get(field), str) and params[field] for field in required_strings):
        raise ValueError("command target and actor are required")
    if params["verb"] in {"sleep", "cancel"} and not params.get("confirmed"):
        raise ValueError("destructive command is not confirmed")
    return params


class Publisher:
    def __init__(self, config: KafkaConfig) -> None:
        from confluent_kafka import Producer

        self.config = config
        self.producer = Producer(
            {
                **config.client_config(),
                "acks": "all",
                "enable.idempotence": True,
                "client.id": "model-fleet",
            }
        )

    def send(self, topic: str, payload: Mapping[str, Any], key: str) -> None:
        errors: list[str] = []

        def delivered(error: Any, _message: Any) -> None:
            if error:
                errors.append(str(error))

        self.producer.produce(
            topic,
            key=key.encode(),
            value=json.dumps(payload, separators=(",", ":")).encode(),
            on_delivery=delivered,
        )
        remaining = self.producer.flush(15)
        if remaining or errors:
            raise RuntimeError(errors[0] if errors else "Kafka publish timed out")

    def enqueue(self, command: Any, actor: str) -> str:
        payload = new_command(command, actor)
        validate_command(payload)
        self.send(
            self.config.command_topic,
            payload,
            f"{command.kind}/{command.namespace}/{command.name}",
        )
        return str(payload["id"])


def event_payload(params: Mapping[str, Any], state: str, error: str = "") -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "method": f"model-fleet.command.{state}",
        "id": params["event_id"],
        "params": {
            **params,
            "state": state,
            "occurred_at": datetime.now(UTC).isoformat(),
            **({"error": error[:300]} if error else {}),
        },
    }
