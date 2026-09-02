import httpx
import pytest

from modelfleet.grafana import GrafanaRenderer


def test_renderer_requests_filtered_png_with_bearer_token(monkeypatch):
    captured = {}

    def get(url, **kwargs):
        captured.update(url=url, **kwargs)
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=b"png",
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", get)
    image = GrafanaRenderer("http://grafana", "token").render("models")

    assert image == b"png"
    assert captured["url"].endswith("/render/d/model-fleet-operations/model-fleet-operations")
    assert captured["headers"] == {"Authorization": "Bearer token"}
    assert captured["params"]["var-namespace"] == "models"


def test_renderer_rejects_non_image_response(monkeypatch):
    def get(url, **kwargs):
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"login",
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", get)
    with pytest.raises(ValueError, match="PNG"):
        GrafanaRenderer("http://grafana").render()
