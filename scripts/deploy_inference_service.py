#!/usr/bin/env python3
"""Construct and optionally apply a Model Fleet InferenceService as JSON."""

import argparse
import json
import re
import subprocess
import sys

DNS_LABEL = re.compile(r"^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?$")


def kubernetes_name(value: str) -> str:
    if not DNS_LABEL.fullmatch(value):
        raise argparse.ArgumentTypeError(f"invalid Kubernetes DNS label: {value!r}")
    return value


def build_manifest(args: argparse.Namespace) -> dict:
    container = {
        "image": args.image,
        "port": args.port,
        "resources": {
            "requests": {"cpu": args.cpu, "memory": args.memory},
            "limits": {"cpu": args.cpu, "memory": args.memory},
        },
    }
    spec = {
        "model": {
            "name": args.model_name,
            "version": args.model_version,
            "uri": args.model_uri,
        },
        "container": container,
        "replicas": args.replicas,
        "service": {"port": args.service_port},
    }
    if args.image_pull_secret:
        spec["imagePullSecrets"] = [{"name": args.image_pull_secret}]
    if args.gateway_host:
        spec["gateway"] = {
            "enabled": True,
            "name": args.gateway_name,
            "namespace": args.gateway_namespace,
            "sectionName": args.gateway_section,
            "hostnames": [args.gateway_host],
            "pathPrefix": "/",
        }
    return {
        "apiVersion": "fleet.sqe.io/v1alpha1",
        "kind": "InferenceService",
        "metadata": {"name": args.name, "namespace": args.namespace},
        "spec": spec,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--image", required=True)
    result.add_argument("--model-name", required=True)
    result.add_argument("--model-version", required=True)
    result.add_argument("--model-uri", required=True)
    result.add_argument("--namespace", type=kubernetes_name, default="default")
    result.add_argument("--name", type=kubernetes_name, required=True)
    result.add_argument("--port", type=int, default=8080)
    result.add_argument("--service-port", type=int, default=80)
    result.add_argument("--replicas", type=int, default=1)
    result.add_argument("--cpu", default="500m")
    result.add_argument("--memory", default="1Gi")
    result.add_argument("--image-pull-secret", type=kubernetes_name)
    result.add_argument("--gateway-host")
    result.add_argument("--gateway-name", type=kubernetes_name, default="model-fleet")
    result.add_argument("--gateway-namespace", type=kubernetes_name, default="model-fleet-system")
    result.add_argument("--gateway-section", choices=("http", "https"), default="http")
    result.add_argument("--apply", action="store_true")
    result.add_argument("--dry-run", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not 1 <= args.port <= 65535 or not 1 <= args.service_port <= 65535:
        parser().error("--port and --service-port must be between 1 and 65535")
    if args.replicas < 0:
        parser().error("--replicas cannot be negative")
    manifest = build_manifest(args)
    payload = json.dumps(manifest, indent=2) + "\n"
    if not args.apply or args.dry_run:
        sys.stdout.write(payload)
        return 0
    subprocess.run(["kubectl", "apply", "-f", "-"], input=payload, text=True, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
