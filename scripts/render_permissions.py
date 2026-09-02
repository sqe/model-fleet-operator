#!/usr/bin/env python3
"""Render a narrow workload identity ruleset for a selected platform."""

from __future__ import annotations

import argparse
import json
import re
import shlex
from pathlib import Path
from typing import Any

DNS_LABEL = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")
PROFILES = {"aws", "gcp", "bare-metal"}
GENERATED_FILES = {"aws-policy.json", "gcp-role.yaml", "NEXT_STEPS.txt", "service-account.yaml"}


def _required(config: dict[str, Any], key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value or "<" in value or ">" in value:
        raise ValueError(f"{key} must be a non-placeholder string")
    return value


def _string_list(config: dict[str, Any], key: str) -> list[str]:
    value = config.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"{key} must be a list of non-empty strings")
    return value


def _service_account(namespace: str, name: str, annotations: dict[str, str] | None = None) -> str:
    lines = [
        "apiVersion: v1",
        "kind: ServiceAccount",
        "metadata:",
        f"  name: {name}",
        f"  namespace: {namespace}",
    ]
    if annotations:
        lines.append("  annotations:")
        lines.extend(f"    {key}: {json.dumps(value)}" for key, value in annotations.items())
    lines.append("automountServiceAccountToken: false")
    return "\n".join(lines) + "\n"


def _render_aws(config: dict[str, Any], output: Path, namespace: str, account: str) -> None:
    role_arn = _required(config, "roleArn")
    reads = _string_list(config, "readResources")
    writes = _string_list(config, "writeResources")
    if not reads and not writes:
        raise ValueError("AWS ruleset needs at least one readResources or writeResources ARN")

    output.joinpath("service-account.yaml").write_text(
        _service_account(namespace, account, {"eks.amazonaws.com/role-arn": role_arn})
    )
    statements: list[dict[str, Any]] = []
    if reads:
        statements.append(
            {
                "Sid": "ReadModelArtifacts",
                "Effect": "Allow",
                "Action": ["s3:GetObject"],
                "Resource": reads,
            }
        )
    if writes:
        statements.append(
            {
                "Sid": "WriteRunArtifacts",
                "Effect": "Allow",
                "Action": ["s3:AbortMultipartUpload", "s3:PutObject"],
                "Resource": writes,
            }
        )
    policy = {"Version": "2012-10-17", "Statement": statements}
    output.joinpath("aws-policy.json").write_text(json.dumps(policy, indent=2) + "\n")
    output.joinpath("NEXT_STEPS.txt").write_text(
        "Attach aws-policy.json to the configured role, configure its EKS Pod Identity "
        "or IRSA trust, then run:\n"
        f"kubectl apply -f {output}/service-account.yaml\n"
    )


def _render_gcp(config: dict[str, Any], output: Path, namespace: str, account: str) -> None:
    project = _required(config, "projectId")
    google_account = _required(config, "googleServiceAccount")
    read_buckets = _string_list(config, "readBuckets")
    write_buckets = _string_list(config, "writeBuckets")
    if not read_buckets and not write_buckets:
        raise ValueError("GCP ruleset needs at least one readBuckets or writeBuckets entry")

    output.joinpath("service-account.yaml").write_text(
        _service_account(namespace, account, {"iam.gke.io/gcp-service-account": google_account})
    )
    member = f"serviceAccount:{project}.svc.id.goog[{namespace}/{account}]"
    commands = [
        "gcloud iam service-accounts add-iam-policy-binding "
        f"{shlex.quote(google_account)} --role=roles/iam.workloadIdentityUser "
        f"--member={shlex.quote(member)}",
    ]
    commands += [
        "gcloud storage buckets add-iam-policy-binding "
        f"{shlex.quote(f'gs://{bucket}')} "
        f"--member={shlex.quote(f'serviceAccount:{google_account}')} "
        "--role=roles/storage.objectViewer"
        for bucket in read_buckets
    ]
    commands += [
        "gcloud storage buckets add-iam-policy-binding "
        f"{shlex.quote(f'gs://{bucket}')} "
        f"--member={shlex.quote(f'serviceAccount:{google_account}')} "
        "--role=roles/storage.objectCreator"
        for bucket in write_buckets
    ]
    commands.append(f"kubectl apply -f {output}/service-account.yaml")
    output.joinpath("NEXT_STEPS.txt").write_text("\n".join(commands) + "\n")


def _render_bare_metal(config: dict[str, Any], output: Path, namespace: str, account: str) -> None:
    output.joinpath("service-account.yaml").write_text(_service_account(namespace, account))
    output.joinpath("NEXT_STEPS.txt").write_text(
        f"kubectl apply -f {output}/service-account.yaml\n"
        + f"Use spec.serviceAccountName: {account} in workloads that need this identity.\n"
    )


def render(config_path: Path, output: Path) -> None:
    config = json.loads(config_path.read_text())
    profile = _required(config, "profile")
    if profile not in PROFILES:
        raise ValueError(f"profile must be one of: {', '.join(sorted(PROFILES))}")
    namespace = _required(config, "namespace")
    account = _required(config, "serviceAccount")
    if not DNS_LABEL.fullmatch(namespace) or not DNS_LABEL.fullmatch(account):
        raise ValueError("namespace and serviceAccount must be Kubernetes DNS labels")

    output.mkdir(parents=True, exist_ok=True)
    for filename in GENERATED_FILES:
        output.joinpath(filename).unlink(missing_ok=True)
    {"aws": _render_aws, "gcp": _render_gcp, "bare-metal": _render_bare_metal}[profile](
        config, output, namespace, account
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(".generated-permissions"))
    args = parser.parse_args()
    try:
        render(args.config, args.output)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
