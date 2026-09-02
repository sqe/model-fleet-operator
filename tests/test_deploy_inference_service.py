import argparse
import importlib.util
from pathlib import Path

import pytest

PATH = Path(__file__).parents[1] / "scripts" / "deploy_inference_service.py"
SPEC = importlib.util.spec_from_file_location("deploy_inference_service", PATH)
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(module)


def test_manifest_uses_structured_values() -> None:
    args = module.parser().parse_args(
        [
            "--image",
            "registry/model:v1",
            "--model-name",
            "demo",
            "--model-version",
            "1",
            "--model-uri",
            "s3://models/demo",
            "--name",
            "demo",
            "--image-pull-secret",
            "pull",
        ]
    )
    manifest = module.build_manifest(args)
    assert manifest["apiVersion"] == "fleet.sqe.io/v1alpha1"
    assert manifest["spec"]["model"]["uri"] == "s3://models/demo"
    assert manifest["spec"]["container"]["image"] == "registry/model:v1"
    assert manifest["spec"]["imagePullSecrets"] == [{"name": "pull"}]


def test_manifest_configures_cilium_gateway_route() -> None:
    args = module.parser().parse_args(
        [
            "--image",
            "registry/model:v1",
            "--model-name",
            "demo",
            "--model-version",
            "1",
            "--model-uri",
            "s3://models/demo",
            "--name",
            "demo",
            "--gateway-host",
            "demo.example.com",
            "--gateway-section",
            "https",
        ]
    )

    gateway = module.build_manifest(args)["spec"]["gateway"]

    assert gateway["hostnames"] == ["demo.example.com"]
    assert gateway["sectionName"] == "https"


@pytest.mark.parametrize("value", ["Upper", "-bad", "bad_thing", "x" * 64])
def test_invalid_kubernetes_names(value):
    with pytest.raises(argparse.ArgumentTypeError):
        module.kubernetes_name(value)


def test_dry_run_does_not_invoke_kubectl(monkeypatch, capsys):
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: pytest.fail("kubectl invoked"))
    assert (
        module.main(
            [
                "--image",
                "model:v1",
                "--model-name",
                "m",
                "--model-version",
                "1",
                "--model-uri",
                "file:///model",
                "--name",
                "m",
                "--apply",
                "--dry-run",
            ]
        )
        == 0
    )
    assert '"kind": "InferenceService"' in capsys.readouterr().out
