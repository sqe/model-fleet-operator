"""Slack control plane for Model Fleet custom resources."""

from __future__ import annotations

import logging
import os
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from typing import Any

import httpx
from kubernetes import client, config
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from modelfleet.cloud_quotas import (
    CloudQuotaManager,
    QuotaCommand,
    QuotaError,
    parse_quota_command,
)
from modelfleet.cost_report import PrometheusCostReporter
from modelfleet.grafana import GrafanaRenderer
from modelfleet.kafka import KafkaConfig, Publisher

LOG = logging.getLogger("modelfleet.slack")
GROUP = "fleet.sqe.io"
VERSION = "v1alpha1"

HELP = """*Model Fleet*
`status [namespace]` — inference, training, and GPU capacity
`cost [namespace]` — current spend, token volume, and GPU utilization
`snapshot [namespace]` — upload the unified Model Fleet Grafana dashboard
`wake <namespace/name>` — keep at least one inference replica active
`wake agent <namespace/name>` — start a managed agent Deployment
`auto <namespace/name>` — return inference to KEDA control
`sleep <namespace/name> confirm` — scale inference to zero
`sleep agent <namespace/name> confirm` — scale a managed agent to zero
`run <skill> <prompt>` — route a specialist-agent task through Kafka
`pause training <namespace/name>` — suspend a training Job
`resume training <namespace/name>` — resume a training Job
`cancel training <namespace/name> confirm` — terminate a training run
`quota aws <service-code> <quota-code> <value> [region] confirm` — request AWS quota
`quota gcp <project-number> <service> <quota-id> <value> [key=value ...] confirm`
    — request GCP quota
`help`"""


@dataclass(frozen=True)
class Command:
    verb: str
    kind: str = "inference"
    namespace: str | None = None
    name: str | None = None
    confirmed: bool = False


@dataclass(frozen=True)
class AgentCommand:
    verb: str
    skill: str
    prompt: str


def parse_command(
    text: str, default_namespace: str = "default"
) -> Command | AgentCommand | QuotaCommand:
    """Parse the intentionally small Slack command grammar."""
    cleaned = re.sub(r"<@[^>]+>", "", text or "").strip()
    try:
        parts = shlex.split(cleaned)
    except ValueError as error:
        raise ValueError(f"could not parse command: {error}") from error
    if not parts:
        return Command("help")
    verb = parts[0].lower()
    if verb in {"help", "?"}:
        return Command("help")
    if verb in {"status", "list", "ls"}:
        return Command("status", namespace=parts[1] if len(parts) > 1 else None)
    if verb in {"cost", "expenses", "spend"}:
        return Command("cost", namespace=parts[1] if len(parts) > 1 else None)
    if verb in {"snapshot", "picture"}:
        return Command("snapshot", namespace=parts[1] if len(parts) > 1 else None)
    if verb == "quota":
        return parse_quota_command(parts)
    if verb == "run":
        if len(parts) < 3:
            raise ValueError("run requires a skill ID and prompt")
        skill = parts[1]
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", skill):
            raise ValueError(
                "skill must use lowercase letters, numbers, dots, dashes, or underscores"
            )
        return AgentCommand("run", skill, " ".join(parts[2:]))

    kind = "inference"
    position = 1
    if len(parts) > 1 and parts[1].lower() in {"training", "train"}:
        kind = "training"
        position = 2
    elif len(parts) > 1 and parts[1].lower() in {"agent", "agents"}:
        kind = "agent"
        position = 2
    if verb in {"pause", "resume", "cancel"} and kind != "training":
        raise ValueError(f"usage: {verb} training <namespace/name>")
    if kind == "agent" and verb not in {"wake", "sleep"}:
        raise ValueError(f"usage: {verb} is not supported for agents; use wake or sleep")
    if verb not in {"wake", "auto", "sleep", "pause", "resume", "cancel"}:
        raise ValueError(f"unknown command: {verb}")
    if len(parts) <= position:
        raise ValueError(f"{verb} requires a namespace/name target")
    target = parts[position]
    if "/" in target:
        namespace, name = target.split("/", 1)
    else:
        namespace, name = default_namespace, target
    if not namespace or not name or not re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", name):
        raise ValueError("target must be namespace/name using Kubernetes resource names")
    return Command(
        verb,
        kind=kind,
        namespace=namespace,
        name=name,
        confirmed="confirm" in {part.lower() for part in parts[position + 1 :]},
    )


class FleetClient:
    def __init__(self, *, durable_commands: bool = True) -> None:
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        self.custom = client.CustomObjectsApi()
        self.core = client.CoreV1Api()
        kafka_config = KafkaConfig.from_env()
        self.publisher = (
            Publisher(kafka_config) if durable_commands and kafka_config.enabled else None
        )

    def status(self, namespace: str | None = None) -> str:
        inference = self._list("inferenceservices", namespace)
        training = self._list("trainingruns", namespace)
        datasets = self._list("datasets", namespace)
        agents = self._list("agentregistrations", namespace)
        lines = [f"*Model Fleet status* — profile `{os.getenv('PLATFORM_PROFILE', 'unknown')}`"]
        lines.append(self._gpu_line())
        lines.extend(self._dataset_lines(datasets))
        lines.extend(self._agent_lines(agents))
        lines.extend(self._inference_lines(inference))
        lines.extend(self._training_lines(training))
        return "\n".join(lines)

    def _list(self, plural: str, namespace: str | None) -> list[dict[str, Any]]:
        if namespace:
            result = self.custom.list_namespaced_custom_object(GROUP, VERSION, namespace, plural)
        else:
            result = self.custom.list_cluster_custom_object(GROUP, VERSION, plural)
        return result.get("items", [])

    def _gpu_line(self) -> str:
        nodes = self.core.list_node().items
        gpu_nodes = 0
        gpu_capacity = 0
        for node in nodes:
            capacity = node.status.capacity or {}
            count = int(capacity.get("nvidia.com/gpu", 0))
            if count:
                gpu_nodes += 1
                gpu_capacity += count
        return f"GPU capacity: {gpu_capacity} across {gpu_nodes} node(s)"

    @staticmethod
    def _inference_lines(items: list[dict[str, Any]]) -> list[str]:
        if not items:
            return ["Inference: none"]
        lines = ["*Inference*"]
        for item in sorted(items, key=_resource_key):
            meta, spec, status = item["metadata"], item["spec"], item.get("status", {})
            mode = (
                "sleep" if spec.get("suspend") else "pinned" if spec.get("forceActive") else "auto"
            )
            lines.append(
                f"• `{meta['namespace']}/{meta['name']}` — {status.get('phase', 'Unknown')}, "
                f"{status.get('readyReplicas', 0)} ready, {mode}"
            )
        return lines

    @staticmethod
    def _training_lines(items: list[dict[str, Any]]) -> list[str]:
        if not items:
            return ["Training: none"]
        lines = ["*Training*"]
        for item in sorted(items, key=_resource_key):
            meta, status = item["metadata"], item.get("status", {})
            lines.append(
                f"• `{meta['namespace']}/{meta['name']}` — {status.get('phase', 'Unknown')}"
            )
        return lines

    @staticmethod
    def _dataset_lines(items: list[dict[str, Any]]) -> list[str]:
        if not items:
            return ["Datasets: none"]
        lines = ["*Datasets*"]
        for item in sorted(items, key=_resource_key):
            meta, spec = item["metadata"], item["spec"]
            lines.append(
                f"• `{meta['namespace']}/{meta['name']}` — version `{spec['version']}`, "
                f"{spec['format']}, {spec['classification']}"
            )
        return lines

    @staticmethod
    def _agent_lines(items: list[dict[str, Any]]) -> list[str]:
        if not items:
            return ["Agents: none"]
        lines = ["*Agents*"]
        for item in sorted(items, key=_resource_key):
            meta, spec, status = item["metadata"], item["spec"], item.get("status", {})
            managed = "managed" if spec.get("runtime") else "registration only"
            lines.append(
                f"• `{meta['namespace']}/{meta['name']}` — "
                f"{status.get('phase', 'Registered')}, "
                f"{status.get('readyReplicas', 0)} ready, {managed}"
            )
        return lines

    def execute(self, command: Command, actor: str) -> str:
        if command.verb in {"sleep", "cancel"} and not command.confirmed:
            return f"This stops compute. Repeat with `confirm`: `{_canonical(command)} confirm`"
        if publisher := getattr(self, "publisher", None):
            event_id = publisher.enqueue(command, actor)
            return f"Queued `{_canonical(command)}` as `{event_id}`."
        return self.apply(command, actor)

    def apply(self, command: Command, actor: str, event_id: str = "") -> str:
        annotations = {
            "fleet.sqe.io/last-command-user": actor,
            "fleet.sqe.io/last-command-at": datetime.now(UTC).isoformat(),
        }
        if event_id:
            annotations["fleet.sqe.io/last-command-id"] = event_id
        if command.kind == "training":
            fields = {
                "pause": {"suspend": True},
                "resume": {"suspend": False},
                "cancel": {"cancelled": True},
            }[command.verb]
            plural = "trainingruns"
        elif command.kind == "agent":
            fields = {"suspend": command.verb == "sleep"}
            plural = "agentregistrations"
        else:
            fields = {
                "wake": {"suspend": False, "forceActive": True},
                "auto": {"suspend": False, "forceActive": False},
                "sleep": {"suspend": True, "forceActive": False},
            }[command.verb]
            plural = "inferenceservices"
        self.custom.patch_namespaced_custom_object(
            GROUP,
            VERSION,
            command.namespace,
            plural,
            command.name,
            {"metadata": {"annotations": annotations}, "spec": fields},
        )
        return f"Accepted `{_canonical(command)}`. The operator is reconciling it now."


def _resource_key(item: dict[str, Any]) -> tuple[str, str]:
    metadata = item["metadata"]
    return metadata["namespace"], metadata["name"]


def _canonical(command: Command) -> str:
    middle = f" {command.kind}" if command.kind in {"training", "agent"} else ""
    return f"{command.verb}{middle} {command.namespace}/{command.name}"


def _allowed(value: str, configured: str) -> bool:
    entries = {item.strip() for item in configured.split(",") if item.strip()}
    return not entries or value in entries


def home_view(
    fleet: FleetClient,
    namespace: str,
    *,
    notice: str = "",
    resource_limit: int = 12,
) -> dict[str, Any]:
    """Build a compact App Home view from current Kubernetes intent."""
    inference = fleet._list("inferenceservices", namespace)
    agents = [
        item for item in fleet._list("agentregistrations", namespace) if item["spec"].get("runtime")
    ]
    resources = [("inference", item) for item in sorted(inference, key=_resource_key)] + [
        ("agent", item) for item in sorted(agents, key=_resource_key)
    ]
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": "Model Fleet"}},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"Workload controls for `{namespace}`. Changes use the same "
                    "authorization and audit path as `/fleet`."
                ),
            },
        },
    ]
    if notice:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": notice[:2000]}]})
    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": "fleet_home_refresh",
                    "text": {"type": "plain_text", "text": "Refresh"},
                    "value": namespace,
                },
                {
                    "type": "button",
                    "action_id": "fleet_home_cost",
                    "text": {"type": "plain_text", "text": "All costs"},
                    "value": "all",
                },
            ],
        }
    )
    if not resources:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "No controllable inference services or managed agents were found.",
                },
            }
        )
    for kind, item in resources[:resource_limit]:
        metadata, status = item["metadata"], item.get("status", {})
        target = f"{metadata['namespace']}/{metadata['name']}"
        ready = status.get("readyReplicas", 0)
        label = "Inference" if kind == "inference" else "Agent"
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{label}* `{target}`\n{status.get('phase', 'Unknown')} · {ready} ready"
                    ),
                },
            }
        )
        action_kind = f" {kind}" if kind == "agent" else ""
        elements = [
            {
                "type": "button",
                "action_id": "fleet_home_control",
                "text": {"type": "plain_text", "text": "Wake"},
                "style": "primary",
                "value": f"wake{action_kind} {target}",
            }
        ]
        if kind == "inference":
            elements.append(
                {
                    "type": "button",
                    "action_id": "fleet_home_control",
                    "text": {"type": "plain_text", "text": "Auto"},
                    "value": f"auto {target}",
                }
            )
        elements.append(
            {
                "type": "button",
                "action_id": "fleet_home_control",
                "text": {"type": "plain_text", "text": "Sleep"},
                "style": "danger",
                "value": f"sleep{action_kind} {target} confirm",
                "confirm": {
                    "title": {"type": "plain_text", "text": "Stop compute?"},
                    "text": {"type": "mrkdwn", "text": f"Scale `{target}` to zero?"},
                    "confirm": {"type": "plain_text", "text": "Sleep"},
                    "deny": {"type": "plain_text", "text": "Cancel"},
                },
            }
        )
        blocks.append({"type": "actions", "elements": elements})
    if len(resources) > resource_limit:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"Showing {resource_limit} of {len(resources)} resources. "
                            f"Use `/fleet status {namespace}` for the complete list."
                        ),
                    }
                ],
            }
        )
    return {"type": "home", "blocks": blocks}


def handle(
    fleet: FleetClient,
    text: str,
    user_id: str,
    channel_id: str,
    *,
    default_namespace: str,
    allowed_users: str,
    allowed_channels: str,
    snapshot: Callable[[str | None, str], str] | None = None,
    cost_report: Callable[[str | None], str] | None = None,
    quota_manager: CloudQuotaManager | None = None,
    agent_submit: Callable[[str, str, str], str] | None = None,
) -> str:
    if not _allowed(channel_id, allowed_channels):
        return "Model Fleet commands are not enabled in this channel."
    try:
        command = parse_command(text, default_namespace)
    except ValueError as error:
        return f"{error}\n\n{HELP}"
    if command.verb == "help":
        return HELP
    if command.verb == "status":
        return fleet.status(command.namespace)
    if command.verb == "cost":
        if cost_report is None:
            return "Cost reporting is not configured."
        try:
            return cost_report(command.namespace)
        except Exception:
            LOG.exception("Cost report failed")
            return "Prometheus could not produce the cost report."
    if command.verb == "snapshot":
        if snapshot is None:
            return "Grafana snapshots are not configured."
        try:
            return snapshot(command.namespace, channel_id)
        except Exception:
            LOG.exception("Grafana snapshot failed")
            return "Grafana could not render or upload the dashboard snapshot."
    if not allowed_users.strip() or not _allowed(user_id, allowed_users):
        return "You can inspect Model Fleet, but you are not allowed to change workloads."
    if isinstance(command, AgentCommand):
        if agent_submit is None:
            return "Agent task routing is not configured."
        try:
            return agent_submit(command.skill, command.prompt, user_id)
        except httpx.HTTPError:
            LOG.exception("Agent task submission failed")
            return "The agent supervisor could not accept the task."
    if isinstance(command, QuotaCommand):
        if not command.confirmed:
            return f"This submits a cloud quota request. Repeat with `confirm`: `{text} confirm`"
        if quota_manager is None:
            return "Cloud quota requests are disabled."
        try:
            return quota_manager.request(command)
        except QuotaError as error:
            LOG.warning("Cloud quota request failed: %s", error)
            return str(error)
    if command.verb in {"sleep", "cancel"} and not command.confirmed:
        return f"This stops compute. Repeat with `confirm`: `{_canonical(command)} confirm`"
    try:
        return fleet.execute(command, user_id)
    except client.ApiException as error:
        if error.status == 404:
            return f"No {command.kind} resource named `{command.namespace}/{command.name}`."
        LOG.exception("Slack command failed")
        return f"Kubernetes rejected the command (HTTP {error.status})."


def create_app(fleet: FleetClient) -> App:  # pragma: no cover - Slack SDK wiring
    app = App(
        token=os.environ["SLACK_BOT_TOKEN"],
        signing_secret=os.getenv("SLACK_SIGNING_SECRET", "socket-mode-only"),
    )
    default_namespace = os.getenv("DEFAULT_WORKLOAD_NAMESPACE", "default")
    allowed_users = os.getenv("SLACK_ALLOWED_USER_IDS", "")
    allowed_channels = os.getenv("SLACK_ALLOWED_CHANNEL_IDS", "")
    renderer = GrafanaRenderer.from_env()
    reporter = PrometheusCostReporter.from_env()
    quota_manager = (
        CloudQuotaManager()
        if os.getenv("SLACK_QUOTA_REQUESTS_ENABLED", "false").lower() == "true"
        else None
    )
    supervisor_url = os.getenv("AGENT_SUPERVISOR_URL", "").rstrip("/")
    supervisor_token = os.getenv("CONTROL_PLANE_API_KEY", "")

    def submit_agent(skill: str, prompt: str, user: str) -> str:
        headers = {"Authorization": f"Bearer {supervisor_token}"} if supervisor_token else {}
        response = httpx.post(
            f"{supervisor_url}/tasks/submit",
            headers=headers,
            json={"user_id": user, "prompt": prompt, "skill": skill},
            timeout=15,
        )
        response.raise_for_status()
        task = response.json()
        return f"Routed `{skill}` to `{task['agent']}` as `{task['id']}`."

    def upload_snapshot(namespace: str | None, channel: str) -> str:
        if renderer is None:
            return "Grafana snapshots are not configured."
        image = BytesIO(renderer.render(namespace))
        image.name = "model-fleet-dashboard.png"
        scope = f" for `{namespace}`" if namespace else ""
        app.client.files_upload_v2(
            channel=channel,
            file=image,
            filename=image.name,
            title=f"Model Fleet dashboard{scope}",
            initial_comment=f"Model Fleet operations dashboard snapshot{scope}",
        )
        return "Dashboard snapshot uploaded."

    def dispatch(text: str, user: str, channel: str) -> str:
        return handle(
            fleet,
            text,
            user,
            channel,
            default_namespace=default_namespace,
            allowed_users=allowed_users,
            allowed_channels=allowed_channels,
            snapshot=upload_snapshot,
            cost_report=reporter.report if reporter else None,
            quota_manager=quota_manager,
            agent_submit=submit_agent if supervisor_url else None,
        )

    def dispatch_home(text: str, user: str) -> str:
        # App Home is private to the user, so conversation channel policy does not apply.
        return handle(
            fleet,
            text,
            user,
            "",
            default_namespace=default_namespace,
            allowed_users=allowed_users,
            allowed_channels="",
            snapshot=upload_snapshot,
            cost_report=reporter.report if reporter else None,
            quota_manager=quota_manager,
            agent_submit=submit_agent if supervisor_url else None,
        )

    def publish_home(user: str, notice: str = "") -> None:
        app.client.views_publish(
            user_id=user,
            view=home_view(fleet, default_namespace, notice=notice),
        )

    @app.event("app_home_opened")
    def on_home_opened(event: dict[str, Any]) -> None:
        if event.get("tab") == "home" and event.get("user"):
            publish_home(event["user"])

    @app.action("fleet_home_refresh")
    def on_home_refresh(ack: Any, body: dict[str, Any]) -> None:
        ack()
        if user := body.get("user", {}).get("id"):
            publish_home(user)

    @app.action("fleet_home_cost")
    def on_home_cost(ack: Any, body: dict[str, Any]) -> None:
        ack()
        if user := body.get("user", {}).get("id"):
            publish_home(user, dispatch_home("cost", user))

    @app.action("fleet_home_control")
    def on_home_control(ack: Any, body: dict[str, Any]) -> None:
        ack()
        user = body.get("user", {}).get("id", "")
        action = body.get("actions", [{}])[0].get("value", "")
        if user:
            publish_home(user, dispatch_home(action, user))

    @app.event("app_mention")
    def on_mention(event: dict[str, Any], say: Any) -> None:
        say(dispatch(event.get("text", ""), event.get("user", ""), event.get("channel", "")))

    @app.event("message")
    def on_message(event: dict[str, Any], say: Any) -> None:
        if event.get("channel_type") == "im" and not event.get("bot_id"):
            say(dispatch(event.get("text", ""), event.get("user", ""), event.get("channel", "")))

    @app.command("/fleet")
    def on_slash(ack: Any, command: dict[str, Any], respond: Any) -> None:
        ack()
        response_text = dispatch(
            command.get("text", ""),
            command.get("user_id", ""),
            command.get("channel_id", ""),
        )
        respond(response_text)
        LOG.info(
            "handled slash command for user %s: %s",
            command.get("user_id"),
            response_text.splitlines()[0],
        )

    return app


def main() -> None:  # pragma: no cover - process entry point
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s"
    )
    app = create_app(FleetClient())
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()


if __name__ == "__main__":  # pragma: no cover
    main()
