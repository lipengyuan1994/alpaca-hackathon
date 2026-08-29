from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
import yaml

from apps.common import runtime

ROOT = Path(__file__).parents[2]


def _dockerfile(name: str) -> str:
    return (ROOT / "infra" / name).read_text(encoding="utf-8")


def _compose() -> dict[str, object]:
    return yaml.safe_load((ROOT / "infra" / "compose.yaml").read_text(encoding="utf-8"))


def _locked_versions() -> dict[str, str]:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    return {package["name"]: package["version"] for package in lock["package"]}


def test_execution_image_contains_locked_credential_zone_dependencies() -> None:
    source = _dockerfile("Dockerfile.execution")
    locked = _locked_versions()
    assert "COPY packages/position_manager packages/position_manager" in source
    assert f'"alpaca-py=={locked["alpaca-py"]}"' in source
    assert locked["psycopg"] == locked["psycopg-binary"]
    assert f'"psycopg[binary]=={locked["psycopg"]}"' in source
    assert f'"pydantic=={locked["pydantic"]}"' in source
    assert "strategy_plugins" not in source
    assert "packages/strategy_runner" not in source
    assert "packages/agent" not in source


def test_decision_image_contains_configs_and_exact_schema_dependencies() -> None:
    source = _dockerfile("Dockerfile.decision")
    locked = _locked_versions()
    assert "COPY configs configs" in source
    assert "COPY apps/common apps/common" in source
    assert "COPY packages/plugin_integrity packages/plugin_integrity" in source
    assert f'"pydantic=={locked["pydantic"]}"' in source
    assert f'"PyYAML=={locked["pyyaml"]}"' in source
    assert "packages/alpaca_execution_mcp" not in source
    assert "packages/execution_core" not in source
    assert "alpaca-py" not in source
    assert "psycopg[binary]" not in source


def test_decision_service_is_networkless_read_only_and_resource_limited() -> None:
    decision = _compose()["services"]["decision"]  # type: ignore[index]
    assert decision["network_mode"] == "none"
    assert decision["read_only"] is True
    assert decision["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in decision["security_opt"]
    assert decision["pids_limit"] > 0
    limits = decision["deploy"]["resources"]["limits"]
    assert limits["cpus"]
    assert limits["memory"]
    assert any(str(item).startswith("/tmp:rw,noexec,nosuid,nodev") for item in decision["tmpfs"])


def test_broker_credentials_exist_only_in_execution_service() -> None:
    services = _compose()["services"]  # type: ignore[index]
    credential_keys = {
        "PAPER_ALPACA_API_KEY",
        "PAPER_ALPACA_API_SECRET",
        "PAPER_ACCOUNT_ID",
        "DATABASE_URL",
    }
    assert credential_keys <= set(services["execution"]["environment"])
    assert services["execution"]["networks"] == ["broker-egress"]
    for name, service in services.items():
        if name != "execution":
            assert credential_keys.isdisjoint(service.get("environment", {})), name
            assert "env_file" not in service, name


def test_local_runtime_rejects_non_arm64_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime.sys, "platform", "darwin")
    monkeypatch.setattr(runtime.platform, "machine", lambda: "x86_64")
    with pytest.raises(RuntimeError, match="LOCAL_RUNTIME_MUST_BE_ARM64"):
        runtime.assert_native_developer_runtime()


def test_local_runtime_accepts_native_darwin_and_linux_containers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime.sys, "platform", "darwin")
    monkeypatch.setattr(runtime.platform, "machine", lambda: "arm64")
    runtime.assert_native_developer_runtime()

    monkeypatch.setattr(runtime.sys, "platform", "linux")
    monkeypatch.setattr(runtime.platform, "machine", lambda: "x86_64")
    runtime.assert_native_developer_runtime()


def test_build_context_excludes_local_secrets_and_environments() -> None:
    ignored = set((ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines())
    assert {".env", ".env.*", "secrets", "*.key", "*.pem"} <= ignored
    assert {".venv", ".uv-cache", ".uv-python-arm64"} <= ignored
