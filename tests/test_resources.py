import json
from copy import deepcopy

import pytest

from modelfleet.resources import (
    InvalidSpec,
    build_deployment,
    build_http_route,
    build_scaled_object,
    build_service,
    build_training_job,
    gpu_class,
    gpu_memory_requirement,
    spec_hash,
)


@pytest.fixture
def inference():
    return {
        "apiVersion": "fleet.sqe.io/v1alpha1",
        "kind": "InferenceService",
        "metadata": {"name": "embed", "namespace": "models", "uid": "u1"},
        "spec": {
            "model": {"name": "bge", "uri": "s3://models/bge"},
            "container": {
                "image": "example/tei:1",
                "port": 8080,
                "resources": {"requests": {"nvidia.com/gpu": "1"}},
            },
            "replicas": 0,
            "autoscaling": {
                "enabled": True,
                "minReplicas": 0,
                "maxReplicas": 4,
                "triggers": [
                    {
                        "type": "prometheus",
                        "metadata": {
                            "serverAddress": "http://prom",
                            "query": "queue",
                            "threshold": "1",
                        },
                    }
                ],
            },
            "service": {"port": 80},
            "gateway": {
                "enabled": True,
                "hostnames": ["embed.example.com"],
                "pathPrefix": "/v1",
            },
        },
    }


def test_deployment_has_model_identity_and_gpu_request(inference):
    deployment = build_deployment(inference)
    pod = deployment["spec"]["template"]
    container = pod["spec"]["containers"][0]
    assert deployment["spec"]["replicas"] == 0
    assert container["resources"]["requests"]["nvidia.com/gpu"] == "1"
    assert {item["name"]: item["value"] for item in container["env"]} == {
        "MODEL_NAME": "bge",
        "MODEL_URI": "s3://models/bge",
    }
    assert pod["spec"]["automountServiceAccountToken"] is False


def test_inference_supports_model_prefetch_init_container(inference):
    fetcher = {
        "name": "model-fetcher",
        "image": "example/model-fetcher:1",
        "volumeMounts": [{"name": "models", "mountPath": "/models"}],
    }
    inference["spec"]["initContainers"] = [fetcher]
    inference["spec"]["volumes"] = [{"name": "models", "emptyDir": {}}]

    pod = build_deployment(inference)["spec"]["template"]["spec"]

    assert pod["initContainers"][0]["image"] == fetcher["image"]
    assert pod["initContainers"][0]["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
    }
    assert pod["volumes"] == [{"name": "models", "emptyDir": {}}]


def test_inference_rejects_privileged_init_container(inference):
    inference["spec"]["initContainers"] = [
        {
            "name": "model-fetcher",
            "image": "example/model-fetcher:1",
            "securityContext": {"privileged": True},
        }
    ]

    with pytest.raises(InvalidSpec, match="privileged execution"):
        build_deployment(inference)


def test_suspend_removes_scaler_and_sets_zero_replicas(inference):
    inference["spec"]["suspend"] = True
    assert build_deployment(inference)["spec"]["replicas"] == 0
    assert build_scaled_object(inference) is None


def test_force_active_pins_keda_minimum(inference):
    inference["spec"]["forceActive"] = True
    scaler = build_scaled_object(inference)
    assert scaler["spec"]["minReplicaCount"] == 1
    assert scaler["spec"]["maxReplicaCount"] == 4


def test_autoscaling_requires_trigger(inference):
    inference["spec"]["autoscaling"]["triggers"] = []
    with pytest.raises(InvalidSpec, match="triggers"):
        build_scaled_object(inference)


def test_service_and_cilium_route_share_port(inference):
    service = build_service(inference)
    route = build_http_route(inference)
    assert service["spec"]["ports"][0]["port"] == 80
    assert route["spec"]["parentRefs"][0]["name"] == "model-fleet"
    assert route["spec"]["rules"][0]["backendRefs"][0] == {"name": "embed", "port": 80}


def test_route_rejects_non_absolute_path(inference):
    inference["spec"]["gateway"]["pathPrefix"] = "v1"
    with pytest.raises(InvalidSpec, match="start with"):
        build_http_route(inference)


def test_route_and_scaler_can_be_disabled(inference):
    inference["spec"].pop("gateway")
    inference["spec"]["autoscaling"]["enabled"] = False
    assert build_http_route(inference) is None
    assert build_scaled_object(inference) is None


def test_invalid_inference_identity_is_rejected(inference):
    inference["spec"]["model"] = {}
    with pytest.raises(InvalidSpec, match="model.name"):
        build_deployment(inference)


@pytest.mark.parametrize(
    ("memory", "expected"),
    [(16, "gpu-24gb"), (24, "gpu-24gb"), (25, "gpu-48gb"), (80, "gpu-80gb"), (81, "gpu-80gb-plus")],
)
def test_gpu_class_selection(memory, expected):
    assert gpu_class(memory) == expected


def test_inference_accelerator_adds_portable_gpu_class(inference):
    inference["spec"]["accelerator"] = {"count": 1, "minimumMemoryGiB": 40}
    pod = build_deployment(inference)["spec"]["template"]["spec"]
    assert pod["nodeSelector"]["model-fleet.sqe.io/gpu-class"] == "gpu-48gb"
    assert pod["containers"][0]["resources"]["limits"]["nvidia.com/gpu"] == "1"


def test_gpu_fit_accounts_for_sharded_and_replicated_modules():
    accelerator = {
        "count": 2,
        "fit": {
            "modules": [
                {"name": "weights", "memoryGiB": 70, "distribution": "sharded"},
                {"name": "kv-cache", "memoryGiB": 20, "distribution": "sharded"},
                {"name": "runtime", "memoryGiB": 4, "distribution": "replicated"},
            ],
            "safetyMarginPercent": 10,
        },
    }
    assert gpu_memory_requirement(accelerator) == 54


def test_gpu_fit_selects_class_and_product_affinity(inference):
    inference["spec"]["accelerator"] = {
        "count": 2,
        "fit": {
            "modules": [
                {"name": "weights", "memoryGiB": 70, "distribution": "sharded"},
                {"name": "kv-cache", "memoryGiB": 20, "distribution": "sharded"},
                {"name": "runtime", "memoryGiB": 4},
            ],
            "safetyMarginPercent": 10,
        },
        "products": ["NVIDIA-A100-SXM4-80GB", "NVIDIA-H100-80GB-HBM3"],
    }
    inference["spec"]["container"]["resources"]["requests"]["nvidia.com/gpu"] = "2"

    pod = build_deployment(inference)["spec"]["template"]

    assert pod["metadata"]["annotations"]["fleet.sqe.io/gpu-memory-per-device-gib"] == "54"
    assert pod["spec"]["nodeSelector"]["model-fleet.sqe.io/gpu-class"] == "gpu-80gb"
    assert pod["spec"]["affinity"]["nodeAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ]["nodeSelectorTerms"][0]["matchExpressions"] == [
        {
            "key": "nvidia.com/gpu.product",
            "operator": "In",
            "values": ["NVIDIA-A100-SXM4-80GB", "NVIDIA-H100-80GB-HBM3"],
        }
    ]


def test_gpu_fit_rejects_class_that_is_too_small(inference):
    inference["spec"]["accelerator"] = {
        "count": 1,
        "class": "gpu-24gb",
        "fit": {"modules": [{"name": "weights", "memoryGiB": 30}]},
    }
    with pytest.raises(InvalidSpec, match="cannot fit"):
        build_deployment(inference)


@pytest.mark.parametrize(
    "accelerator",
    [
        {"count": 1, "fit": {"modules": []}},
        {"count": 1, "fit": {"modules": [{"name": "weights", "memoryGiB": 0}]}},
        {
            "count": 1,
            "fit": {
                "modules": [{"name": "weights", "memoryGiB": 1}],
                "safetyMarginPercent": 101,
            },
        },
        {"count": True, "minimumMemoryGiB": 1},
        {"count": 1, "minimumMemoryGiB": 1, "products": []},
    ],
)
def test_gpu_fit_rejects_invalid_input(inference, accelerator):
    inference["spec"]["accelerator"] = accelerator
    with pytest.raises(InvalidSpec):
        build_deployment(inference)


def test_accelerator_rejects_conflicting_request(inference):
    inference["spec"]["accelerator"] = {"count": 2, "minimumMemoryGiB": 24}
    with pytest.raises(InvalidSpec, match="conflicts"):
        build_deployment(inference)
    inference["spec"]["model"] = {"name": "bge"}
    inference["spec"]["container"] = {}
    with pytest.raises(InvalidSpec, match="container.image"):
        build_deployment(inference)


def test_accelerator_rejects_non_numeric_gpu_request(inference):
    inference["spec"]["accelerator"] = {"count": 1, "minimumMemoryGiB": 24}
    inference["spec"]["container"]["resources"] = {"requests": {"nvidia.com/gpu": "one"}}
    with pytest.raises(InvalidSpec, match="whole number"):
        build_deployment(inference)


def test_training_job_and_cancel():
    body = {
        "kind": "TrainingRun",
        "metadata": {"name": "run-1", "namespace": "models", "uid": "u2"},
        "spec": {
            "image": "example/trainer:1",
            "parallelism": 2,
            "resources": {"requests": {"nvidia.com/gpu": "1"}},
        },
    }
    job = build_training_job(body)
    assert job["spec"]["parallelism"] == 2
    assert job["spec"]["template"]["spec"]["restartPolicy"] == "Never"
    cancelled = deepcopy(body)
    cancelled["spec"]["cancelled"] = True
    assert build_training_job(cancelled) is None


def test_training_resolved_dataset_manifest_and_read_only_pvc():
    body = {
        "kind": "TrainingRun",
        "metadata": {"name": "run-1", "namespace": "models", "uid": "u2"},
        "spec": {"image": "example/trainer:1"},
    }
    datasets = [
        {
            "name": "training",
            "resourceName": "support-v3",
            "uri": "pvc://research-data/support/v3",
            "version": "3.0.0",
            "format": "parquet",
            "checksum": None,
            "splits": [],
            "mountPath": "/datasets/training",
            "storage": {"pvc": {"claimName": "research-data", "subPath": "support/v3"}},
        }
    ]

    pod = build_training_job(body, datasets)["spec"]["template"]["spec"]
    manifest = next(
        item["value"]
        for item in pod["containers"][0]["env"]
        if item["name"] == "MODEL_FLEET_DATASETS_JSON"
    )

    assert json.loads(manifest) == datasets
    assert pod["volumes"][0]["persistentVolumeClaim"] == {
        "claimName": "research-data",
        "readOnly": True,
    }
    assert pod["containers"][0]["volumeMounts"][0]["subPath"] == "support/v3"
    assert pod["containers"][0]["volumeMounts"][0]["readOnly"] is True


def test_training_supports_dataset_prefetch_init_container():
    fetcher = {"name": "dataset-fetcher", "image": "example/dataset-fetcher:1"}
    body = {
        "kind": "TrainingRun",
        "metadata": {"name": "run-1", "namespace": "models", "uid": "u2"},
        "spec": {"image": "example/trainer:1", "initContainers": [fetcher]},
    }

    pod = build_training_job(body)["spec"]["template"]["spec"]

    assert pod["initContainers"][0]["image"] == fetcher["image"]


def test_training_rejects_init_container_capability_additions():
    body = {
        "kind": "TrainingRun",
        "metadata": {"name": "run-1", "namespace": "models", "uid": "u2"},
        "spec": {
            "image": "example/trainer:1",
            "initContainers": [
                {
                    "name": "dataset-fetcher",
                    "image": "example/dataset-fetcher:1",
                    "securityContext": {"capabilities": {"add": ["NET_ADMIN"]}},
                }
            ],
        },
    }

    with pytest.raises(InvalidSpec, match="cannot add Linux capabilities"):
        build_training_job(body)


def test_training_accelerator_selects_capacity_class():
    body = {
        "kind": "TrainingRun",
        "metadata": {"name": "run-1", "namespace": "models", "uid": "u2"},
        "spec": {
            "image": "example/trainer:1",
            "accelerator": {"count": 4, "class": "gpu-80gb"},
        },
    }
    pod = build_training_job(body)["spec"]["template"]["spec"]
    assert pod["nodeSelector"]["model-fleet.sqe.io/gpu-class"] == "gpu-80gb"
    assert pod["containers"][0]["resources"]["requests"]["nvidia.com/gpu"] == "4"


def test_training_requires_image():
    body = {
        "kind": "TrainingRun",
        "metadata": {"name": "run-1", "namespace": "models", "uid": "u2"},
        "spec": {},
    }
    with pytest.raises(InvalidSpec, match="image"):
        build_training_job(body)


def test_spec_hash_is_stable():
    assert spec_hash({"b": 1, "a": 2}) == spec_hash({"a": 2, "b": 1})
