from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from apps.decision_worker.main import FIXTURE_TIME, _evaluate, fixture_inputs
from packages.contracts.models import StrategyContextV1
from packages.market_data import (
    DEFAULT_FEATURE_CONTRACT_PATH,
    FeatureContractError,
    compute_feature_vector,
    load_feature_contract,
)
from packages.strategy_runner import PluginAuthorizationError, run_plugin
from tests.security.test_strategy_authorization import _authorization


def _rebuild_context(context: StrategyContextV1, **updates: object) -> StrategyContextV1:
    payload = context.model_dump(mode="json", exclude={"context_hash"})
    payload.update(updates)
    return StrategyContextV1.model_validate(payload)


def test_feature_contract_is_strict_hash_addressed_and_namespaced(tmp_path: Path) -> None:
    contract = load_feature_contract()
    assert contract.feature_keys == ("SPY__momentum_z", "QQQ__momentum_z")
    assert contract.content_hash.startswith("sha256:")

    raw = yaml.safe_load(DEFAULT_FEATURE_CONTRACT_PATH.read_text(encoding="utf-8"))
    raw["unexpected"] = True
    invalid = tmp_path / "invalid-feature-contract.yaml"
    invalid.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    with pytest.raises(FeatureContractError, match="FEATURE_CONTRACT_INVALID"):
        load_feature_contract(invalid)


def test_feature_builder_refuses_missing_or_reordered_contract_keys() -> None:
    market, *_ = fixture_inputs(momentum=True)
    contract = load_feature_contract()
    with pytest.raises(FeatureContractError, match="FEATURE_VECTOR_KEYS_MISMATCH"):
        compute_feature_vector(
            market,
            feature_id="bad-features",
            calculated_at=FIXTURE_TIME,
            values={"QQQ__momentum_z": Decimal("1")},
            contract=contract,
        )


@pytest.mark.parametrize(
    ("update", "reason"),
    [
        (
            {"universe_features": {"SPY__momentum_z": Decimal("1.2")}},
            "PLUGIN_FEATURE_AUTHORITY_MISMATCH",
        ),
        (
            {"feature_contract_hash": "sha256:" + "f" * 64},
            "PLUGIN_FEATURE_CONTRACT_MISMATCH",
        ),
        (
            {"feature_available_time": FIXTURE_TIME - timedelta(seconds=61)},
            "PLUGIN_FEATURE_STALE_OR_FUTURE",
        ),
    ],
)
def test_runner_refuses_unpinned_or_stale_feature_inputs(
    update: dict[str, object], reason: str
) -> None:
    *_, context, config, _ = _evaluate("regime_momentum", momentum=True)
    altered = _rebuild_context(context, **update)
    with pytest.raises(PluginAuthorizationError, match=reason):
        run_plugin(
            authorization=_authorization(),
            context=altered,
            config=config,
        )
