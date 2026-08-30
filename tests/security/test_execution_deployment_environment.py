from __future__ import annotations

import pytest

from apps.execution_worker import main as execution_main


def test_execution_deployment_rejects_missing_release_bindings_before_broker_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        execution_main,
        "require_yaml_file_secret",
        lambda *args, **kwargs: "paper-fixture-account",
    )
    for name in (
        "RISK_POLICY_HASH",
        "TEMPLATE_CATALOG_HASH",
        "STRATEGY_REGISTRY_HASH",
        "STRATEGY_CONFIG_HASH",
        "STRATEGY_CONTENT_HASH",
        "ACCOUNT_ALLOWLIST_HASH",
        "RELEASE_HASH",
        "ENTRY_CUTOFF_AT",
        "FLATTEN_AT",
        "FLAT_DEADLINE_AT",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="EXECUTION_DEPLOYMENT_ENV_MISSING"):
        execution_main.deployment_from_environment()
