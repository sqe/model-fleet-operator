"""Prometheus-backed cost and accelerator utilization reports."""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class CloudCostItem:
    provider: str
    service: str
    category: str
    cost_24h_usd: float


@dataclass(frozen=True)
class CostSnapshot:
    compute_hourly_cost_usd: float | None
    storage_hourly_cost_usd: float | None
    gpu_hourly_cost_usd: float | None
    model_cost_24h_usd: float | None
    input_tokens_24h: float | None
    output_tokens_24h: float | None
    gpu_utilization_percent: float | None
    gpu_memory_used_bytes: float | None
    gpu_memory_total_bytes: float | None
    cloud_cost_items_24h: tuple[CloudCostItem, ...] = ()
    bare_metal_power_watts: float | None = None
    electricity_usd_per_kwh: float | None = None


class PrometheusCostReporter:
    def __init__(
        self,
        base_url: str,
        token: str = "",
        timeout_seconds: float = 20,
        *,
        electricity_usd_per_kwh: float | None = None,
        bare_metal_node_power_watts: float | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.electricity_usd_per_kwh = electricity_usd_per_kwh
        self.bare_metal_node_power_watts = bare_metal_node_power_watts

    @classmethod
    def from_env(cls) -> PrometheusCostReporter | None:
        if not (base_url := os.getenv("PROMETHEUS_URL", "").strip()):
            return None
        return cls(
            base_url,
            os.getenv("PROMETHEUS_SERVICE_ACCOUNT_TOKEN", ""),
            float(os.getenv("PROMETHEUS_TIMEOUT_SECONDS", "20")),
            electricity_usd_per_kwh=_optional_float("BARE_METAL_ELECTRICITY_USD_PER_KWH"),
            bare_metal_node_power_watts=_optional_float("BARE_METAL_NODE_POWER_WATTS"),
        )

    def _query_results(self, query: str) -> list[dict[str, Any]]:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        response = httpx.get(
            f"{self.base_url}/api/v1/query",
            params={"query": query},
            headers=headers,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        if payload.get("status") != "success":
            raise ValueError("Prometheus query failed")
        return payload.get("data", {}).get("result", [])

    def _query(self, query: str) -> float | None:
        results = self._query_results(query)
        if not results:
            return None
        value = float(results[0]["value"][1])
        return value if math.isfinite(value) else None

    def _cloud_cost_items(self) -> tuple[CloudCostItem, ...]:
        results = self._query_results(
            "sum by (provider, service, category) (increase(model_fleet_cloud_cost_usd_total[24h]))"
        )
        items = []
        for result in results:
            metric = result.get("metric", {})
            value = float(result["value"][1])
            if not math.isfinite(value):
                continue
            items.append(
                CloudCostItem(
                    provider=metric.get("provider", "unknown"),
                    service=metric.get("service", "unclassified"),
                    category=metric.get("category", "other"),
                    cost_24h_usd=value,
                )
            )
        return tuple(
            sorted(items, key=lambda item: (item.provider, -item.cost_24h_usd, item.service))
        )

    def snapshot(self, namespace: str | None = None) -> CostSnapshot:
        if namespace and not re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", namespace):
            raise ValueError("namespace must be a Kubernetes DNS label")
        matcher = namespace or ".*"
        workload_nodes = f'max by (node) (kube_pod_info{{namespace=~"{matcher}"}}) > 0'
        labels = f'namespace=~"{matcher}"'
        power_watts = self._query(
            f"sum(node_power_usage_watts * on(node) group_left() ({workload_nodes}))"
        )
        if power_watts is None and self.bare_metal_node_power_watts is not None:
            node_count = self._query(f"count({workload_nodes})")
            if node_count is not None:
                power_watts = node_count * self.bare_metal_node_power_watts
        return CostSnapshot(
            compute_hourly_cost_usd=self._query(
                f"sum(node_total_hourly_cost * on(node) group_left() ({workload_nodes}))"
            ),
            storage_hourly_cost_usd=self._query(
                f"sum(avg(pod_pvc_allocation{{{labels}}}) by (persistentvolume, namespace) "
                "* on(persistentvolume) group_left() avg(pv_hourly_cost) by (persistentvolume) "
                "/ 1073741824)"
            ),
            gpu_hourly_cost_usd=self._query(
                "sum((avg(node_gpu_hourly_cost) by (node) * on(node) group_left() "
                f"avg(node_gpu_count) by (node)) * on(node) group_left() ({workload_nodes}))"
            ),
            model_cost_24h_usd=self._query(
                f"sum(increase(model_fleet_inference_cost_usd_total{{{labels}}}[24h]))"
            ),
            input_tokens_24h=self._query(
                f'sum(increase(model_fleet_tokens_total{{{labels},direction="input"}}[24h]))'
            ),
            output_tokens_24h=self._query(
                f'sum(increase(model_fleet_tokens_total{{{labels},direction="output"}}[24h]))'
            ),
            gpu_utilization_percent=self._query(
                f'avg(DCGM_FI_DEV_GPU_UTIL{{namespace=~"{matcher}"}})'
            ),
            gpu_memory_used_bytes=self._query(
                f'sum(DCGM_FI_DEV_FB_USED{{namespace=~"{matcher}"}}) * 1024 * 1024'
            ),
            gpu_memory_total_bytes=self._query(
                f'sum(DCGM_FI_DEV_FB_TOTAL{{namespace=~"{matcher}"}}) * 1024 * 1024'
            ),
            cloud_cost_items_24h=self._cloud_cost_items() if namespace is None else (),
            bare_metal_power_watts=power_watts,
            electricity_usd_per_kwh=self.electricity_usd_per_kwh,
        )

    def report(self, namespace: str | None = None) -> str:
        values = self.snapshot(namespace)
        scope = f"`{namespace}`" if namespace else "all namespaces"
        lines = [f"*Model Fleet cost and GPU report* — {scope}"]
        if values.cloud_cost_items_24h:
            cloud_total = sum(item.cost_24h_usd for item in values.cloud_cost_items_24h)
            lines.append(f"• Cloud billed usage, previous 24h: ${cloud_total:,.2f}")
            for item in values.cloud_cost_items_24h:
                lines.append(
                    f"  ◦ {item.provider.upper()} · {item.service} · {item.category}: "
                    f"${item.cost_24h_usd:,.2f}"
                )
            lines.append("• Billing and OpenCost estimates overlap; they are not added together.")
        elif namespace is None:
            lines.append("• Cloud billed usage: unavailable (billing-export metric missing)")
        known_costs = [
            value
            for value in (values.compute_hourly_cost_usd, values.storage_hourly_cost_usd)
            if value is not None
        ]
        if not known_costs:
            lines.append("• Infrastructure cost: unavailable (OpenCost metric missing)")
        else:
            total = sum(known_costs)
            lines.append(
                f"• Known infrastructure: ${total:.2f}/hour · ${total * 24:.2f}/day · "
                f"${total * 24 * 30:.2f}/30 days projected"
            )
            lines.append(
                f"• Breakdown: compute {_hourly(values.compute_hourly_cost_usd)} · "
                f"storage {_hourly(values.storage_hourly_cost_usd)}"
            )
            if values.gpu_hourly_cost_usd is not None:
                lines.append(
                    f"• GPU cost component: ${values.gpu_hourly_cost_usd:.2f}/hour "
                    "(included in compute)"
                )
        if values.bare_metal_power_watts is not None:
            power_kw = values.bare_metal_power_watts / 1000
            power_line = (
                f"• Bare-metal electricity: {power_kw:.2f} kW · {power_kw * 24:.2f} kWh/day"
            )
            if values.electricity_usd_per_kwh is not None:
                hourly = power_kw * values.electricity_usd_per_kwh
                power_line += f" · ${hourly:.2f}/hour · ${hourly * 24:.2f}/day"
            else:
                power_line += " · cost unavailable (electricity rate missing)"
            lines.append(power_line)
        lines.append(
            "• Model spend, previous 24h: "
            + (
                _usd(values.model_cost_24h_usd)
                if values.model_cost_24h_usd is not None
                else "unavailable"
            )
        )
        lines.append(
            f"• Tokens, previous 24h: input {_count(values.input_tokens_24h)} · "
            f"output {_count(values.output_tokens_24h)}"
        )
        total_tokens = (values.input_tokens_24h or 0) + (values.output_tokens_24h or 0)
        if values.model_cost_24h_usd is not None and total_tokens:
            cost_per_million = values.model_cost_24h_usd * 1_000_000 / total_tokens
            lines.append(f"• Effective model cost: ${cost_per_million:,.2f} per 1M tokens")
        lines.append(
            "• GPU utilization: "
            + (
                f"{values.gpu_utilization_percent:.1f}%"
                if values.gpu_utilization_percent is not None
                else "unavailable"
            )
        )
        if values.gpu_memory_used_bytes is None or values.gpu_memory_total_bytes is None:
            lines.append("• GPU memory: unavailable (DCGM metrics missing)")
        else:
            percentage = (
                100 * values.gpu_memory_used_bytes / values.gpu_memory_total_bytes
                if values.gpu_memory_total_bytes
                else 0
            )
            lines.append(
                f"• GPU memory: {_gib(values.gpu_memory_used_bytes)} / "
                f"{_gib(values.gpu_memory_total_bytes)} GiB ({percentage:.1f}%)"
            )
        return "\n".join(lines)


def _usd(value: float) -> str:
    return f"${value:,.2f}"


def _hourly(value: float | None) -> str:
    return f"${value:,.2f}/hour" if value is not None else "unavailable"


def _count(value: float | None) -> str:
    return f"{value:,.0f}" if value is not None else "unavailable"


def _gib(value: float) -> str:
    return f"{value / 1024**3:,.1f}"


def _optional_float(name: str) -> float | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    parsed = float(value)
    if parsed < 0 or not math.isfinite(parsed):
        raise ValueError(f"{name} must be a finite non-negative number")
    return parsed
