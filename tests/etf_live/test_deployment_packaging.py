from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
DEPLOY_ROOT = ROOT / "infra" / "etf-live"


def _read(name: str) -> str:
    return (DEPLOY_ROOT / name).read_text(encoding="utf-8")


def _compose() -> dict[str, object]:
    return yaml.safe_load((DEPLOY_ROOT / "compose.yaml").read_text(encoding="utf-8"))


def test_l11_image_is_linux_amd64_and_contains_only_the_isolated_runtime() -> None:
    source = _read("Dockerfile")
    assert 'test "$TARGETARCH" = "amd64"' in source
    assert "FROM python:3.12-slim@sha256:" in source
    assert "COPY packages/etf_live packages/etf_live" in source
    assert "COPY configs/live/l11_tqqq_soxl.yaml configs/live/l11_tqqq_soxl.yaml" in source
    assert "packages/paper_wheel" not in source
    assert "configs/paper" not in source
    assert "USER 10002:10002" in source
    assert "chown -R 10002:10002 /app /var/lib/alpaca-etf-live" in source


def test_l11_compose_is_disabled_by_default_and_uses_separate_roots() -> None:
    compose = _compose()
    service = compose["services"]["l11"]  # type: ignore[index]
    assert compose["name"] == "alpaca-etf-live"
    assert service["image"] == "${ETF_LIVE_IMAGE:?set ETF_LIVE_IMAGE to an immutable GHCR digest}"
    assert service["platform"] == "linux/amd64"
    assert service["environment"]["ETF_LIVE_ENABLED"] == "${ETF_LIVE_ENABLED:-0}"
    assert service["environment"]["ETF_LIVE_CONFIG"] == "/app/configs/live/l11_tqqq_soxl.yaml"
    assert service["user"] == "10002:10002"
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in service["security_opt"]
    assert "/var/lib/alpaca-etf-live/l11_tqqq_soxl:/var/lib/alpaca-etf-live/l11_tqqq_soxl:rw" in service["volumes"]
    assert "/etc/etf-live:/etc/etf-live:ro" in service["volumes"]
    assert "/etc/etf-live-secrets:/run/etf-live-secrets:ro" in service["volumes"]
    assert "ports" not in service
    assert "alpaca-paper" not in str(compose)


def test_t08_is_the_selected_default_service_and_keeps_l11_compatibility_profile() -> None:
    compose = _compose()
    service = compose["services"]["t08"]  # type: ignore[index]
    assert service["environment"]["ETF_LIVE_CONFIG"] == "/app/configs/live/t08_tecl.yaml"
    assert "/var/lib/alpaca-etf-live/t08_tecl:/var/lib/alpaca-etf-live/t08_tecl:rw" in service["volumes"]
    assert service["healthcheck"]["test"] == ["CMD", "python", "-m", "packages.etf_live.cli", "status", "--config", "/app/configs/live/t08_tecl.yaml"]
    assert service["healthcheck"]["retries"] == 3
    assert compose["services"]["l11"]["profiles"] == ["legacy-l11"]  # type: ignore[index]


def test_deployer_requires_immutable_digest_and_stages_while_disabled() -> None:
    deployer = _read("deploy.sh")
    assert "ghcr.io/lipengyuan1994/alpaca-hackathon-etf-live@sha256:*" in deployer
    assert re.search(r'if \[ "\$\{#digest\}" -ne 64 \]', deployer)
    assert "/etc/etf-live/enabled" in deployer
    assert "ETF_LIVE_IMAGE_STAGED_DISABLED" in deployer
    assert "ETF_LIVE_DEPLOY_HEALTHCHECK_FAILED" in deployer
    assert "ETF_LIVE_POSTSTART_PREFLIGHT_BLOCKED" in deployer
    assert "ETF_LIVE_PREVIOUS_IMAGE_RESTORED" in deployer
    assert "wait_healthy" in deployer
    assert 'run --rm --no-deps "$service" preflight --config "$config"' in deployer
    assert "/opt/alpaca-etf-live" in deployer
    assert "/opt/alpaca-paper" not in deployer


def test_entrypoint_honors_operator_actions_and_fails_closed_by_default() -> None:
    entrypoint = _read("entrypoint.sh")
    assert 'ETF_LIVE_ENABLED:-0' in entrypoint
    assert "packages.etf_live.cli" in entrypoint
    assert "ETF_LIVE_ENABLE_FILE_MISSING" in entrypoint
    assert "run-once" in entrypoint
    assert "ETF_LIVE_CONSECUTIVE_FAILURE_LIMIT" in entrypoint
    assert "ETF_LIVE_MAX_CONSECUTIVE_FAILURES" in entrypoint
    assert 'if [ "$#" -gt 0 ]' in entrypoint


def test_vultr_installer_installs_only_new_roots_and_preserves_enablement() -> None:
    installer = _read("install-vultr.sh")
    assert "RUN_AS_ROOT_REQUIRED" in installer
    assert "/opt/alpaca-etf-live" in installer
    assert "/var/lib/alpaca-etf-live/l11_tqqq_soxl" in installer
    assert "/etc/etf-live-secrets" in installer
    assert "/usr/local/sbin/deploy-alpaca-etf-live" in installer
    assert "printf '0\\n' > /etc/etf-live/enabled" in installer
    assert "if [ ! -e /etc/etf-live/enabled ]" in installer
    assert "/opt/alpaca-paper" not in installer


def test_ssh_dispatcher_allows_only_ghcr_login_deploy_and_logout() -> None:
    dispatcher = _read("ssh-dispatch.sh")
    assert 'SSH_ORIGINAL_COMMAND:-' in dispatcher
    assert "ghcr-login)" in dispatcher
    assert "deploy)" in dispatcher
    assert "ghcr-logout)" in dispatcher
    assert "ETF_LIVE_SSH_COMMAND_NOT_ALLOWED" in dispatcher
    assert "stage)" not in dispatcher
    assert "sh -c" not in dispatcher
    assert "alpaca-hackathon-etf-live@sha256:" in dispatcher


def test_workflows_use_pinned_actions_and_manual_protected_deployment() -> None:
    image_workflow = (ROOT / ".github/workflows/etf-live-image.yml").read_text(encoding="utf-8")
    deploy_workflow = (ROOT / ".github/workflows/deploy-etf-live.yml").read_text(encoding="utf-8")
    uses = re.findall(r"uses:\s+[^@\s]+@([^\s]+)", image_workflow + deploy_workflow)
    assert uses
    assert all(re.fullmatch(r"[0-9a-f]{40}", revision) for revision in uses)
    assert "platforms: linux/amd64" in image_workflow
    assert "alpaca-hackathon-etf-live" in image_workflow
    assert "packages/etf_strategy_core/**" in image_workflow
    assert "tests/etf_live/**" in image_workflow
    assert "Smoke-test the disabled image without credentials" in image_workflow
    assert "docker run --rm" in image_workflow
    assert "workflow_dispatch:" in deploy_workflow
    assert "environment: vultr-etf-live" in deploy_workflow
    assert "GHCR_TOKEN" in deploy_workflow
    assert "infra/etf-live/github-deploy.sh" in deploy_workflow
    assert "paper-wheel" not in image_workflow + deploy_workflow


def test_shell_scripts_parse_without_executing_deployment() -> None:
    for name in ("deploy.sh", "entrypoint.sh", "install-vultr.sh", "ssh-dispatch.sh"):
        completed = subprocess.run(["sh", "-n", str(DEPLOY_ROOT / name)], check=False)
        assert completed.returncode == 0, name

    completed = subprocess.run(["bash", "-n", str(DEPLOY_ROOT / "github-deploy.sh")], check=False)
    assert completed.returncode == 0
