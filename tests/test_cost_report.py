import pytest

from modelfleet.cost_report import CloudCostItem, CostSnapshot, PrometheusCostReporter


def test_cost_report_includes_projection_tokens_and_gpu_memory(monkeypatch):
    reporter = PrometheusCostReporter("http://prometheus")
    snapshot = CostSnapshot(
        compute_hourly_cost_usd=10,
        storage_hourly_cost_usd=1,
        gpu_hourly_cost_usd=7,
        model_cost_24h_usd=3,
        input_tokens_24h=2_000_000,
        output_tokens_24h=1_000_000,
        gpu_utilization_percent=72.5,
        gpu_memory_used_bytes=24 * 1024**3,
        gpu_memory_total_bytes=48 * 1024**3,
        cloud_cost_items_24h=(
            CloudCostItem("aws", "Amazon EC2", "compute", 120),
            CloudCostItem("gcp", "Cloud Storage", "storage", 8),
        ),
        bare_metal_power_watts=750,
        electricity_usd_per_kwh=0.12,
    )
    monkeypatch.setattr(reporter, "snapshot", lambda namespace: snapshot)

    report = reporter.report("models")
    assert "Known infrastructure: $11.00/hour · $264.00/day · $7920.00/30 days" in report
    assert "compute $10.00/hour · storage $1.00/hour" in report
    assert "GPU cost component: $7.00/hour" in report
    assert "input 2,000,000 · output 1,000,000" in report
    assert "$1.00 per 1M tokens" in report
    assert "24.0 / 48.0 GiB (50.0%)" in report
    assert "Cloud billed usage, previous 24h: $128.00" in report
    assert "AWS · Amazon EC2 · compute: $120.00" in report
    assert "0.75 kW · 18.00 kWh/day · $0.09/hour · $2.16/day" in report


def test_cost_report_rejects_promql_injection_in_namespace():
    reporter = PrometheusCostReporter("http://prometheus")
    with pytest.raises(ValueError, match="DNS label"):
        reporter.snapshot('models"} or vector(1)')


def test_snapshot_itemizes_cloud_costs_and_estimates_bare_metal_power(monkeypatch):
    reporter = PrometheusCostReporter(
        "http://prometheus",
        electricity_usd_per_kwh=0.15,
        bare_metal_node_power_watts=500,
    )
    scalar_values = iter([None, 2, 10, 1, 7, 3, 2_000_000, 1_000_000, 72, 24, 48])
    monkeypatch.setattr(reporter, "_query", lambda query: next(scalar_values))
    monkeypatch.setattr(
        reporter,
        "_query_results",
        lambda query: [
            {
                "metric": {"provider": "aws", "service": "EC2", "category": "compute"},
                "value": [0, "25.5"],
            },
            {
                "metric": {"provider": "gcp", "service": "GCS", "category": "storage"},
                "value": [0, "4.5"],
            },
        ],
    )

    snapshot = reporter.snapshot()

    assert snapshot.bare_metal_power_watts == 1000
    assert sum(item.cost_24h_usd for item in snapshot.cloud_cost_items_24h) == 30


def test_namespaced_snapshot_does_not_mix_account_billing(monkeypatch):
    reporter = PrometheusCostReporter("http://prometheus")
    monkeypatch.setattr(reporter, "_query", lambda query: None)
    monkeypatch.setattr(
        reporter, "_cloud_cost_items", lambda: pytest.fail("queried global cloud billing")
    )

    assert reporter.snapshot("models").cloud_cost_items_24h == ()
