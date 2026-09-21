from __future__ import annotations

from types import MappingProxyType

import pandas as pd
import pytest

from packages.etf_cash_research.data_v3 import FeatureStore
from packages.etf_cash_research.engine_v3 import DecisionContext, PositionView, SizingMode
from packages.etf_cash_research.strategies_sa_v3 import SAEngineStrategy


def _features(**overrides):
    base = {
        "close": 110.0,
        "sma20": 105.0,
        "sma50": 104.0,
        "sma50_lag20": 100.0,
        "sma100": 102.0,
        "sma200": 100.0,
        "ema20": 105.0,
        "ema100": 102.0,
        "r1": 0.01,
        "r5": 0.03,
        "r21": 0.10,
        "r63": 0.25,
        "r126": 0.50,
        "hh20": 109.0,
        "hh55": 108.0,
        "ll20": 90.0,
        "prev_high": 108.0,
        "std20": 2.0,
        "atr14": 2.0,
        "rsi2": 50.0,
        "vol60": 0.30,
        "vol63": 0.30,
        "bandwidth": 0.04,
        "bandwidth_p20": 0.05,
        "contraction": False,
        "er63": 0.50,
        "session_index": 250,
    }
    base.update(overrides)
    return base


def _context(features=None, *, held=(), component="account", weekly=True, monthly=True, feedback=()):
    features = features or {"QQQM": _features(), "SMH": _features()}
    positions = {}
    for symbol, age in held:
        positions[(component, symbol)] = PositionView(component, symbol, 2.0, 2_000_000, pd.Timestamp("2024-01-02", tz="UTC"), age)
    return DecisionContext(
        execution_session=pd.Timestamp("2025-01-03", tz="UTC"),
        information_cutoff=pd.Timestamp("2025-01-02", tz="UTC"),
        prior_close={symbol: float(values["close"]) for symbol, values in features.items()},
        prior_close_equity=1000.0,
        settled_cash=1000.0,
        unsettled_cash=0.0,
        dividend_receivables=0.0,
        positions=MappingProxyType(positions),
        physical_positions=MappingProxyType({}),
        features=MappingProxyType({symbol: MappingProxyType(values) for symbol, values in features.items()}),
        history=MappingProxyType({}),
        review_weekly=weekly,
        review_monthly=monthly,
        initial_session=False,
        recent_feedback=tuple(feedback),
    )


def _intents(strategy, context):
    return strategy.decide(context)


def test_all_sa_candidates_return_versioned_intents():
    for identifier in [*(f"S{i:02d}" for i in range(1, 11)), *(f"A{i:02d}" for i in range(1, 12))]:
        strategy = SAEngineStrategy(identifier)
        intents = _intents(strategy, _context())
        assert all(intent.mode in set(SizingMode) for intent in intents)
        assert all(intent.symbol in {"QQQM", "SMH"} for intent in intents)


def test_s01_monthly_tie_prefers_qqqm_and_rebalances_only_review():
    strategy = SAEngineStrategy("S01")
    review = _intents(strategy, _context())
    assert [(item.symbol, item.target_weight, item.mode) for item in review] == [("QQQM", 0.99, SizingMode.REBALANCE)]
    later = _intents(strategy, _context(weekly=False, monthly=False))
    assert later == []  # the target is already a scheduled order/position boundary


def test_s02_does_not_rebalance_a_fixed_sleeve_each_day():
    strategy = SAEngineStrategy("S02")
    first = _intents(strategy, _context(weekly=True, monthly=True))
    assert {item.symbol for item in first} == {"QQQM", "SMH"}
    held = _intents(strategy, _context(held=(("QQQM", 2),), weekly=False, monthly=False))
    assert held == [] or all(item.mode != SizingMode.REBALANCE for item in held)


def test_s04_holding_age_is_fill_state_and_ten_sessions_exits():
    strategy = SAEngineStrategy("S04")
    context = _context(held=(("QQQM", 10),), weekly=False, monthly=False)
    intents = _intents(strategy, context)
    assert len(intents) == 1
    assert intents[0].mode == SizingMode.EXIT_FULLY


def test_s05_requires_recovery_after_arm():
    features = {"QQQM": _features(close=100.5, sma20=105.0, std20=2.0), "SMH": _features(close=110.0)}
    strategy = SAEngineStrategy("S05")
    assert _intents(strategy, _context(features, weekly=False, monthly=False)) == []
    features["QQQM"] = _features(close=102.0, sma20=100.0, std20=2.0, session_index=251)
    intents = _intents(strategy, _context(features, weekly=False, monthly=False))
    assert any(item.symbol == "QQQM" for item in intents)


def test_a11_caps_semiconductor_and_keeps_account_at_99_percent():
    features = {
        "QQQM": _features(r63=0.30, r126=0.50, vol63=0.20),
        "SMH": _features(r63=0.60, r126=0.80, vol63=0.10),
    }
    features["SMH"].update({"ratio": 1.2, "ratio_sma20": 1.0})
    strategy = SAEngineStrategy("A11")
    intents = _intents(strategy, _context(features))
    targets = {item.symbol: item.target_weight for item in intents if item.target_weight is not None}
    assert targets["SMH"] <= 0.495 + 1e-12
    assert sum(targets.values()) == 0.99


def test_s10_uses_virtual_component_ids_and_nets_weights():
    strategy = SAEngineStrategy("S10")
    intents = _intents(strategy, _context())
    assert intents
    assert {item.component_id for item in intents} <= {"S01", "S04"}
    assert sum(float(item.target_weight or 0) for item in intents) <= 0.99 + 1e-12


def test_s10_component_hold_age_and_a10_budget_change_are_fill_based():
    features = {"QQQM": _features(), "SMH": _features(rsi2=50.0)}
    positions = {
        ("S01", "QQQM"): PositionView("S01", "QQQM", 2.0, 2_000_000, pd.Timestamp("2024-01-02", tz="UTC"), 5),
        ("S04", "SMH"): PositionView("S04", "SMH", 2.0, 2_000_000, pd.Timestamp("2024-01-02", tz="UTC"), 5),
    }
    base = _context(features, weekly=False, monthly=False)
    held_context = DecisionContext(
        execution_session=base.execution_session,
        information_cutoff=base.information_cutoff,
        prior_close=base.prior_close,
        prior_close_equity=base.prior_close_equity,
        settled_cash=base.settled_cash,
        unsettled_cash=base.unsettled_cash,
        dividend_receivables=base.dividend_receivables,
        positions=MappingProxyType(positions),
        physical_positions=base.physical_positions,
        features=base.features,
        history=base.history,
        review_weekly=False,
        review_monthly=False,
        initial_session=False,
    )
    ensemble = SAEngineStrategy("S10")
    ensemble._trend.state.selected = "QQQM"
    ensemble._trend.state.target_weights = {"QQQM": 0.99}
    assert not any(item.mode == SizingMode.EXIT_FULLY for item in ensemble.decide(held_context))
    positions[("S04", "SMH")] = PositionView("S04", "SMH", 2.0, 2_000_000, pd.Timestamp("2024-01-02", tz="UTC"), 10)
    assert any(item.component_id == "S04" and item.mode == SizingMode.EXIT_FULLY for item in ensemble.decide(held_context))

    dynamic = SAEngineStrategy("A10")
    first = dynamic.decide(_context(features, weekly=True, monthly=True))
    assert any(item.component_id == "S01" and item.mode == SizingMode.REBALANCE for item in first)
    # Once the strong-trend budget is established, repeating the same weekly
    # budget with held component lots does not manufacture another rebalance.
    held_context = DecisionContext(
        execution_session=base.execution_session,
        information_cutoff=base.information_cutoff,
        prior_close=base.prior_close,
        prior_close_equity=base.prior_close_equity,
        settled_cash=base.settled_cash,
        unsettled_cash=base.unsettled_cash,
        dividend_receivables=base.dividend_receivables,
        positions=MappingProxyType({("S01", "QQQM"): positions[("S01", "QQQM")]}),
        physical_positions=base.physical_positions,
        features=base.features,
        history=base.history,
        review_weekly=True,
        review_monthly=False,
        initial_session=False,
    )
    assert not any(item.mode == SizingMode.REBALANCE for item in dynamic.decide(held_context))


def test_a09_uses_shadow_component_quantities_at_prior_close():
    strategy = SAEngineStrategy("A09")
    strategy.shadow_result = type(
        "Shadow",
        (),
        {
            "equity": pd.DataFrame({"date": ["2025-01-02"], "equity": [1000.0]}),
            "component_ledger": pd.DataFrame(
                {
                    "date": ["2025-01-02", "2025-01-02", "2025-01-02", "2025-01-02"],
                    "kind": ["component_position"] * 4,
                    "component_id": ["S01", "S01", "S04", "S04"],
                    "symbol": ["QQQM", "SMH", "QQQM", "SMH"],
                    "quantity": [5.0, 0.0, 1.0, 2.0],
                }
            ),
        },
    )()
    intents = _intents(strategy, _context())
    targets = {(item.component_id, item.symbol): item.target_weight for item in intents if item.target_weight is not None}
    assert targets["S01", "QQQM"] == 0.55
    assert targets["S04", "QQQM"] == 0.11
    assert targets["S04", "SMH"] == 0.22
    assert ("S01", "SMH") not in targets


def test_a09_mirrors_shadow_exit_from_same_execution_morning():
    strategy = SAEngineStrategy("A09")
    strategy.shadow_result = type(
        "Shadow",
        (),
        {
            "equity": pd.DataFrame({"date": ["2025-01-02"], "equity": [1000.0]}),
            "component_ledger": pd.DataFrame(
                {
                    "date": ["2025-01-02"],
                    "kind": ["component_position"],
                    "component_id": ["S01"],
                    "symbol": ["QQQM"],
                    "quantity": [5.0],
                }
            ),
            # This is the shadow's current-morning decision, made from the
            # 2025-01-02 cutoff.  A delayed fill must not defer the mirror.
            "signals": pd.DataFrame(
                {
                    "decision_session": ["2025-01-03T00:00:00+00:00"],
                    "information_cutoff": ["2025-01-02T00:00:00+00:00"],
                    "action": [SizingMode.EXIT_FULLY.value],
                    "component_id": ["S01"],
                    "symbol": ["QQQM"],
                }
            ),
        },
    )()
    intents = _intents(strategy, _context(held=(("QQQM", 1),), component="S01"))
    assert any(item.component_id == "S01" and item.symbol == "QQQM" and item.mode == SizingMode.EXIT_FULLY for item in intents)


def test_future_feature_fields_cannot_change_an_earlier_decision():
    dates = pd.bdate_range("2023-01-02", periods=260, tz="UTC")
    rows = []
    for symbol in ("QQQM", "SMH"):
        for offset, timestamp in enumerate(dates):
            close = 100.0 + offset * 0.1
            rows.append({"date": timestamp, "symbol": symbol, "open": close, "high": close + 1, "low": close - 1, "close": close, "split_factor": 1.0, "dividend": 0.0, "dividend_payable_date": pd.NaT})
    original = pd.DataFrame(rows)
    future = pd.concat([original, original.tail(5).assign(date=pd.bdate_range(dates[-1] + pd.Timedelta(days=1), periods=5, tz="UTC"), close=9999.0, open=9999.0, high=10000.0, low=9998.0)], ignore_index=True)
    store_a, store_b = FeatureStore(original), FeatureStore(future)
    cutoff = dates[220]
    for identifier in [*(f"S{i:02d}" for i in range(1, 11)), *(f"A{i:02d}" for i in range(1, 12))]:
        left = {symbol: store_a.at(symbol, cutoff) for symbol in ("QQQM", "SMH")}
        right = {symbol: store_b.at(symbol, cutoff) for symbol in ("QQQM", "SMH")}
        assert left == right
        first = _context(left, weekly=True, monthly=True)
        altered = _context(right, weekly=True, monthly=True)
        left_strategy = SAEngineStrategy(identifier, store=store_a)
        right_strategy = SAEngineStrategy(identifier, store=store_b)
        left_decision = [(x.component_id, x.symbol, x.mode, x.target_weight) for x in _intents(left_strategy, first)]
        right_decision = [(x.component_id, x.symbol, x.mode, x.target_weight) for x in _intents(right_strategy, altered)]
        assert left_decision == right_decision


@pytest.mark.parametrize("identifier", ["S01", "S02", "S07", "A01", "A02", "A03", "A07", "A08", "A11"])
def test_strict_equality_does_not_create_a_liquidation(identifier):
    features = {"QQQM": _features(), "SMH": _features()}
    strategy = SAEngineStrategy(identifier)
    # Seed scheduled/selected state where needed, then hold at the exact
    # trend boundary.  A strict-downside rule must not treat equality as an
    # exit; scheduled candidates may simply emit their unchanged target.
    for values in features.values():
        values["close"] = values["sma200"]
    intents = _intents(strategy, _context(features, held=(("QQQM", 2), ("SMH", 2)), weekly=False, monthly=False))
    assert not any(item.mode == SizingMode.EXIT_FULLY for item in intents)


def test_a04_requires_three_up_closes_and_two_down_closes():
    strategy = SAEngineStrategy("A04")
    for index in (250, 251):
        features = {"QQQM": _features(session_index=index, close=102.0), "SMH": _features(session_index=index, close=102.0)}
        assert not _intents(strategy, _context(features, weekly=False, monthly=False))
    features = {"QQQM": _features(session_index=252, close=102.0), "SMH": _features(session_index=252, close=102.0)}
    assert any(item.mode == SizingMode.ENTER_SLEEVE for item in _intents(strategy, _context(features, weekly=False, monthly=False)))
    features = {"QQQM": _features(session_index=253, close=98.0), "SMH": _features(session_index=253, close=98.0)}
    assert not any(item.mode == SizingMode.EXIT_FULLY for item in _intents(strategy, _context(features, held=(("QQQM", 2), ("SMH", 2)), weekly=False, monthly=False)))
    features = {"QQQM": _features(session_index=254, close=98.0), "SMH": _features(session_index=254, close=98.0)}
    assert any(item.mode == SizingMode.EXIT_FULLY for item in _intents(strategy, _context(features, held=(("QQQM", 2), ("SMH", 2)), weekly=False, monthly=False)))
