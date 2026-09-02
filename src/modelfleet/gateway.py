"""OpenAI-compatible gateway with an environment-defined upstream allow-list."""

import json
import os
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

from .control_auth import require_control_token

TOKENS = Counter(
    "model_fleet_tokens_total",
    "Tokens processed by the model gateway",
    ["namespace", "workload", "model", "direction"],
)
COST = Counter(
    "model_fleet_inference_cost_usd_total",
    "Estimated inference cost in USD",
    ["namespace", "workload", "model"],
)


@dataclass(frozen=True)
class Route:
    base_url: str
    upstream_model: str
    api_key_env: str | None = None
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0
    namespace: str = "default"
    workload: str = "gateway"


def load_routes(raw: str | None = None) -> dict[str, Route]:
    value = json.loads(raw if raw is not None else os.getenv("MODEL_FLEET_LLM_ROUTES", "{}"))
    if not isinstance(value, dict):
        raise ValueError("MODEL_FLEET_LLM_ROUTES must be a JSON object")
    return {alias: Route(**route) for alias, route in value.items()}


def record_usage(alias: str, route: Route, usage: dict[str, Any]) -> None:
    inputs = int(usage.get("prompt_tokens", 0))
    outputs = int(usage.get("completion_tokens", 0))
    labels = (route.namespace, route.workload, alias)
    TOKENS.labels(*labels, "input").inc(inputs)
    TOKENS.labels(*labels, "output").inc(outputs)
    cost = (
        inputs * route.input_cost_per_million + outputs * route.output_cost_per_million
    ) / 1_000_000
    COST.labels(*labels).inc(cost)


def create_app(
    routes: dict[str, Route] | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> FastAPI:
    app = FastAPI(title="Model Fleet LLM gateway")
    configured = routes if routes is not None else load_routes()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/v1/models", dependencies=[Depends(require_control_token)])
    def models() -> dict[str, Any]:
        data = [{"id": name, "object": "model"} for name in sorted(configured)]
        return {"object": "list", "data": data}

    @app.post("/v1/chat/completions", dependencies=[Depends(require_control_token)])
    async def completions(payload: dict[str, Any]) -> dict[str, Any]:
        alias = payload.get("model")
        if alias not in configured:
            raise HTTPException(404, "unknown model alias")
        route = configured[alias]
        headers: dict[str, str] = {}
        if route.api_key_env:
            key = os.getenv(route.api_key_env)
            if not key:
                raise HTTPException(503, "upstream credential is not configured")
            headers["Authorization"] = f"Bearer {key}"
        forwarded = {**payload, "model": route.upstream_model}
        client = http_client or httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0))
        try:
            response = await client.post(
                f"{route.base_url.rstrip('/')}/v1/chat/completions", json=forwarded, headers=headers
            )
            response.raise_for_status()
            result = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(502, "upstream inference failed") from exc
        finally:
            if http_client is None:
                await client.aclose()
        record_usage(alias, route, result.get("usage", {}))
        return result

    return app


app = create_app()
