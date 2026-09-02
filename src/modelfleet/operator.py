"""Kopf reconciler for model inference, training, datasets, and managed agents."""

from __future__ import annotations

import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import kopf
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from modelfleet.metrics import INFERENCE_READY, RECONCILES, TRAINING_PHASE
from modelfleet.resources import (
    InvalidSpec,
    build_deployment,
    build_http_route,
    build_scaled_object,
    build_service,
    build_training_job,
)

LOG = logging.getLogger("modelfleet.operator")
GROUP = "fleet.sqe.io"
VERSION = "v1alpha1"
AGENT_REGISTRATION_ANNOTATION = "fleet.sqe.io/agent-registration"


def _load_config() -> None:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()


def _upsert(
    read_method: Any,
    create_method: Any,
    patch_method: Any,
    body: dict[str, Any],
) -> None:
    """Create a typed object if absent, otherwise merge-patch it."""
    name = body["metadata"]["name"]
    namespace = body["metadata"]["namespace"]
    try:
        read_method(name=name, namespace=namespace)
    except ApiException as error:
        if error.status != 404:
            raise
        create_method(namespace=namespace, body=body)
    else:
        patch_method(name=name, namespace=namespace, body=body)


def _apply_custom(
    api: client.CustomObjectsApi,
    group: str,
    version: str,
    plural: str,
    body: dict[str, Any],
) -> None:
    args = {
        "group": group,
        "version": version,
        "namespace": body["metadata"]["namespace"],
        "plural": plural,
        "name": body["metadata"]["name"],
    }
    try:
        api.get_namespaced_custom_object(**args)
    except ApiException as error:
        if error.status != 404:
            raise
        del args["name"]
        api.create_namespaced_custom_object(**args, body=body)
    else:
        api.patch_namespaced_custom_object(**args, body=body)


def _delete_custom(
    api: client.CustomObjectsApi,
    group: str,
    version: str,
    plural: str,
    namespace: str,
    name: str,
) -> None:
    try:
        api.delete_namespaced_custom_object(group, version, namespace, plural, name)
    except ApiException as error:
        if error.status != 404:
            raise


def reconcile_inference(body: dict[str, Any], patch: kopf.Patch) -> None:
    """Converge one InferenceService and its Deployment, Service, route, and scaler."""
    apps = client.AppsV1Api()
    core = client.CoreV1Api()
    custom = client.CustomObjectsApi()
    namespace = body["metadata"]["namespace"]
    name = body["metadata"]["name"]

    try:
        _upsert(
            apps.read_namespaced_deployment,
            apps.create_namespaced_deployment,
            apps.patch_namespaced_deployment,
            build_deployment(body),
        )
        _upsert(
            core.read_namespaced_service,
            core.create_namespaced_service,
            core.patch_namespaced_service,
            build_service(body),
        )

        scaled_object = build_scaled_object(body)
        if scaled_object:
            _apply_custom(custom, "keda.sh", "v1alpha1", "scaledobjects", scaled_object)
        else:
            _delete_custom(custom, "keda.sh", "v1alpha1", "scaledobjects", namespace, name)

        route = build_http_route(body)
        if route:
            _apply_custom(
                custom,
                "gateway.networking.k8s.io",
                "v1",
                "httproutes",
                route,
            )
        else:
            _delete_custom(
                custom,
                "gateway.networking.k8s.io",
                "v1",
                "httproutes",
                namespace,
                name,
            )
    except InvalidSpec as error:
        patch.status.update(
            {
                "phase": "Invalid",
                "message": str(error),
                "observedGeneration": body["metadata"]["generation"],
            }
        )
        raise kopf.PermanentError(str(error)) from error
    except ApiException as error:
        if error.status == 404 and "keda.sh" in str(error.body):
            raise kopf.TemporaryError("KEDA is not installed", delay=30) from error
        raise

    suspended = body["spec"].get("suspend", False)
    patch.status.update(
        {
            "phase": "Suspended" if suspended else "Reconciling",
            "message": "desired resources applied",
            "url": _inference_url(body),
            "observedGeneration": body["metadata"]["generation"],
        }
    )


def _inference_url(body: dict[str, Any]) -> str:
    gateway = body["spec"].get("gateway", {})
    hostnames = gateway.get("hostnames", [])
    if gateway.get("enabled") and hostnames:
        scheme = "https" if gateway.get("sectionName") == "https" else "http"
        return f"{scheme}://{hostnames[0]}{gateway.get('pathPrefix', '/')}"
    metadata = body["metadata"]
    port = body["spec"].get("service", {}).get("port", 80)
    return f"http://{metadata['name']}.{metadata['namespace']}.svc:{port}"


def reconcile_agent_registration(
    body: dict[str, Any], patch: kopf.Patch, apps: Any | None = None
) -> None:
    """Apply AgentRegistration replica intent to an explicitly linked Deployment."""
    spec = body["spec"]
    runtime = spec.get("runtime")
    if not runtime:
        patch.status.update(
            {
                "phase": "Registered",
                "message": "agent metadata registered; no managed runtime",
                "readyReplicas": 0,
                "observedGeneration": body["metadata"]["generation"],
            }
        )
        return
    apps = apps or client.AppsV1Api()
    namespace = body["metadata"]["namespace"]
    registration_name = body["metadata"]["name"]
    deployment_name = runtime["deploymentName"]
    try:
        deployment = apps.read_namespaced_deployment(deployment_name, namespace)
    except ApiException as error:
        if error.status != 404:
            raise
        patch.status.update(
            {
                "phase": "Missing",
                "message": f"managed Deployment {deployment_name} was not found",
                "readyReplicas": 0,
                "observedGeneration": body["metadata"]["generation"],
            }
        )
        raise kopf.TemporaryError(
            f"managed Deployment {deployment_name} was not found", delay=30
        ) from error
    annotations = deployment.metadata.annotations or {}
    if annotations.get(AGENT_REGISTRATION_ANNOTATION) != registration_name:
        message = (
            f"Deployment {deployment_name} must have annotation "
            f"{AGENT_REGISTRATION_ANNOTATION}={registration_name}"
        )
        patch.status.update(
            {
                "phase": "Invalid",
                "message": message,
                "readyReplicas": 0,
                "observedGeneration": body["metadata"]["generation"],
            }
        )
        raise kopf.PermanentError(message)
    desired = 0 if spec.get("suspend") else runtime.get("activeReplicas", 1)
    if deployment.spec.replicas != desired:
        apps.patch_namespaced_deployment_scale(
            deployment_name, namespace, {"spec": {"replicas": desired}}
        )
    ready = deployment.status.ready_replicas or 0
    patch.status.update(
        {
            "phase": "Suspended" if spec.get("suspend") else "Active",
            "message": f"managed Deployment target is {desired} replica(s)",
            "readyReplicas": ready,
            "observedGeneration": body["metadata"]["generation"],
        }
    )


def refresh_inference_status(body: dict[str, Any], patch: kopf.Patch) -> None:
    if body["spec"].get("suspend"):
        patch.status.update({"phase": "Suspended", "readyReplicas": 0})
        return
    try:
        deployment = client.AppsV1Api().read_namespaced_deployment(
            body["metadata"]["name"], body["metadata"]["namespace"]
        )
    except ApiException as error:
        if error.status == 404:
            patch.status.update({"phase": "Reconciling", "readyReplicas": 0})
            return
        raise
    ready = deployment.status.ready_replicas or 0
    desired = deployment.spec.replicas or 0
    patch.status.update(
        {
            "phase": "Ready" if desired > 0 and ready == desired else "Scaling",
            "readyReplicas": ready,
            "message": f"{ready}/{desired} replicas ready",
        }
    )


def reconcile_training(body: dict[str, Any], patch: kopf.Patch) -> None:
    batch = client.BatchV1Api()
    custom = client.CustomObjectsApi()
    name = body["metadata"]["name"]
    namespace = body["metadata"]["namespace"]
    try:
        datasets = [] if body["spec"].get("cancelled") else resolve_training_datasets(body, custom)
        job = build_training_job(body, datasets)
    except InvalidSpec as error:
        patch.status.update(
            {
                "phase": "Invalid",
                "message": str(error),
                "observedGeneration": body["metadata"]["generation"],
            }
        )
        raise kopf.PermanentError(str(error)) from error
    except ApiException as error:
        if error.status == 404:
            raise kopf.TemporaryError("referenced Dataset was not found", delay=30) from error
        raise
    if job is None:
        try:
            batch.delete_namespaced_job(name, namespace, propagation_policy="Foreground")
        except ApiException as error:
            if error.status != 404:
                raise
        patch.status.update({"phase": "Cancelled", "message": "training job cancelled"})
        return

    try:
        _upsert(
            batch.read_namespaced_job,
            batch.create_namespaced_job,
            batch.patch_namespaced_job,
            job,
        )
    except ApiException as error:
        if error.status == 422:
            raise kopf.PermanentError(
                "TrainingRun job fields are immutable; create a new TrainingRun for a new attempt"
            ) from error
        raise
    patch.status.update(
        {
            "phase": "Suspended" if body["spec"].get("suspend") else "Pending",
            "jobName": name,
            "message": "training job applied",
            "observedGeneration": body["metadata"]["generation"],
        }
    )


def resolve_training_datasets(
    body: dict[str, Any], api: client.CustomObjectsApi
) -> list[dict[str, Any]]:
    """Resolve versioned Dataset references in the TrainingRun namespace."""
    namespace = body["metadata"]["namespace"]
    service_account = body["spec"].get("serviceAccountName", "default")
    resolved = []
    for reference in body["spec"].get("datasets", []):
        resource_name = reference["datasetRef"]
        dataset = api.get_namespaced_custom_object(
            GROUP, VERSION, namespace, "datasets", resource_name
        )
        spec = dataset["spec"]
        expected_version = reference["expectedVersion"]
        if spec["version"] != expected_version:
            raise InvalidSpec(
                f"Dataset {resource_name} is version {spec['version']}, expected {expected_version}"
            )
        allowed = spec.get("allowedServiceAccounts", [])
        if allowed and service_account not in allowed:
            raise InvalidSpec(
                f"service account {service_account} is not allowed to use Dataset {resource_name}"
            )
        resolved.append(
            {
                "name": reference["name"],
                "resourceName": resource_name,
                "uri": spec["uri"],
                "version": spec["version"],
                "format": spec["format"],
                "checksum": spec.get("checksum"),
                "splits": spec.get("splits", []),
                "mountPath": reference.get("mountPath"),
                "storage": spec.get("storage", {}),
            }
        )
    return resolved


def refresh_training_status(body: dict[str, Any], patch: kopf.Patch) -> None:
    if body["spec"].get("cancelled"):
        patch.status["phase"] = "Cancelled"
        return
    try:
        job = client.BatchV1Api().read_namespaced_job(
            body["metadata"]["name"], body["metadata"]["namespace"]
        )
    except ApiException as error:
        if error.status == 404:
            patch.status["phase"] = "Pending"
            return
        raise
    if job.spec.suspend:
        phase = "Suspended"
    elif job.status.succeeded:
        phase = "Succeeded"
    elif job.status.failed:
        phase = "Failed"
    elif job.status.active:
        phase = "Running"
    else:
        phase = "Pending"
    patch.status.update(
        {
            "phase": phase,
            "jobName": body["metadata"]["name"],
            "startTime": _iso(job.status.start_time),
            "completionTime": _iso(job.status.completion_time),
        }
    )


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, **_: Any) -> None:
    _load_config()
    settings.posting.level = logging.INFO
    settings.persistence.finalizer = "fleet.sqe.io/kopf-finalizer"


@kopf.on.create(GROUP, VERSION, "inferenceservices")
@kopf.on.update(GROUP, VERSION, "inferenceservices")
@kopf.on.resume(GROUP, VERSION, "inferenceservices")
def inference_handler(body: dict[str, Any], patch: kopf.Patch, **_: Any) -> None:
    try:
        reconcile_inference(body, patch)
    except Exception:
        RECONCILES.labels("InferenceService", "error").inc()
        raise
    RECONCILES.labels("InferenceService", "ok").inc()


@kopf.timer(GROUP, VERSION, "inferenceservices", interval=15.0, sharp=True)
def inference_status_handler(body: dict[str, Any], patch: kopf.Patch, **_: Any) -> None:
    refresh_inference_status(body, patch)
    INFERENCE_READY.labels(body["metadata"]["namespace"], body["metadata"]["name"]).set(
        patch.status.get("readyReplicas", 0)
    )


@kopf.on.create(GROUP, VERSION, "trainingruns")
@kopf.on.update(GROUP, VERSION, "trainingruns")
@kopf.on.resume(GROUP, VERSION, "trainingruns")
def training_handler(body: dict[str, Any], patch: kopf.Patch, **_: Any) -> None:
    try:
        reconcile_training(body, patch)
    except Exception:
        RECONCILES.labels("TrainingRun", "error").inc()
        raise
    RECONCILES.labels("TrainingRun", "ok").inc()


@kopf.timer(GROUP, VERSION, "trainingruns", interval=15.0, sharp=True)
def training_status_handler(body: dict[str, Any], patch: kopf.Patch, **_: Any) -> None:
    refresh_training_status(body, patch)
    namespace, name = body["metadata"]["namespace"], body["metadata"]["name"]
    current = patch.status.get("phase", "Unknown")
    for phase in ("Pending", "Running", "Suspended", "Succeeded", "Failed", "Cancelled"):
        TRAINING_PHASE.labels(namespace, name, phase).set(phase == current)


@kopf.on.create(GROUP, VERSION, "datasets")
@kopf.on.update(GROUP, VERSION, "datasets")
@kopf.on.resume(GROUP, VERSION, "datasets")
def dataset_handler(body: dict[str, Any], patch: kopf.Patch, **_: Any) -> None:
    patch.status.update(
        {
            "phase": "Registered",
            "message": "immutable dataset version registered",
            "observedGeneration": body["metadata"]["generation"],
        }
    )
    RECONCILES.labels("Dataset", "ok").inc()


@kopf.on.create(GROUP, VERSION, "agentregistrations")
@kopf.on.update(GROUP, VERSION, "agentregistrations")
@kopf.on.resume(GROUP, VERSION, "agentregistrations")
@kopf.timer(GROUP, VERSION, "agentregistrations", interval=15.0, sharp=True)
def agent_registration_handler(body: dict[str, Any], patch: kopf.Patch, **_: Any) -> None:
    try:
        reconcile_agent_registration(body, patch)
    except Exception:
        RECONCILES.labels("AgentRegistration", "error").inc()
        raise
    RECONCILES.labels("AgentRegistration", "ok").inc()


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/metrics":
            payload = generate_latest()
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPE_LATEST)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.path not in ("/healthz", "/readyz"):
            self.send_error(404)
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok\n")

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s"
    )
    health = ThreadingHTTPServer(("0.0.0.0", 8080), _HealthHandler)
    threading.Thread(target=health.serve_forever, daemon=True, name="health-server").start()
    kopf.run(standalone=True)


if __name__ == "__main__":
    main()
