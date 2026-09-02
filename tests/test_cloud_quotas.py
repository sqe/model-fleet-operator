from unittest.mock import Mock

import pytest

from modelfleet.cloud_quotas import CloudQuotaManager, QuotaCommand, QuotaError


def test_aws_request_checks_current_and_submits():
    api = Mock()
    api.get_service_quota.return_value = {"Quota": {"Value": 8.0}}
    api.list_requested_service_quota_change_history_by_quota.return_value = {"RequestedQuotas": []}
    api.request_service_quota_increase.return_value = {
        "RequestedQuota": {"Id": "request-1", "Status": "PENDING"}
    }
    manager = CloudQuotaManager(aws_client_factory=lambda _region: api)

    result = manager.request(QuotaCommand("aws", "ec2", "L-DB2E81BA", 16, "us-west-2"))

    assert "request-1" in result
    api.request_service_quota_increase.assert_called_once_with(
        ServiceCode="ec2", QuotaCode="L-DB2E81BA", DesiredValue=16
    )


def test_aws_request_does_not_duplicate_pending_increase():
    api = Mock()
    api.get_service_quota.return_value = {"Quota": {"Value": 8.0}}
    api.list_requested_service_quota_change_history_by_quota.return_value = {
        "RequestedQuotas": [{"Id": "pending-1", "Status": "PENDING", "DesiredValue": 32.0}]
    }
    manager = CloudQuotaManager(aws_client_factory=lambda _region: api)

    result = manager.request(QuotaCommand("aws", "ec2", "L-DB2E81BA", 16, "us-west-2"))

    assert "already pending" in result
    api.request_service_quota_increase.assert_not_called()


def test_aws_request_rejects_value_at_or_below_current():
    api = Mock()
    api.get_service_quota.return_value = {"Quota": {"Value": 8.0}}
    manager = CloudQuotaManager(aws_client_factory=lambda _region: api)

    with pytest.raises(QuotaError, match="already 8"):
        manager.request(QuotaCommand("aws", "ec2", "quota", 8, "us-west-2"))


def test_gcp_request_uses_deterministic_preference():
    client = Mock()
    client.get.return_value = Mock(status_code=404)
    client.patch.return_value = Mock(status_code=200, json=Mock(return_value={"reconciling": True}))
    manager = CloudQuotaManager(gcp_client=client)
    command = QuotaCommand(
        "gcp",
        "compute.googleapis.com",
        "GPUS-PER-GPU-FAMILY-per-project-region",
        8,
        "123456789",
        (("gpu_family", "NVIDIA_H100"), ("region", "us-central1")),
    )

    first = manager.request(command)
    manager.request(command)

    assert "RECONCILING" in first
    assert client.patch.call_args_list[0].args[0] == client.patch.call_args_list[1].args[0]
    payload = client.patch.call_args.kwargs["json"]
    assert payload["dimensions"] == {
        "gpu_family": "NVIDIA_H100",
        "region": "us-central1",
    }
