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
    assert "COPY packages/agent/frozen.py packages/agent/frozen.py" in source
    assert "COPY packages/agent packages/agent" not in source
    assert f'"pydantic=={locked["pydantic"]}"' in source
    assert f'"PyYAML=={locked["pyyaml"]}"' in source
    assert "packages/alpaca_execution_mcp" not in source
    assert "packages/execution_core" not in source
    assert "alpaca-py" not in source
    assert "psycopg[binary]" not in source
    assert "httpx" not in source


def test_decision_service_has_only_the_internal_advisory_route_and_hardening() -> None:
    decision = _compose()["services"]["decision"]  # type: ignore[index]
    networks = _compose()["networks"]  # type: ignore[index]
    assert decision["networks"] == ["agent-internal"]
    assert networks["agent-internal"]["internal"] is True
    assert decision["read_only"] is True
    assert decision["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in decision["security_opt"]
    limits = decision["deploy"]["resources"]["limits"]
    assert limits["cpus"]
    assert limits["memory"]
    assert limits["pids"] > 0
    assert any(str(item).startswith("/tmp:rw,noexec,nosuid,nodev") for item in decision["tmpfs"])


def test_file_secrets_are_scoped_to_one_credentialed_role() -> None:
    services = _compose()["services"]  # type: ignore[index]
    direct_credential_keys = {
        "PAPER_ALPACA_API_KEY",
        "PAPER_ALPACA_API_SECRET",
        "PAPER_ACCOUNT_ID",
        "DATABASE_URL",
    }
    execution_secret_files = {
        "PAPER_ALPACA_API_KEY_FILE",
        "PAPER_ALPACA_API_SECRET_FILE",
        "PAPER_ACCOUNT_ID_FILE",
        "DATABASE_URL_FILE",
    }
    assert execution_secret_files <= set(services["execution"]["environment"])
    assert set(services["execution"]["secrets"]) == {
        "paper_alpaca_api_key",
        "paper_alpaca_api_secret",
        "paper_account_id",
        "execution_database_url",
    }
    assert services["execution"]["networks"] == ["broker-egress", "database-internal"]
    assert services["execution"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    for name, service in services.items():
        assert direct_credential_keys.isdisjoint(service.get("environment", {})), name
        assert "env_file" not in service, name


def test_agent_service_has_one_model_secret_and_no_public_port() -> None:
    services = _compose()["services"]  # type: ignore[index]
    secrets = _compose()["secrets"]  # type: ignore[index]
    agent = services["agent"]
    assert agent["secrets"] == ["agent_model_api_key"]
    assert agent["environment"]["AGENT_MODEL_ID"] == "${AGENT_MODEL_ID:-gemini_3_6_flash}"
    assert agent["environment"]["AGENT_MODEL_API_KEY_FILE"] == "/run/secrets/agent_model_api_key"
    assert agent["networks"] == ["agent-internal", "agent-egress"]
    assert "ports" not in agent
    source = _dockerfile("Dockerfile.agent")
    assert "COPY packages/agent packages/agent" in source
    assert "packages/alpaca_execution_mcp" not in source
    assert "packages/order_planner" not in source
    assert "packages/risk_kernel" not in source
    assert (
        secrets["agent_model_api_key"]["file"]
        == "${REGIMESWITCH_SECRETS_DIR:-/Users/lipengyuan/.config/great_secrets}/llm/model_api_key.yaml"
    )


def test_compose_secret_sources_share_the_local_external_secret_directory() -> None:
    secrets = _compose()["secrets"]  # type: ignore[index]
    secret_directory = "${REGIMESWITCH_SECRETS_DIR:-/Users/lipengyuan/.config/great_secrets}"
    alpaca_bundle = f"{secret_directory}/alpaca/alpaca_api_key.yaml"
    assert secrets["paper_alpaca_api_key"]["file"] == alpaca_bundle
    assert secrets["paper_alpaca_api_secret"]["file"] == alpaca_bundle
    assert secrets["paper_account_id"]["file"] == alpaca_bundle
    assert secrets["execution_database_url"]["file"] == f"{secret_directory}/execution_database_url"
    assert (
        secrets["postgres_bootstrap_password"]["file"]
        == f"{secret_directory}/postgres/bootstrap_password"
    )
    assert (
        secrets["postgres_execution_password"]["file"]
        == f"{secret_directory}/postgres/execution_password"
    )


def test_postgres_service_is_internal_and_initializes_the_runtime_schema() -> None:
    compose = _compose()
    services = compose["services"]
    networks = compose["networks"]
    postgres = services["postgres"]

    assert postgres["platform"] == "${REGIMESWITCH_DOCKER_PLATFORM:-linux/arm64}"
    assert postgres["networks"] == ["database-internal"]
    assert networks["database-internal"]["internal"] is True
    assert "ports" not in postgres
    assert postgres["secrets"] == ["postgres_bootstrap_password", "postgres_execution_password"]
    assert postgres["read_only"] is True
    assert "no-new-privileges:true" in postgres["security_opt"]
    assert postgres["healthcheck"]["test"] == [
        "CMD-SHELL",
        "pg_isready --username=$${POSTGRES_USER} --dbname=$${POSTGRES_DB}",
    ]
    assert postgres["volumes"] == ["postgres_data:/var/lib/postgresql/data"]
    assert compose["volumes"] == {"postgres_data": None}

    source = _dockerfile("Dockerfile.postgres")
    assert "postgres:18.3-bookworm@sha256:" in source
    assert "001_initial.sql" in source
    assert "002_runtime_safety.sql" in source
    grants = (ROOT / "infra" / "postgres" / "999_grant_execution_role.sh").read_text(
        encoding="utf-8"
    )
    assert "GRANT SELECT, INSERT, UPDATE, DELETE" in grants
    assert "GRANT CONNECT ON DATABASE" in grants
    assert "TEMPORARY" not in grants
    assert "FROM pg_database" in grants
    assert "GRANT ALL" not in grants
    role = (ROOT / "infra" / "postgres" / "000_create_execution_role.sh").read_text(
        encoding="utf-8"
    )
    assert "NOSUPERUSER" in role
    assert "NOCREATEDB" in role
    assert "NOCREATEROLE" in role
    assert "NOBYPASSRLS" in role

    assert "database-internal" not in services["agent"]["networks"]
    assert "database-internal" not in services["decision"]["networks"]
    assert "database-internal" not in services["api"]["networks"]


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
    assert {".env", ".env.*", "secrets", ".secrets", "*.key", "*.pem"} <= ignored
    assert {".venv", ".uv-cache", ".uv-python-arm64"} <= ignored
