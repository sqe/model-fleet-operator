"""Pure Kubernetes resource builders used by the reconciler."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

API_GROUP = "fleet.sqe.io"
API_VERSION = "v1alpha1"
MANAGED_BY = "model-fleet-operator"


class InvalidSpec(ValueError):
    """Raised when a custom resource cannot produce a safe workload."""


GPU_CLASS_LABEL = "model-fleet.sqe.io/gpu-class"
GPU_PRODUCT_LABEL = "nvidia.com/gpu.product"
GPU_CLASS_CAPACITY_GIB = {
    "gpu-24gb": 24,
    "gpu-48gb": 48,
    "gpu-80gb": 80,
}


def gpu_class(minimum_memory_gib: int) -> str:
    """Map a minimum per-GPU memory requirement to a portable capacity class."""
    if minimum_memory_gib <= 0:
        raise InvalidSpec("spec.accelerator.minimumMemoryGiB must be positive")
    for upper_bound, name in (
        (24, "gpu-24gb"),
        (48, "gpu-48gb"),
        (80, "gpu-80gb"),
    ):
        if minimum_memory_gib <= upper_bound:
            return name
    return "gpu-80gb-plus"


def gpu_memory_requirement(accelerator: Mapping[str, Any]) -> int | None:
    """Calculate the minimum usable memory required on each selected GPU."""
    count = accelerator.get("count", 1)
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise InvalidSpec("spec.accelerator.count must be a positive whole number")

    declared = accelerator.get("minimumMemoryGiB")
    if declared is not None and (
        not isinstance(declared, int) or isinstance(declared, bool) or declared < 1
    ):
        raise InvalidSpec("spec.accelerator.minimumMemoryGiB must be a positive whole number")

    fit = accelerator.get("fit")
    if fit is None:
        return declared
    modules = fit.get("modules", [])
    if not modules:
        raise InvalidSpec("spec.accelerator.fit.modules must not be empty")

    sharded_gib = 0.0
    replicated_gib = 0.0
    for module in modules:
        name = module.get("name")
        memory = module.get("memoryGiB")
        distribution = module.get("distribution", "replicated")
        if not name:
            raise InvalidSpec("each spec.accelerator.fit.modules entry requires name")
        if (
            not isinstance(memory, (int, float))
            or isinstance(memory, bool)
            or not math.isfinite(memory)
            or memory <= 0
        ):
            raise InvalidSpec(f"GPU fit module {name} memoryGiB must be positive")
        if distribution == "sharded":
            sharded_gib += memory
        elif distribution == "replicated":
            replicated_gib += memory
        else:
            raise InvalidSpec(f"GPU fit module {name} distribution must be sharded or replicated")

    margin = fit.get("safetyMarginPercent", 10)
    if (
        not isinstance(margin, (int, float))
        or isinstance(margin, bool)
        or not math.isfinite(margin)
        or not 0 <= margin <= 100
    ):
        raise InvalidSpec("spec.accelerator.fit.safetyMarginPercent must be between 0 and 100")
    calculated = math.ceil((sharded_gib / count + replicated_gib) * (1 + margin / 100))
    return max(declared or 0, calculated)


def _apply_gpu_product_affinity(pod_spec: dict[str, Any], products: list[str]) -> None:
    if not products or any(not isinstance(product, str) or not product for product in products):
        raise InvalidSpec("spec.accelerator.products must contain at least one GPU product")
    node_selector = pod_spec.get("nodeSelector", {})
    selected_product = node_selector.get(GPU_PRODUCT_LABEL)
    if selected_product and selected_product not in products:
        raise InvalidSpec("spec.accelerator.products conflicts with spec.nodeSelector")
    if selected_product:
        return

    affinity = deepcopy(pod_spec.get("affinity", {}))
    node_affinity = affinity.setdefault("nodeAffinity", {})
    required = node_affinity.setdefault("requiredDuringSchedulingIgnoredDuringExecution", {})
    terms = required.setdefault("nodeSelectorTerms", [{}])
    expression = {"key": GPU_PRODUCT_LABEL, "operator": "In", "values": products}
    for term in terms:
        expressions = term.setdefault("matchExpressions", [])
        if any(item.get("key") == GPU_PRODUCT_LABEL for item in expressions):
            raise InvalidSpec("spec.accelerator.products conflicts with spec.affinity")
        expressions.append(deepcopy(expression))
    pod_spec["affinity"] = affinity


def _apply_accelerator(
    spec: Mapping[str, Any], pod_spec: dict[str, Any], container: dict[str, Any]
) -> int | None:
    accelerator = spec.get("accelerator")
    if not accelerator:
        return None
    count = accelerator.get("count", 1)
    minimum_memory = gpu_memory_requirement(accelerator)
    selected_class = accelerator.get("class", "auto")
    if selected_class == "auto":
        if minimum_memory is None:
            raise InvalidSpec(
                "spec.accelerator.minimumMemoryGiB or spec.accelerator.fit is required "
                "for automatic selection"
            )
        selected_class = gpu_class(minimum_memory)
    elif selected_class not in {*GPU_CLASS_CAPACITY_GIB, "gpu-80gb-plus"}:
        raise InvalidSpec(f"unsupported spec.accelerator.class {selected_class}")
    elif maximum_memory := GPU_CLASS_CAPACITY_GIB.get(selected_class):
        if minimum_memory is not None and minimum_memory > maximum_memory:
            raise InvalidSpec(
                f"spec.accelerator.class {selected_class} cannot fit the "
                f"{minimum_memory} GiB per-GPU requirement"
            )

    resources = deepcopy(container.get("resources", {}))
    requests = resources.setdefault("requests", {})
    limits = resources.setdefault("limits", {})
    try:
        existing = int(requests.get("nvidia.com/gpu", count))
    except (TypeError, ValueError) as error:
        raise InvalidSpec("the container GPU request must be a whole number") from error
    if existing != count:
        raise InvalidSpec("spec.accelerator.count conflicts with the container GPU request")
    requests["nvidia.com/gpu"] = str(count)
    limits["nvidia.com/gpu"] = str(count)
    container["resources"] = resources

    node_selector = deepcopy(pod_spec.get("nodeSelector", {}))
    if node_selector.get(GPU_CLASS_LABEL, selected_class) != selected_class:
        raise InvalidSpec("spec.accelerator.class conflicts with spec.nodeSelector")
    node_selector[GPU_CLASS_LABEL] = selected_class
    pod_spec["nodeSelector"] = node_selector
    if "products" in accelerator:
        _apply_gpu_product_affinity(pod_spec, accelerator["products"])
    return minimum_memory


def _labels(name: str, component: str) -> dict[str, str]:
    return {
        "app.kubernetes.io/name": name,
        "app.kubernetes.io/component": component,
        "app.kubernetes.io/managed-by": MANAGED_BY,
    }


def _owner(body: Mapping[str, Any]) -> list[dict[str, Any]]:
    metadata = body["metadata"]
    return [
        {
            "apiVersion": f"{API_GROUP}/{API_VERSION}",
            "kind": body["kind"],
            "name": metadata["name"],
            "uid": metadata["uid"],
            "controller": True,
            "blockOwnerDeletion": True,
        }
    ]


def spec_hash(spec: Mapping[str, Any]) -> str:
    """Return a stable short hash for rollout and drift diagnostics."""
    payload = json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()[:12]


def _container(spec: Mapping[str, Any], name: str) -> dict[str, Any]:
    container_spec = spec["container"]
    container: dict[str, Any] = {
        "name": name,
        "image": container_spec["image"],
        "imagePullPolicy": container_spec.get("imagePullPolicy", "IfNotPresent"),
        "ports": [{"name": "http", "containerPort": container_spec.get("port", 8080)}],
        "resources": container_spec.get(
            "resources",
            {"requests": {"cpu": "500m", "memory": "1Gi"}},
        ),
        "securityContext": {
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
        },
    }
    for field in ("command", "args", "env", "envFrom", "volumeMounts"):
        if value := container_spec.get(field):
            container[field] = value

    env = list(container.get("env", []))
    model = spec["model"]
    defined = {item["name"] for item in env}
    for key, value in (
        ("MODEL_NAME", model["name"]),
        ("MODEL_VERSION", model.get("version")),
        ("MODEL_URI", model.get("uri")),
    ):
        if value is not None and key not in defined:
            env.append({"name": key, "value": str(value)})
    if env:
        container["env"] = env
    return container


def _init_containers(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    containers = deepcopy(spec.get("initContainers", []))
    for container in containers:
        if not container.get("name") or not container.get("image"):
            raise InvalidSpec("each spec.initContainers entry requires name and image")
        security_context = container.setdefault("securityContext", {})
        capabilities = security_context.setdefault("capabilities", {})
        if security_context.get("privileged") or security_context.get("allowPrivilegeEscalation"):
            raise InvalidSpec("spec.initContainers cannot request privileged execution")
        if capabilities.get("add"):
            raise InvalidSpec("spec.initContainers cannot add Linux capabilities")
        security_context["allowPrivilegeEscalation"] = False
        capabilities.setdefault("drop", ["ALL"])
    return containers


def _apply_datasets(
    container: dict[str, Any], pod_spec: dict[str, Any], datasets: list[dict[str, Any]]
) -> None:
    if not datasets:
        return
    env = list(container.get("env", []))
    if any(item.get("name") == "MODEL_FLEET_DATASETS_JSON" for item in env):
        raise InvalidSpec("MODEL_FLEET_DATASETS_JSON is reserved for resolved datasets")
    env.append(
        {
            "name": "MODEL_FLEET_DATASETS_JSON",
            "value": json.dumps(datasets, sort_keys=True, separators=(",", ":")),
        }
    )
    container["env"] = env

    volumes = deepcopy(pod_spec.get("volumes", []))
    mounts = deepcopy(container.get("volumeMounts", []))
    volume_names = {volume["name"] for volume in volumes}
    mount_paths = {mount["mountPath"] for mount in mounts}
    for dataset in datasets:
        pvc = dataset.get("storage", {}).get("pvc")
        if not pvc:
            continue
        mount_path = dataset.get("mountPath")
        if not mount_path:
            raise InvalidSpec(f"dataset {dataset['name']} requires mountPath for PVC storage")
        if mount_path in mount_paths:
            raise InvalidSpec(
                f"dataset mountPath {mount_path} conflicts with an existing volume mount"
            )
        volume_name = f"dataset-{hashlib.sha256(dataset['resourceName'].encode()).hexdigest()[:10]}"
        if volume_name not in volume_names:
            volumes.append(
                {
                    "name": volume_name,
                    "persistentVolumeClaim": {"claimName": pvc["claimName"], "readOnly": True},
                }
            )
            volume_names.add(volume_name)
        mount = {"name": volume_name, "mountPath": mount_path, "readOnly": True}
        if pvc.get("subPath"):
            mount["subPath"] = pvc["subPath"]
        mounts.append(mount)
        mount_paths.add(mount_path)
    if volumes:
        pod_spec["volumes"] = volumes
    if mounts:
        container["volumeMounts"] = mounts


def build_deployment(body: Mapping[str, Any]) -> dict[str, Any]:
    """Build the inference Deployment owned by an InferenceService."""
    spec = body["spec"]
    name = body["metadata"]["name"]
    if not spec.get("model", {}).get("name"):
        raise InvalidSpec("spec.model.name is required")
    if not spec.get("container", {}).get("image"):
        raise InvalidSpec("spec.container.image is required")

    labels = _labels(name, "inference")
    replicas = 0 if spec.get("suspend") else spec.get("replicas", 1)
    if spec.get("autoscaling", {}).get("enabled") and not spec.get("suspend"):
        replicas = max(replicas, spec["autoscaling"].get("minReplicas", 0))

    container = _container(spec, name)
    pod_spec: dict[str, Any] = {
        "serviceAccountName": spec.get("serviceAccountName", "default"),
        "automountServiceAccountToken": spec.get("automountServiceAccountToken", False),
        "containers": [container],
        "terminationGracePeriodSeconds": spec.get("terminationGracePeriodSeconds", 60),
    }
    for field in (
        "nodeSelector",
        "tolerations",
        "affinity",
        "volumes",
        "imagePullSecrets",
    ):
        if value := spec.get(field):
            pod_spec[field] = value
    if spec.get("initContainers"):
        pod_spec["initContainers"] = _init_containers(spec)
    required_gpu_memory = _apply_accelerator(spec, pod_spec, container)

    annotations = {"fleet.sqe.io/spec-hash": spec_hash(spec)}
    if required_gpu_memory is not None:
        annotations["fleet.sqe.io/gpu-memory-per-device-gib"] = str(required_gpu_memory)

    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": name,
            "namespace": body["metadata"]["namespace"],
            "labels": labels,
            "ownerReferences": _owner(body),
        },
        "spec": {
            "replicas": replicas,
            "selector": {"matchLabels": labels},
            "strategy": {"type": "RollingUpdate"},
            "template": {
                "metadata": {
                    "labels": labels,
                    "annotations": annotations,
                },
                "spec": pod_spec,
            },
        },
    }


def build_service(body: Mapping[str, Any]) -> dict[str, Any]:
    """Build the stable Service in front of an inference Deployment."""
    spec = body["spec"]
    name = body["metadata"]["name"]
    service = spec.get("service", {})
    labels = _labels(name, "inference")
    metadata: dict[str, Any] = {
        "name": name,
        "namespace": body["metadata"]["namespace"],
        "labels": labels,
        "ownerReferences": _owner(body),
    }
    if service.get("annotations"):
        metadata["annotations"] = service["annotations"]
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": metadata,
        "spec": {
            "type": service.get("type", "ClusterIP"),
            "selector": labels,
            "ports": [
                {
                    "name": "http",
                    "port": service.get("port", 80),
                    "targetPort": "http",
                }
            ],
        },
    }


def build_http_route(body: Mapping[str, Any]) -> dict[str, Any] | None:
    """Build an HTTPRoute attached to the shared Cilium Gateway."""
    spec = body["spec"]
    gateway = spec.get("gateway", {})
    if not gateway.get("enabled"):
        return None
    name = body["metadata"]["name"]
    namespace = body["metadata"]["namespace"]
    path_prefix = gateway.get("pathPrefix", "/")
    if not path_prefix.startswith("/"):
        raise InvalidSpec("spec.gateway.pathPrefix must start with /")
    parent_ref: dict[str, Any] = {
        "name": gateway.get("name", "model-fleet"),
        "namespace": gateway.get("namespace", "model-fleet-system"),
    }
    if section_name := gateway.get("sectionName"):
        parent_ref["sectionName"] = section_name
    route_spec: dict[str, Any] = {
        "parentRefs": [parent_ref],
        "rules": [
            {
                "matches": [{"path": {"type": "PathPrefix", "value": path_prefix}}],
                "backendRefs": [{"name": name, "port": spec.get("service", {}).get("port", 80)}],
            }
        ],
    }
    if hostnames := gateway.get("hostnames"):
        route_spec["hostnames"] = hostnames
    return {
        "apiVersion": "gateway.networking.k8s.io/v1",
        "kind": "HTTPRoute",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": _labels(name, "routing"),
            "ownerReferences": _owner(body),
        },
        "spec": route_spec,
    }


def build_scaled_object(body: Mapping[str, Any]) -> dict[str, Any] | None:
    """Build a KEDA ScaledObject, or None when autoscaling is disabled/suspended."""
    spec = body["spec"]
    autoscaling = spec.get("autoscaling", {})
    if not autoscaling.get("enabled") or spec.get("suspend"):
        return None
    triggers = autoscaling.get("triggers", [])
    if not triggers:
        raise InvalidSpec("spec.autoscaling.triggers must not be empty when autoscaling is enabled")

    name = body["metadata"]["name"]
    scaled_spec: dict[str, Any] = {
        "scaleTargetRef": {"name": name},
        "pollingInterval": autoscaling.get("pollingInterval", 30),
        "cooldownPeriod": autoscaling.get("cooldownPeriod", 300),
        "minReplicaCount": 1 if spec.get("forceActive") else autoscaling.get("minReplicas", 0),
        "maxReplicaCount": autoscaling.get("maxReplicas", 1),
        "triggers": triggers,
    }
    if fallback := autoscaling.get("fallback"):
        scaled_spec["fallback"] = fallback
    return {
        "apiVersion": "keda.sh/v1alpha1",
        "kind": "ScaledObject",
        "metadata": {
            "name": name,
            "namespace": body["metadata"]["namespace"],
            "labels": _labels(name, "autoscaling"),
            "ownerReferences": _owner(body),
        },
        "spec": scaled_spec,
    }


def build_training_job(
    body: Mapping[str, Any], resolved_datasets: list[dict[str, Any]] | None = None
) -> dict[str, Any] | None:
    """Build an immutable Kubernetes Job for a TrainingRun."""
    spec = body["spec"]
    if spec.get("cancelled"):
        return None
    name = body["metadata"]["name"]
    if not spec.get("image"):
        raise InvalidSpec("spec.image is required")

    container: dict[str, Any] = {
        "name": "trainer",
        "image": spec["image"],
        "imagePullPolicy": spec.get("imagePullPolicy", "IfNotPresent"),
        "resources": spec.get("resources", {"requests": {"cpu": "1", "memory": "2Gi"}}),
        "securityContext": {
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
        },
    }
    for field in ("command", "args", "env", "envFrom", "volumeMounts"):
        if value := spec.get(field):
            container[field] = value

    pod_spec: dict[str, Any] = {
        "restartPolicy": spec.get("restartPolicy", "Never"),
        "serviceAccountName": spec.get("serviceAccountName", "default"),
        "automountServiceAccountToken": spec.get("automountServiceAccountToken", False),
        "containers": [container],
    }
    for field in (
        "nodeSelector",
        "tolerations",
        "affinity",
        "volumes",
        "imagePullSecrets",
    ):
        if value := spec.get(field):
            pod_spec[field] = value
    if spec.get("initContainers"):
        pod_spec["initContainers"] = _init_containers(spec)
    _apply_datasets(container, pod_spec, resolved_datasets or [])
    required_gpu_memory = _apply_accelerator(spec, pod_spec, container)

    pod_annotations: dict[str, str] = {}
    if required_gpu_memory is not None:
        pod_annotations["fleet.sqe.io/gpu-memory-per-device-gib"] = str(required_gpu_memory)

    job_spec: dict[str, Any] = {
        "backoffLimit": spec.get("backoffLimit", 0),
        "parallelism": spec.get("parallelism", 1),
        "completions": spec.get("completions", 1),
        "suspend": spec.get("suspend", False),
        "template": {
            "metadata": {"labels": _labels(name, "training"), "annotations": pod_annotations},
            "spec": pod_spec,
        },
    }
    for field in ("ttlSecondsAfterFinished", "activeDeadlineSeconds"):
        if value := spec.get(field):
            job_spec[field] = value

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": body["metadata"]["namespace"],
            "labels": _labels(name, "training"),
            "annotations": {"fleet.sqe.io/spec-hash": spec_hash(spec)},
            "ownerReferences": _owner(body),
        },
        "spec": job_spec,
    }
