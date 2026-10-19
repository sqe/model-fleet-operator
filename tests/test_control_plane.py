import httpx
import pytest
from fastapi.testclient import TestClient

from modelfleet.agent_runtime import AgentWorker, create_agent_app
from modelfleet.gateway import Route, create_app, load_routes
from modelfleet.protocol import AgentCard, JsonRpcTask
from modelfleet.registry import KubernetesRegistry
from modelfleet.supervisor import exact_skill, validate_selection

CARDS = [
    {
        "id": "b-agent",
        "skills": [{"id": "summarize", "name": "Summarize", "description": "short text"}],
    },
    {
        "id": "a-agent",
        "skills": [{"id": "summarize", "name": "Summary", "description": "documents"}],
    },
]


def test_exact_skill_is_deterministic_and_selection_is_strict():
    assert exact_skill(CARDS, "summarize").agent == "a-agent"
    assert validate_selection('{"agent":"b-agent","skill":"summarize"}', CARDS).skill == "summarize"
    with pytest.raises(ValueError):
        validate_selection('{"agent":"b-agent","skill":"missing"}', CARDS)
    with pytest.raises(ValueError):
        validate_selection('{"agent":"b-agent","skill":"summarize","other":1}', CARDS)


def test_gateway_routes_are_static_and_typed():
    routes = load_routes(
        '{"safe":{"base_url":"https://llm","upstream_model":"real","runtime":"mlx"}}'
    )
    assert routes == {"safe": Route(base_url="https://llm", upstream_model="real", runtime="mlx")}
    with pytest.raises(ValueError):
        load_routes("[]")


def test_gateway_exports_runtime_request_and_latency_metrics(monkeypatch):
    monkeypatch.setenv("CONTROL_PLANE_API_KEY", "secret")

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1},
            },
        )

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    route = Route(
        base_url="http://mlx-host",
        upstream_model="local-model",
        runtime="mlx",
        namespace="models",
        workload="mlx-host-test",
    )
    client = TestClient(create_app({"mlx-test": route}, upstream))

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer secret"},
        json={"model": "mlx-test", "messages": [{"role": "user", "content": "hello"}]},
    )
    metrics = client.get("/metrics").text

    assert response.status_code == 200
    request_labels = (
        'model="mlx-test",namespace="models",result="success",runtime="mlx",'
        'workload="mlx-host-test"'
    )
    latency_labels = 'model="mlx-test",namespace="models",runtime="mlx",workload="mlx-host-test"'
    assert f"model_fleet_upstream_requests_total{{{request_labels}}} 1.0" in metrics
    assert f"model_fleet_upstream_request_duration_seconds_count{{{latency_labels}}} 1.0" in metrics


def test_registry_lexical_search_is_ranked_and_deterministic():
    class Api:
        def list_namespaced_custom_object(self, *args):
            items = []
            for card in reversed(CARDS):
                items.append(
                    {
                        "metadata": {"name": card["id"]},
                        "spec": {
                            "name": card["id"],
                            "description": "agent",
                            "version": "1",
                            "endpoint": "http://agent",
                            "kafka_topic": "tasks",
                            "kafka_result_topic": "results",
                            "max_concurrent_tasks": 1,
                            "timeout_seconds": 30,
                            "skills": [{**card["skills"][0], "input_schema": {}}],
                        },
                    }
                )
            return {"items": items}

    results = KubernetesRegistry(Api(), "test").search("summarize documents")
    assert [item["id"] for item in results] == ["a-agent", "b-agent"]


def test_python_agent_worker_uses_shared_json_rpc_contract():
    card = AgentCard(
        name="echo",
        description="echo agent",
        version="1.0.0",
        endpoint="http://echo",
        kafka_topic="tasks.echo",
        kafka_result_topic="results.echo",
        max_concurrent_tasks=1,
        timeout_seconds=30,
        skills=[{"id": "echo", "name": "Echo", "description": "echo", "input_schema": {}}],
    )
    worker = AgentWorker(card, {"echo": lambda params: {"text": params["prompt"]}})
    task = JsonRpcTask(id="task-1", params={"skill": "echo", "prompt": "hello"})
    result = worker.process(task.model_dump_json().encode())
    assert result.result == {"text": "hello"}


def test_python_agent_http_transport_requires_configured_bearer_token(monkeypatch):
    monkeypatch.setenv("CONTROL_PLANE_API_KEY", "secret")
    card = AgentCard(
        name="echo-http",
        description="echo agent",
        version="1.0.0",
        endpoint="http://echo",
        kafka_topic="tasks.echo",
        kafka_result_topic="results.echo",
        max_concurrent_tasks=1,
        timeout_seconds=30,
        skills=[{"id": "echo", "name": "Echo", "description": "echo", "input_schema": {}}],
        transports=[{"protocol": "http", "endpoint": "/v1/tasks:execute"}],
    )
    app = create_agent_app(card, AgentWorker(card, {"echo": lambda params: params["prompt"]}))
    task = {
        "jsonrpc": "2.0",
        "method": "tasks.execute",
        "id": "one",
        "params": {"skill": "echo", "prompt": "hello"},
    }

    assert TestClient(app).post("/v1/tasks:execute", json=task).status_code == 401
    response = TestClient(app).post(
        "/v1/tasks:execute", json=task, headers={"Authorization": "Bearer secret"}
    )
    assert response.json()["result"] == "hello"


def test_fleet_operations_agent_import_does_not_load_kubernetes_configuration():
    from modelfleet.fleet_agent import CARD

    assert {skill.id for skill in CARD.skills} == {
        "fleet.agent.control",
        "fleet.status",
        "fleet.inference.control",
        "fleet.training.control",
    }
