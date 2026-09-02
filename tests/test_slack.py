from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from kubernetes.client import ApiException

from modelfleet.cloud_quotas import QuotaCommand
from modelfleet.slack import AgentCommand, Command, FleetClient, handle, home_view, parse_command


class FakeFleet:
    def __init__(self):
        self.executed = []

    def status(self, namespace):
        return f"status:{namespace}"

    def execute(self, command, actor):
        self.executed.append((command, actor))
        return "accepted"


def test_parse_status_and_mention():
    assert parse_command("<@U1> status models") == Command("status", namespace="models")


def test_parse_snapshot_alias():
    assert parse_command("picture models") == Command("snapshot", namespace="models")


def test_parse_training_cancel_confirmation():
    assert parse_command("cancel training models/run-1 confirm") == Command(
        "cancel", "training", "models", "run-1", True
    )


def test_parse_cloud_quota_commands():
    assert parse_command("quota aws ec2 L-DB2E81BA 16 us-west-2 confirm") == QuotaCommand(
        "aws", "ec2", "L-DB2E81BA", 16, "us-west-2", (), True
    )
    assert parse_command(
        "quota gcp 123456 compute.googleapis.com GPUS-PER-GPU-FAMILY-per-project-region "
        "8 region=us-central1 gpu_family=NVIDIA_H100 confirm"
    ) == QuotaCommand(
        "gcp",
        "compute.googleapis.com",
        "GPUS-PER-GPU-FAMILY-per-project-region",
        8,
        "123456",
        (("gpu_family", "NVIDIA_H100"), ("region", "us-central1")),
        True,
    )


def test_parse_managed_agent_commands():
    assert parse_command("wake agent models/research") == Command(
        "wake", "agent", "models", "research"
    )


def test_parse_and_route_agent_task():
    assert parse_command('run weather.current "weather in London"') == AgentCommand(
        "run", "weather.current", "weather in London"
    )
    submit = Mock(return_value="routed")
    response = handle(
        FakeFleet(),
        "run weather.current weather in London",
        "U1",
        "C1",
        default_namespace="default",
        allowed_users="U1",
        allowed_channels="C1",
        agent_submit=submit,
    )
    assert response == "routed"
    submit.assert_called_once_with("weather.current", "weather in London", "U1")
    assert parse_command("sleep agent models/research confirm") == Command(
        "sleep", "agent", "models", "research", True
    )


def test_parse_uses_default_namespace():
    assert parse_command("wake embed", "models") == Command("wake", "inference", "models", "embed")


def test_destructive_action_needs_confirmation():
    fleet = FakeFleet()
    response = handle(
        fleet,
        "sleep models/embed",
        "U1",
        "C1",
        default_namespace="default",
        allowed_users="U1",
        allowed_channels="C1",
    )
    assert "confirm" in response
    assert not fleet.executed


def test_quota_request_requires_confirmation_and_enabled_manager():
    manager = Mock()
    common = {
        "user_id": "U1",
        "channel_id": "C1",
        "default_namespace": "default",
        "allowed_users": "U1",
        "allowed_channels": "C1",
        "quota_manager": manager,
    }
    command = "quota aws ec2 L-DB2E81BA 16 us-west-2"

    assert "Repeat with `confirm`" in handle(FakeFleet(), command, **common)
    manager.request.assert_not_called()
    manager.request.return_value = "submitted"
    assert handle(FakeFleet(), f"{command} confirm", **common) == "submitted"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('wake "unterminated', "could not parse"),
        ("explode models/x", "unknown command"),
        ("pause models/x", "usage"),
        ("wake", "requires"),
        ("wake models/Bad_Name", "Kubernetes resource names"),
        ("run weather.current", "requires"),
        ("run Bad! prompt", "skill must use"),
    ],
)
def test_parse_rejects_invalid_commands(text, message):
    with pytest.raises(ValueError, match=message):
        parse_command(text)


def test_write_is_allowlisted_and_audited():
    fleet = FakeFleet()
    response = handle(
        fleet,
        "wake models/embed",
        "U1",
        "C1",
        default_namespace="default",
        allowed_users="U1,U2",
        allowed_channels="C1",
    )
    assert response == "accepted"
    assert fleet.executed[0][1] == "U1"


def test_read_only_status_is_available_to_non_operator():
    response = handle(
        FakeFleet(),
        "status models",
        "U9",
        "C1",
        default_namespace="default",
        allowed_users="U1",
        allowed_channels="",
    )
    assert response == "status:models"


def test_snapshot_upload_is_available_to_read_only_user():
    snapshot = Mock(return_value="uploaded")
    response = handle(
        FakeFleet(),
        "snapshot models",
        "U9",
        "C1",
        default_namespace="default",
        allowed_users="",
        allowed_channels="C1",
        snapshot=snapshot,
    )
    assert response == "uploaded"
    snapshot.assert_called_once_with("models", "C1")


def test_cost_report_is_available_to_read_only_user():
    cost_report = Mock(return_value="costs")
    response = handle(
        FakeFleet(),
        "cost models",
        "U9",
        "C1",
        default_namespace="default",
        allowed_users="",
        allowed_channels="C1",
        cost_report=cost_report,
    )
    assert response == "costs"
    cost_report.assert_called_once_with("models")


def test_empty_allowlist_denies_changes():
    response = handle(
        FakeFleet(),
        "wake models/embed",
        "U1",
        "C1",
        default_namespace="default",
        allowed_users="",
        allowed_channels="",
    )
    assert "not allowed" in response


def test_help_invalid_command_and_disallowed_channel():
    fleet = FakeFleet()
    common = {
        "user_id": "U1",
        "channel_id": "C1",
        "default_namespace": "default",
        "allowed_users": "U1",
        "allowed_channels": "C1",
    }
    assert "Model Fleet" in handle(fleet, "help", **common)
    assert "unknown command" in handle(fleet, "wat", **common)
    common["channel_id"] = "C2"
    assert "not enabled" in handle(fleet, "status", **common)


def test_fleet_status_formats_capacity_and_resources(monkeypatch):
    fleet = FleetClient.__new__(FleetClient)
    fleet.custom = Mock()
    fleet.core = Mock()
    fleet.custom.list_namespaced_custom_object.side_effect = [
        {
            "items": [
                {
                    "metadata": {"namespace": "models", "name": "embed"},
                    "spec": {"forceActive": True},
                    "status": {"phase": "Ready", "readyReplicas": 1},
                }
            ]
        },
        {
            "items": [
                {
                    "metadata": {"namespace": "models", "name": "run-1"},
                    "spec": {},
                    "status": {"phase": "Running"},
                }
            ]
        },
        {
            "items": [
                {
                    "metadata": {"namespace": "models", "name": "support-v3"},
                    "spec": {
                        "version": "3.0.0",
                        "format": "parquet",
                        "classification": "internal",
                    },
                }
            ]
        },
        {
            "items": [
                {
                    "metadata": {"namespace": "models", "name": "research-agent"},
                    "spec": {"runtime": {"deploymentName": "research-agent"}},
                    "status": {"phase": "Active", "readyReplicas": 1},
                }
            ]
        },
    ]
    fleet.core.list_node.return_value.items = [
        SimpleNamespace(status=SimpleNamespace(capacity={"nvidia.com/gpu": "2"})),
        SimpleNamespace(status=SimpleNamespace(capacity={"cpu": "4"})),
    ]
    monkeypatch.setenv("PLATFORM_PROFILE", "kind")
    result = fleet.status("models")
    assert "GPU capacity: 2 across 1 node" in result
    assert "`models/support-v3` — version `3.0.0`, parquet, internal" in result
    assert "`models/research-agent` — Active, 1 ready, managed" in result
    assert "`models/embed` — Ready, 1 ready, pinned" in result
    assert "`models/run-1` — Running" in result


def test_fleet_empty_cluster_status():
    fleet = FleetClient.__new__(FleetClient)
    fleet.custom = Mock()
    fleet.core = Mock()
    fleet.custom.list_cluster_custom_object.return_value = {"items": []}
    fleet.core.list_node.return_value.items = []
    result = fleet.status()
    assert "Inference: none" in result
    assert "Training: none" in result


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (Command("wake", "inference", "models", "embed"), {"suspend": False, "forceActive": True}),
        (Command("auto", "inference", "models", "embed"), {"suspend": False, "forceActive": False}),
        (
            Command("sleep", "inference", "models", "embed", True),
            {"suspend": True, "forceActive": False},
        ),
        (Command("pause", "training", "models", "run"), {"suspend": True}),
        (Command("resume", "training", "models", "run"), {"suspend": False}),
        (Command("cancel", "training", "models", "run", True), {"cancelled": True}),
        (Command("wake", "agent", "models", "research"), {"suspend": False}),
        (Command("sleep", "agent", "models", "research", True), {"suspend": True}),
    ],
)
def test_fleet_execute_patches_intent(command, expected):
    fleet = FleetClient.__new__(FleetClient)
    fleet.custom = Mock()
    result = fleet.execute(command, "U1")
    body = fleet.custom.patch_namespaced_custom_object.call_args.args[-1]
    assert body["spec"] == expected
    assert body["metadata"]["annotations"]["fleet.sqe.io/last-command-user"] == "U1"
    assert "Accepted" in result


def test_fleet_execute_defends_destructive_calls_without_confirmation():
    fleet = FleetClient.__new__(FleetClient)
    fleet.custom = Mock()
    result = fleet.execute(Command("cancel", "training", "models", "run"), "U1")
    assert "confirm" in result
    fleet.custom.patch_namespaced_custom_object.assert_not_called()


def test_fleet_execute_queues_when_kafka_is_configured():
    fleet = FleetClient.__new__(FleetClient)
    fleet.custom = Mock()
    fleet.publisher = Mock()
    fleet.publisher.enqueue.return_value = "event-1"
    result = fleet.execute(Command("wake", "inference", "models", "embed"), "U1")
    assert "event-1" in result
    fleet.publisher.enqueue.assert_called_once()
    fleet.custom.patch_namespaced_custom_object.assert_not_called()


def test_handle_reports_not_found():
    fleet = FakeFleet()
    fleet.execute = Mock(side_effect=ApiException(status=404))
    result = handle(
        fleet,
        "wake models/missing",
        "U1",
        "C1",
        default_namespace="default",
        allowed_users="U1",
        allowed_channels="C1",
    )
    assert "No inference resource" in result


def test_home_view_renders_inference_and_managed_agent_controls():
    fleet = FleetClient.__new__(FleetClient)
    fleet.custom = Mock()
    fleet.custom.list_namespaced_custom_object.side_effect = [
        {
            "items": [
                {
                    "metadata": {"namespace": "models", "name": "embed"},
                    "spec": {"forceActive": False},
                    "status": {"phase": "Ready", "readyReplicas": 2},
                }
            ]
        },
        {
            "items": [
                {
                    "metadata": {"namespace": "models", "name": "research"},
                    "spec": {"runtime": {"deploymentName": "research"}},
                    "status": {"phase": "Active", "readyReplicas": 1},
                },
                {
                    "metadata": {"namespace": "models", "name": "catalog-only"},
                    "spec": {},
                },
            ]
        },
    ]

    view = home_view(fleet, "models", notice="Accepted")

    action_ids = [
        element.get("action_id")
        for block in view["blocks"]
        for element in block.get("elements", [])
    ]
    values = [
        element["value"]
        for block in view["blocks"]
        for element in block.get("elements", [])
        if element.get("action_id") == "fleet_home_control"
    ]
    assert "fleet_home_refresh" in action_ids
    assert "fleet_home_cost" in action_ids
    assert "wake models/embed" in values
    assert "auto models/embed" in values
    assert "sleep models/embed confirm" in values
    assert "wake agent models/research" in values
    assert "sleep agent models/research confirm" in values
    assert all("catalog-only" not in value for value in values)
    sleep = next(
        element
        for block in view["blocks"]
        for element in block.get("elements", [])
        if element.get("value") == "sleep agent models/research confirm"
    )
    assert sleep["confirm"]["confirm"]["text"] == "Sleep"
