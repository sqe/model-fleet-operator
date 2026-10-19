#!/usr/bin/env python3
"""Generate the controller-only OLM bundle from the Helm chart's deployment/RBAC."""

import base64
import json
import os
import shutil
import subprocess
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "model-fleet-operator"


def main() -> None:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    image = os.environ.get("IMG", f"ghcr.io/sqe/{PACKAGE}:{version}")
    output = ROOT / "dist/olm/bundle"
    if output.exists():
        shutil.rmtree(output)
    (output / "manifests").mkdir(parents=True)
    (output / "metadata").mkdir()
    rendered = subprocess.check_output(
        [
            "helm",
            "template",
            "model-fleet",
            str(ROOT / "charts/model-fleet-operator"),
            "--namespace",
            "model-fleet-system",
            "--set",
            "fullnameOverride=model-fleet",
            "--set",
            "gateway.enabled=false",
        ],
        text=True,
    )
    resources = [doc for doc in yaml.safe_load_all(rendered) if doc]
    deployment = next(doc for doc in resources if doc["kind"] == "Deployment")
    rules = next(doc["rules"] for doc in resources if doc["kind"] == "ClusterRole")
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]
    container["image"] = image
    # The chart ConfigMap configures optional companion services, not this controller.
    container.pop("envFrom", None)
    container["env"] = [{"name": "HOME", "value": "/tmp"}]
    pod["volumes"] = [{"name": "tmp", "emptyDir": {}}]
    container["volumeMounts"] = [{"name": "tmp", "mountPath": "/tmp"}]
    examples = list(yaml.safe_load_all((ROOT / "config/olm/examples.yaml").read_text()))
    owned = []
    for example in examples:
        kind = example["kind"]
        crd = next(
            doc
            for path in (ROOT / "charts/model-fleet-operator/crds").glob("*.yaml")
            if (doc := yaml.safe_load(path.read_text()))["spec"]["names"]["kind"] == kind
        )
        (output / "manifests" / f"{crd['metadata']['name']}.yaml").write_text(
            yaml.safe_dump(crd, sort_keys=False)
        )
        owned.append(
            {
                "name": crd["metadata"]["name"],
                "version": "v1alpha1",
                "kind": kind,
                "displayName": kind,
                "description": f"Declarative Model Fleet {kind} resource.",
            }
        )
    csv = {
        "apiVersion": "operators.coreos.com/v1alpha1",
        "kind": "ClusterServiceVersion",
        "metadata": {
            "name": f"{PACKAGE}.v{version}",
            "annotations": {
                "alm-examples": json.dumps(examples),
                "capabilities": "Basic Install",
                "categories": "AI/Machine Learning",
                "description": "Portable Kubernetes inference, training and dataset registration.",
                "containerImage": image,
                "repository": f"https://github.com/sqe/{PACKAGE}",
                "support": f"https://github.com/sqe/{PACKAGE}/issues",
                "operators.operatorframework.io/arch.amd64": "supported",
                "operators.operatorframework.io/arch.arm64": "supported",
            },
        },
        "spec": {
            "displayName": "Model Fleet Operator",
            "description": """## Model Fleet Operator

Model Fleet manages containerized model inference and training through standard
Kubernetes resources. Source and documentation: https://github.com/sqe/model-fleet-operator.

### Features and usage

Create an InferenceService for a Deployment and Service, a TrainingRun for a Job,
a Dataset for immutable dataset metadata, or an AgentRegistration for agent
metadata and management of explicitly annotated deployments. Optional KEDA
ScaledObjects and Gateway API HTTPRoutes integrate autoscaling and ingress.

This bundle installs one cluster-wide controller and four CRDs. It does not
install the optional Slack/Kafka/registry/supervisor/gateway services, AgentTask
control plane, model weights, inference runtimes, or cluster infrastructure.
Do not run a second Helm-installed controller alongside this OLM installation.

### Prerequisites

Kubernetes 1.34 or newer. CPU-only examples need no GPU, KEDA or Gateway API.
Install KEDA before enabling autoscaling, and Gateway API with a compatible
controller/Gateway before enabling routes. Provision workload images, data,
storage, Secret references and GPU drivers/device plugins separately as needed.
Dataset registration records metadata; it does not download or validate data.
AgentRegistration does not deploy an agent or Kafka broker.

### License

Apache-2.0. Independently maintained by Aziz Kurbanov.
""",
            "version": version,
            "maturity": "alpha",
            "minKubeVersion": "1.34.0",
            "provider": {"name": "Aziz Kurbanov", "url": "https://github.com/sqe"},
            "maintainers": [{"name": "Aziz Kurbanov", "email": "azizbek@gmail.com"}],
            "icon": [
                {
                    "base64data": base64.b64encode(
                        (ROOT / "config/olm/icon.svg").read_bytes()
                    ).decode(),
                    "mediatype": "image/svg+xml",
                }
            ],
            "keywords": ["inference", "training", "models", "datasets", "agents"],
            "links": [{"name": "Documentation", "url": f"https://github.com/sqe/{PACKAGE}"}],
            "installModes": [
                {"type": mode, "supported": mode == "AllNamespaces"}
                for mode in ("OwnNamespace", "SingleNamespace", "MultiNamespace", "AllNamespaces")
            ],
            "customresourcedefinitions": {"owned": owned},
            "install": {
                "strategy": "deployment",
                "spec": {
                    "deployments": [
                        {"name": deployment["metadata"]["name"], "spec": deployment["spec"]}
                    ],
                    "clusterPermissions": [
                        {"serviceAccountName": pod["serviceAccountName"], "rules": rules}
                    ],
                },
            },
        },
    }
    (output / "manifests" / f"{PACKAGE}.clusterserviceversion.yaml").write_text(
        yaml.safe_dump(csv, sort_keys=False)
    )
    labels = {
        "operators.operatorframework.io.bundle.mediatype.v1": "registry+v1",
        "operators.operatorframework.io.bundle.manifests.v1": "manifests/",
        "operators.operatorframework.io.bundle.metadata.v1": "metadata/",
        "operators.operatorframework.io.bundle.package.v1": PACKAGE,
        "operators.operatorframework.io.bundle.channels.v1": "alpha",
        "operators.operatorframework.io.bundle.channel.default.v1": "alpha",
    }
    (output / "metadata/annotations.yaml").write_text(yaml.safe_dump({"annotations": labels}))
    shutil.copyfile(ROOT / "LICENSE", output / "LICENSE")
    (output / "Dockerfile").write_text(
        "FROM scratch\nCOPY manifests /manifests/\nCOPY metadata /metadata/\n"
        "COPY LICENSE /licenses/LICENSE\nLABEL org.opencontainers.image.licenses=Apache-2.0\n"
        + "".join(f"LABEL {key}={json.dumps(value)}\n" for key, value in labels.items())
    )
    print(output)


if __name__ == "__main__":
    main()
