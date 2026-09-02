import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("profile", ["aws", "gcp", "bare-metal"])
def test_example_permission_profiles_render(profile: str, tmp_path: Path) -> None:
    config = Path(f"config/permissions/{profile}.example.json")

    subprocess.run(
        [sys.executable, "scripts/render_permissions.py", "--config", config, "--output", tmp_path],
        check=True,
    )

    service_account = tmp_path.joinpath("service-account.yaml").read_text()
    assert "kind: ServiceAccount" in service_account
    assert "automountServiceAccountToken: false" in service_account
    assert tmp_path.joinpath("NEXT_STEPS.txt").is_file()


def test_rejects_unknown_profile(tmp_path: Path) -> None:
    config = tmp_path / "unknown.json"
    config.write_text(
        json.dumps({"profile": "other", "namespace": "models", "serviceAccount": "runner"})
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/render_permissions.py",
            "--config",
            config,
            "--output",
            tmp_path / "output",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "profile must be one of" in result.stderr


def test_render_removes_artifacts_from_previous_profile(tmp_path: Path) -> None:
    tmp_path.joinpath("aws-policy.json").write_text("stale")

    subprocess.run(
        [
            sys.executable,
            "scripts/render_permissions.py",
            "--config",
            "config/permissions/bare-metal.example.json",
            "--output",
            tmp_path,
        ],
        check=True,
    )

    assert not tmp_path.joinpath("aws-policy.json").exists()
