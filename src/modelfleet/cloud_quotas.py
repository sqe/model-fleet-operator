"""Confirmed cloud quota requests submitted by the Slack control service."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx


class QuotaError(RuntimeError):
    """A quota request that cannot be safely submitted."""


@dataclass(frozen=True)
class QuotaCommand:
    provider: str
    service: str
    quota: str
    desired_value: float
    scope: str = ""
    dimensions: tuple[tuple[str, str], ...] = ()
    confirmed: bool = False
    verb: str = "quota"


def parse_quota_command(parts: list[str]) -> QuotaCommand:
    """Parse provider-specific quota arguments after the ``quota`` verb."""
    confirmed = bool(parts and parts[-1].lower() == "confirm")
    arguments = parts[1:-1] if confirmed else parts[1:]
    if not arguments or arguments[0].lower() not in {"aws", "gcp"}:
        raise ValueError("usage: quota <aws|gcp> ... confirm")
    provider = arguments[0].lower()
    try:
        if provider == "aws":
            if len(arguments) not in {4, 5}:
                raise ValueError(
                    "usage: quota aws <service-code> <quota-code> <desired-value> [region] confirm"
                )
            service, quota = arguments[1:3]
            desired_value = float(arguments[3])
            scope = arguments[4] if len(arguments) == 5 else ""
            dimensions: tuple[tuple[str, str], ...] = ()
        else:
            if len(arguments) < 5:
                raise ValueError(
                    "usage: quota gcp <project-number> <service> <quota-id> "
                    "<desired-value> [key=value ...] confirm"
                )
            scope, service, quota = arguments[1:4]
            if not scope.isdigit():
                raise ValueError("GCP quota requests require the numeric project number")
            desired_value = float(arguments[4])
            parsed_dimensions = []
            for item in arguments[5:]:
                key, separator, value = item.partition("=")
                if not separator or not key or not value:
                    raise ValueError("GCP dimensions must use key=value")
                parsed_dimensions.append((key, value))
            dimensions = tuple(sorted(parsed_dimensions))
    except ValueError as error:
        if str(error).startswith(("usage:", "GCP")):
            raise
        raise ValueError("desired quota value must be a number") from error
    if desired_value <= 0:
        raise ValueError("desired quota value must be positive")
    return QuotaCommand(
        provider,
        service,
        quota,
        desired_value,
        scope,
        dimensions,
        confirmed,
    )


class CloudQuotaManager:
    """Read quota state and submit provider-native increase requests."""

    def __init__(
        self,
        *,
        aws_client_factory: Callable[[str], Any] | None = None,
        gcp_client: httpx.Client | Any | None = None,
        gcp_credentials: Any | None = None,
    ) -> None:
        self._aws_client_factory = aws_client_factory
        self._gcp_client = gcp_client
        self._gcp_credentials = gcp_credentials

    def request(self, command: QuotaCommand) -> str:
        if command.provider == "aws":
            return self._request_aws(command)
        return self._request_gcp(command)

    def _request_aws(self, command: QuotaCommand) -> str:
        region = command.scope or os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
        if not region:
            raise QuotaError("AWS region is required in the command or Slack pod environment.")
        if self._aws_client_factory:
            api = self._aws_client_factory(region)
        else:
            import boto3

            api = boto3.client("service-quotas", region_name=region)
        try:
            current = api.get_service_quota(ServiceCode=command.service, QuotaCode=command.quota)[
                "Quota"
            ]
            if command.desired_value <= float(current["Value"]):
                raise QuotaError(
                    f"AWS quota is already {current['Value']:g} in `{region}`. "
                    "The requested value must be higher."
                )
            history = api.list_requested_service_quota_change_history_by_quota(
                ServiceCode=command.service, QuotaCode=command.quota
            ).get("RequestedQuotas", [])
            pending = next(
                (
                    item
                    for item in history
                    if item.get("Status") in {"PENDING", "CASE_OPENED"}
                    and float(item.get("DesiredValue", 0)) >= command.desired_value
                ),
                None,
            )
            if pending:
                return (
                    "*AWS quota request already pending*\n"
                    f"• Request: `{pending.get('Id', 'unknown')}`\n"
                    f"• Target: `{command.service}` / `{command.quota}` in `{region}`\n"
                    f"• Desired value: `{pending['DesiredValue']:g}`\n"
                    f"• Status: `{pending['Status']}`"
                )
            requested = api.request_service_quota_increase(
                ServiceCode=command.service,
                QuotaCode=command.quota,
                DesiredValue=command.desired_value,
            )["RequestedQuota"]
        except QuotaError:
            raise
        except Exception as error:
            raise QuotaError(f"AWS rejected the quota request: {error}") from error
        return (
            "*AWS quota request submitted*\n"
            f"• Request: `{requested.get('Id', 'unknown')}`\n"
            f"• Target: `{command.service}` / `{command.quota}` in `{region}`\n"
            f"• Desired value: `{command.desired_value:g}`\n"
            f"• Status: `{requested.get('Status', 'PENDING')}`\n"
            "Approval and capacity are not guaranteed. Track this request in AWS Service Quotas."
        )

    def _gcp_transport(self) -> tuple[Any, dict[str, str]]:
        if self._gcp_client is not None and self._gcp_credentials is None:
            return self._gcp_client, {}
        credentials = self._gcp_credentials
        if credentials is None:
            import google.auth

            credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            self._gcp_credentials = credentials
        if not credentials.valid:
            from google.auth.transport.requests import Request

            credentials.refresh(Request())
        if self._gcp_client is None:
            self._gcp_client = httpx.Client(timeout=30)
        return self._gcp_client, {"Authorization": f"Bearer {credentials.token}"}

    def _request_gcp(self, command: QuotaCommand) -> str:
        digest_input = "|".join(
            [
                command.service,
                command.quota,
                *(f"{key}={value}" for key, value in command.dimensions),
            ]
        )
        preference_id = f"model-fleet-{hashlib.sha256(digest_input.encode()).hexdigest()[:20]}"
        name = f"projects/{command.scope}/locations/global/quotaPreferences/{preference_id}"
        url = f"https://cloudquotas.googleapis.com/v1/{name}"
        client, headers = self._gcp_transport()
        try:
            existing = client.get(url, headers=headers)
            if existing.status_code not in {200, 404}:
                existing.raise_for_status()
            if existing.status_code == 200:
                data = existing.json()
                preferred = float(data.get("quotaConfig", {}).get("preferredValue", 0))
                if data.get("reconciling") and preferred >= command.desired_value:
                    return (
                        "*GCP quota request already pending*\n"
                        f"• Preference: `{name}`\n"
                        f"• Target: `{command.service}` / `{command.quota}`\n"
                        f"• Desired value: `{preferred:g}`\n"
                        "• Status: `RECONCILING`"
                    )
            payload = {
                "name": name,
                "service": command.service,
                "quotaId": command.quota,
                "quotaConfig": {"preferredValue": str(command.desired_value)},
                "dimensions": dict(command.dimensions),
                "justification": os.getenv(
                    "QUOTA_JUSTIFICATION", "Capacity requested through Model Fleet operations"
                ),
                "contactEmail": os.getenv("QUOTA_CONTACT_EMAIL", ""),
            }
            response = client.patch(
                url, params={"allowMissing": "true"}, headers=headers, json=payload
            )
            response.raise_for_status()
            result = response.json()
        except Exception as error:
            raise QuotaError(f"Google Cloud rejected the quota request: {error}") from error
        status = "RECONCILING" if result.get("reconciling", True) else "SUBMITTED"
        dimensions = ", ".join(f"{key}={value}" for key, value in command.dimensions) or "global"
        return (
            "*GCP quota preference submitted*\n"
            f"• Preference: `{name}`\n"
            f"• Target: `{command.service}` / `{command.quota}`\n"
            f"• Dimensions: `{dimensions}`\n"
            f"• Desired value: `{command.desired_value:g}`\n"
            f"• Status: `{status}`\n"
            "Approval and capacity are not guaranteed. Track this preference in Cloud Quotas."
        )
