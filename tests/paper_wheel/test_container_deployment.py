from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]


def _locked_versions() -> dict[str, str]:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    return {package["name"]: package["version"] for package in lock["package"]}


def test_paper_wheel_container_is_amd64_non_root_and_paper_only() -> None:
    dockerfile = (ROOT / "infra/paper-wheel/Dockerfile").read_text(encoding="utf-8")
    compose = yaml.safe_load(
        (ROOT / "infra/paper-wheel/compose.yaml").read_text(encoding="utf-8")
    )
    service = compose["services"]["paper-wheel"]

    assert 'test "$TARGETARCH" = "amd64"' in dockerfile
    assert "FROM python:3.12-slim@sha256:" in dockerfile
    assert "COPY apps/common apps/common" in dockerfile
    assert "COPY pyproject.toml pyproject.toml" in dockerfile
    assert service["platform"] == "linux/amd64"
    assert service["user"] == "10001:10001"
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["environment"]["PAPER_API_BASE_URL"] == "https://paper-api.alpaca.markets"
    assert service["environment"]["PAPER_WHEEL_ENABLED"] == "${PAPER_WHEEL_ENABLED:-0}"
    assert "/etc/alpaca-paper/secrets:/run/paper-secrets:ro" in service["volumes"]


def test_paper_wheel_image_dependencies_match_the_repository_lock() -> None:
    dockerfile = (ROOT / "infra/paper-wheel/Dockerfile").read_text(encoding="utf-8")
    locked = _locked_versions()
    distributions = {
        "alpaca-py": "alpaca-py",
        "numpy": "numpy",
        "pandas": "pandas",
        "pydantic": "pydantic",
        "pydantic-core": "pydantic-core",
        "pyyaml": "PyYAML",
        "requests": "requests",
        "websockets": "websockets",
    }
    for lock_name, distribution_name in distributions.items():
        assert f'"{distribution_name}=={locked[lock_name]}"' in dockerfile


def test_deployer_accepts_only_the_repository_image_by_digest() -> None:
    deployer = (ROOT / "infra/paper-wheel/deploy.sh").read_text(encoding="utf-8")
    assert "ghcr.io/lipengyuan1994/alpaca-hackathon-paper-wheel@sha256:*" in deployer
    assert re.search(r'if \[ "\$\{#digest\}" -ne 64 \]', deployer)
    assert "PAPER_WHEEL_IMAGE_STAGED_DISABLED" in deployer
    assert "enabled_file=\"/etc/alpaca-paper/enabled\"" in deployer
    assert "paper-wheel preflight" in deployer
    assert "paper-wheel verify-arm" in deployer
    assert deployer.index("paper-wheel preflight") < deployer.index("up --detach")


def test_image_workflow_pins_every_action_to_a_full_sha() -> None:
    workflow = (ROOT / ".github/workflows/paper-wheel-image.yml").read_text(encoding="utf-8")
    uses = re.findall(r"uses:\s+[^@\s]+@([^\s]+)", workflow)
    assert uses
    assert all(re.fullmatch(r"[0-9a-f]{40}", revision) for revision in uses)
    assert "github.event_name == 'push' && github.ref == 'refs/heads/main'" in workflow
    assert "environment: vultr-paper" in workflow
    assert "needs.build.outputs.image_digest" in workflow


def test_manual_deployment_workflow_pins_every_action_to_a_full_sha() -> None:
    workflow = (ROOT / ".github/workflows/deploy-paper-wheel.yml").read_text(
        encoding="utf-8"
    )
    uses = re.findall(r"uses:\s+[^@\s]+@([^\s]+)", workflow)
    assert uses
    assert all(re.fullmatch(r"[0-9a-f]{40}", revision) for revision in uses)
    assert "infra/paper-wheel/github-deploy.sh" in workflow


def test_deployment_ssh_key_is_limited_to_three_commands() -> None:
    dispatcher = (ROOT / "infra/paper-wheel/ssh-dispatch.sh").read_text(encoding="utf-8")
    assert 'original_command="${SSH_ORIGINAL_COMMAND:-}"' in dispatcher
    assert "ghcr-login)" in dispatcher
    assert "deploy)" in dispatcher
    assert "ghcr-logout)" in dispatcher
    assert "PAPER_WHEEL_SSH_COMMAND_NOT_ALLOWED" in dispatcher
    assert "sh -c" not in dispatcher
