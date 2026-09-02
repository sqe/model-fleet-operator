from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from modelfleet.command_worker import process
from modelfleet.kafka import KafkaConfig, event_payload, new_command, validate_command
from modelfleet.slack import Command


def test_new_command_round_trip():
    command = Command("sleep", "inference", "models", "embed", True)
    payload = new_command(command, "U123")
    params = validate_command(payload)
    assert params["verb"] == "sleep"
    assert params["namespace"] == "models"
    assert payload["id"] == params["event_id"]


def test_managed_agent_command_round_trip():
    command = Command("sleep", "agent", "models", "research", True)
    params = validate_command(new_command(command, "U123"))
    assert params["kind"] == "agent"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(jsonrpc="1.0"),
        lambda value: value["params"].update(schema_version=2),
        lambda value: value["params"].update(verb="delete"),
        lambda value: value["params"].update(actor=""),
        lambda value: value["params"].update(confirmed=False),
    ],
)
def test_validate_rejects_invalid_commands(mutation):
    payload = new_command(Command("sleep", "inference", "models", "embed", True), "U1")
    mutation(payload)
    with pytest.raises(ValueError):
        validate_command(payload)


def test_kafka_config_requires_distinct_topics(monkeypatch):
    monkeypatch.setenv("KAFKA_ENABLED", "true")
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
    monkeypatch.setenv("KAFKA_COMMAND_TOPIC", "same")
    monkeypatch.setenv("KAFKA_EVENT_TOPIC", "same")
    with pytest.raises(RuntimeError):
        KafkaConfig.from_env()


def test_process_applies_valid_command():
    fleet = SimpleNamespace(apply=Mock())
    payload = new_command(Command("wake", "inference", "models", "embed"), "U1")
    params = process(payload, fleet)
    applied = fleet.apply.call_args.args
    assert applied[0] == Command("wake", "inference", "models", "embed", False)
    assert applied[1:] == ("U1", params["event_id"])


def test_event_payload_preserves_correlation():
    params = validate_command(new_command(Command("auto", "inference", "models", "x"), "U1"))
    event = event_payload(params, "applied")
    assert event["id"] == params["event_id"]
    assert event["params"]["state"] == "applied"
