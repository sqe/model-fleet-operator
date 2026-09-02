from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from kubernetes.client import ApiException

from modelfleet.operator import (
    _inference_url,
    _iso,
    _upsert,
    reconcile_agent_registration,
    resolve_training_datasets,
)
from modelfleet.resources import InvalidSpec


def test_upsert_creates_missing_resource():
    read = Mock(side_effect=ApiException(status=404))
    create = Mock()
    patch = Mock()
    body = {"metadata": {"name": "x", "namespace": "models"}}
    _upsert(read, create, patch, body)
    create.assert_called_once_with(namespace="models", body=body)
    patch.assert_not_called()


def test_upsert_patches_existing_resource():
    read, create, patch = Mock(), Mock(), Mock()
    body = {"metadata": {"name": "x", "namespace": "models"}}
    _upsert(read, create, patch, body)
    patch.assert_called_once_with(name="x", namespace="models", body=body)


def test_inference_url_prefers_route_and_reports_service_fallback():
    body = {
        "metadata": {"name": "x", "namespace": "models"},
        "spec": {"gateway": {"enabled": True, "hostnames": ["x.test"], "pathPrefix": "/v1"}},
    }
    assert _inference_url(body) == "http://x.test/v1"
    body["spec"]["gateway"]["sectionName"] = "https"
    assert _inference_url(body) == "https://x.test/v1"
    body["spec"] = {"service": {"port": 9000}}
    assert _inference_url(body) == "http://x.models.svc:9000"


def test_iso():
    value = datetime(2026, 8, 28, tzinfo=UTC)
    assert _iso(value) == value.isoformat()
    assert _iso(None) is None


def test_resolve_training_dataset_checks_version_and_access():
    api = Mock()
    api.get_namespaced_custom_object.return_value = {
        "spec": {
            "uri": "hf://datasets/sqe/support@abc123",
            "version": "abc123",
            "format": "parquet",
            "classification": "internal",
            "allowedServiceAccounts": ["researcher"],
        }
    }
    body = {
        "metadata": {"namespace": "models"},
        "spec": {
            "serviceAccountName": "researcher",
            "datasets": [
                {
                    "name": "training",
                    "datasetRef": "support-abc123",
                    "expectedVersion": "abc123",
                }
            ],
        },
    }

    resolved = resolve_training_datasets(body, api)

    assert resolved[0]["uri"] == "hf://datasets/sqe/support@abc123"
    assert resolved[0]["resourceName"] == "support-abc123"

    body["spec"]["datasets"][0]["expectedVersion"] = "different"
    with pytest.raises(InvalidSpec, match="expected different"):
        resolve_training_datasets(body, api)

    body["spec"]["datasets"][0]["expectedVersion"] = "abc123"
    body["spec"]["serviceAccountName"] = "unauthorized"
    with pytest.raises(InvalidSpec, match="not allowed"):
        resolve_training_datasets(body, api)


def test_managed_agent_registration_scales_annotated_deployment():
    apps = Mock()
    apps.read_namespaced_deployment.return_value = SimpleNamespace(
        metadata=SimpleNamespace(annotations={"fleet.sqe.io/agent-registration": "research-agent"}),
        spec=SimpleNamespace(replicas=2),
        status=SimpleNamespace(ready_replicas=2),
    )
    patch = SimpleNamespace(status={})
    body = {
        "metadata": {"name": "research-agent", "namespace": "models", "generation": 4},
        "spec": {
            "suspend": True,
            "runtime": {"deploymentName": "research-agent-runtime", "activeReplicas": 2},
        },
    }

    reconcile_agent_registration(body, patch, apps)

    apps.patch_namespaced_deployment_scale.assert_called_once_with(
        "research-agent-runtime", "models", {"spec": {"replicas": 0}}
    )
    assert patch.status["phase"] == "Suspended"
