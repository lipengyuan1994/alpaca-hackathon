from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from packages.etf_cash_research.data_v3 import FeatureStore, load_market_data, wilder
from packages.etf_cash_research.protocol_v3 import PROTOCOL, candidate_registry, registry_envelope


def _bars(*, periods: int = 24, split_factors: list[float] | None = None) -> pd.DataFrame:
    dates = pd.date_range("2024-01-02", periods=periods, freq="B", tz="UTC")
    factors = split_factors or [1.0] * periods
    return pd.DataFrame(
        [
            {
                "date": date,
                "symbol": "TEST",
                "open": 100.0 + index,
                "high": 101.0 + index,
                "low": 99.0 + index,
                "close": 100.0 + index,
                "split_factor": factors[index],
            }
            for index, date in enumerate(dates)
        ]
    )


def test_wilder_uses_simple_seed_then_recursive_smoothing() -> None:
    values = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])

    actual = wilder(values, 3)

    assert actual.iloc[:2].isna().all()
    assert actual.iloc[2] == pytest.approx(2.0)
    assert actual.iloc[3] == pytest.approx(8.0 / 3.0)
    assert actual.iloc[4] == pytest.approx(31.0 / 9.0)


def test_feature_store_is_point_in_time_under_future_price_and_split_mutation() -> None:
    baseline = _bars()
    mutated = baseline.copy()
    future_date = mutated["date"].iloc[18]
    mutated.loc[mutated["date"] == future_date, "close"] = 9_999.0
    mutated.loc[mutated["date"] == future_date, "split_factor"] = 2.0

    left = FeatureStore(baseline).frames["TEST"]
    right = FeatureStore(mutated).frames["TEST"]
    cutoff = baseline["date"].iloc[12]
    columns = ["open", "high", "low", "close", "r1", "r5", "sma20", "atr14", "vol60"]

    pd.testing.assert_frame_equal(left.loc[:cutoff, columns], right.loc[:cutoff, columns])
    assert right.loc[future_date, "close"] != left.loc[future_date, "close"]


def test_feature_store_forward_adjustment_preserves_value_through_split_and_reverse_split() -> None:
    bars = _bars(periods=3, split_factors=[1.0, 2.0, 0.5])
    bars.loc[:, "close"] = [100.0, 50.0, 100.0]
    bars.loc[:, "open"] = bars["close"]
    bars.loc[:, "high"] = bars["close"]
    bars.loc[:, "low"] = bars["close"]

    frame = FeatureStore(bars).frames["TEST"]

    assert frame["close"].tolist() == pytest.approx([100.0, 100.0, 100.0])
    assert frame["open"].tolist() == pytest.approx([100.0, 100.0, 100.0])


def test_registry_has_63_definitions_and_no_performance_outcomes() -> None:
    registry = candidate_registry()
    assert len(registry) == 63
    assert len({item.candidate_id for item in registry}) == 63
    assert sum(item.strategy_id.startswith("S") for item in registry) == 20
    assert sum(item.strategy_id.startswith("A") for item in registry) == 11
    assert sum(item.strategy_id.startswith("L") for item in registry) == 32

    outcome_keys = {"return", "net_pnl", "ending_equity", "max_drawdown", "metrics", "status"}
    for item in registry:
        assert outcome_keys.isdisjoint(item.as_dict())
    envelope = registry_envelope()
    assert envelope["schema_version"] == "etf-cash-registry/v3"
    assert envelope["registry_hash"].startswith("sha256:")
    assert PROTOCOL.as_dict()["subagent_model"] == "gpt-5.6-luna"
    assert PROTOCOL.as_dict()["subagent_reasoning_effort"] == "max"


def test_feature_store_does_not_silently_use_future_row_for_cutoff() -> None:
    bars = _bars(periods=30)
    store = FeatureStore(bars)
    cutoff = bars["date"].iloc[10]

    row = store.at("TEST", cutoff)

    assert row["session_index"] == 10
    assert row["close"] == pytest.approx(110.0)
    assert np.isnan(row["sma20"])


@pytest.mark.skipif(
    not Path("/Volumes/T9/TradingResearch/datasets/alpaca/us-etf-daily/36733396b8a35f06/data_manifest.json").is_file(),
    reason="verified T9 v3 manifest is not mounted",
)
def test_verified_t9_manifest_is_sip_complete_and_uses_bound_action_resolution() -> None:
    path = Path("/Volumes/T9/TradingResearch/datasets/alpaca/us-etf-daily/36733396b8a35f06/data_manifest.json")

    market = load_market_data(path)

    assert set(market.bars["symbol"].unique()) == {"QQQM", "SMH", "SOXX", "QQQ", "SPY", "TQQQ", "SOXL", "SPXL"}
    assert market.resolutions["resolutions"]
    duplicate_dividends = market.actions.assign(action_type=market.actions["action_type"].astype(str).str.lower())
    duplicate_dividends = duplicate_dividends[duplicate_dividends["action_type"].str.contains("dividend")]
    assert not duplicate_dividends.duplicated(["symbol", "ex_date"]).any()
    assert market.manifest_path == path.resolve()
