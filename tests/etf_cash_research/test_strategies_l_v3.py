from __future__ import annotations

import pandas as pd
import pytest

from packages.etf_cash_research.engine_v3 import (
    DecisionContext,
    ExecutionFeedback,
    OrderIntent,
    PositionView,
    SizingMode,
)
from packages.etf_cash_research.protocol_v3 import candidate_registry
from packages.etf_cash_research.strategies_l_v3 import LeveragedStrategyV3, make_l_strategy


def _features(*, risk: bool = False) -> dict[str, dict[str, float]]:
    output: dict[str, dict[str, float]] = {}
    for symbol in ("TQQQ", "SOXL", "SPXL", "QQQ", "SPY", "SOXX"):
        output[symbol] = {
            "close": 110.0,
            "sma50": 104.0,
            "sma100": 100.0,
            "sma200": 100.0,
            "ema20": 105.0 if not risk else 95.0,
            "ema100": 100.0,
            "r1": -0.05 if risk else 0.01,
            "r5": -0.07 if risk else 0.03,
            "r21": 0.05,
            "r63": 0.10,
            "r126": 0.20,
            "rsi2": 5.0,
            "hh20": 100.0,
            "hh55": 100.0,
            "ll20": 80.0,
            "atr14": 4.0,
            "vol60": 0.40,
            "vol63": 0.40,
            "er63": 0.60,
            "trend_persistence20": 0.80,
            "bandwidth": 0.10,
            "bandwidth_p20": 0.20,
            "contraction": 1.0,
            "recovery5": 0.0 if risk else 1.0,
        }
    return output


def _context(
    features: dict[str, dict[str, float]] | None = None,
    *,
    positions: dict[tuple[str, str], PositionView] | None = None,
    weekly: bool = True,
    monthly: bool = True,
    feedback: tuple[ExecutionFeedback, ...] = (),
    execution: str = "2024-06-03",
) -> DecisionContext:
    date = pd.Timestamp(execution, tz="UTC")
    return DecisionContext(
        execution_session=date,
        information_cutoff=date - pd.Timedelta(days=1),
        prior_close={"TQQQ": 110.0, "SOXL": 110.0, "SPXL": 110.0, "QQQ": 110.0, "SPY": 110.0, "SOXX": 110.0},
        prior_close_equity=1000.0,
        settled_cash=1000.0,
        unsettled_cash=0.0,
        dividend_receivables=0.0,
        positions=positions or {},
        physical_positions={},
        features=features or _features(),
        history={},
        review_weekly=weekly,
        review_monthly=monthly,
        initial_session=False,
        recent_feedback=feedback,
    )


def _position(component: str, symbol: str, age: int = 1) -> PositionView:
    return PositionView(component, symbol, 4.0, 4_000_000, pd.Timestamp("2024-05-30", tz="UTC"), age)


@pytest.mark.parametrize("strategy_id", [f"L{i:02d}" for i in range(1, 17)])
def test_every_leveraged_strategy_has_a_causal_decision_boundary(strategy_id: str) -> None:
    strategy = LeveragedStrategyV3(strategy_id, "TQQQ", "SOXL", ("QQQ", "SOXX"))
    result = list(strategy.decide(_context()))
    assert all(isinstance(item, OrderIntent) for item in result)
    assert all(item.symbol in {"TQQQ", "SOXL"} for item in result)
    # Strategies can choose cash when a required lookback is not available;
    # they must never request a current-session or unknown symbol.


def test_factory_builds_both_leveraged_pairs_without_changing_rules() -> None:
    candidates = [item for item in candidate_registry() if item.strategy_id == "L11"]
    assert {item.universe.pair_id for item in candidates} == {"TQQQ_SOXL", "SPXL_SOXL"}
    for candidate in candidates:
        strategy = make_l_strategy(candidate, store=None, data=None, config=None, start=None, end=None)
        assert strategy.B == candidate.universe.tradable_symbols[0]
        assert strategy.U == candidate.universe.broad_proxy
        assert strategy.V == "SOXX"


def test_l06_timed_exit_uses_actual_fill_age() -> None:
    strategy = LeveragedStrategyV3("L06", "TQQQ", "SOXL", ("QQQ", "SOXX"))
    context = _context(positions={("account", "TQQQ"): _position("account", "TQQQ", age=5)})
    intents = list(strategy.decide(context))
    assert ("TQQQ", SizingMode.EXIT_FULLY) in {(item.symbol, item.mode) for item in intents}


def test_l09_shock_exit_cooldown_starts_from_actual_liquidation() -> None:
    strategy = LeveragedStrategyV3("L09", "TQQQ", "SOXL", ("QQQ", "SOXX"))
    held = {("account", "TQQQ"): _position("account", "TQQQ", age=3)}
    first = list(strategy.decide(_context(_features(risk=True), positions=held)))
    assert any(item.mode == SizingMode.EXIT_FULLY for item in first)
    feedback = ExecutionFeedback(
        order_id="x",
        component_id="account",
        symbol="TQQQ",
        side="sell",
        decision_session=pd.Timestamp("2024-06-03", tz="UTC"),
        execution_session=pd.Timestamp("2024-06-03", tz="UTC"),
        requested_quantity_microshares=4_000_000,
        filled_quantity_microshares=4_000_000,
        fill_price=100.0,
        status="filled",
        fully_liquidated=True,
    )
    second = list(strategy.decide(_context(feedback=(feedback,))))
    assert not any(item.symbol == "TQQQ" and item.mode in {SizingMode.ENTER_SLEEVE, SizingMode.REBALANCE} for item in second)


def test_l10_preserves_virtual_components_and_shared_cash_intents() -> None:
    strategy = LeveragedStrategyV3("L10", "TQQQ", "SOXL", ("QQQ", "SOXX"))
    intents = list(strategy.decide(_context()))
    assert intents
    assert {item.component_id for item in intents}.issubset({"L02", "L06"})
    assert all(item.target_weight is None or item.target_weight <= 0.693 + 1e-9 for item in intents)


def test_l11_sector_only_leadership_keeps_broad_allocation_in_cash() -> None:
    features = _features()
    features["QQQ"]["close"] = 90.0
    features["QQQ"]["sma200"] = 100.0
    strategy = LeveragedStrategyV3("L11", "TQQQ", "SOXL", ("QQQ", "SOXX"))
    # With no historical ratio frame the fallback ratio equals its own
    # one-point average, so this fixture correctly exercises the broad-only
    # gate without inventing a sector leadership signal.
    intents = list(strategy.decide(_context(features)))
    assert not any(item.symbol == "TQQQ" and item.target_weight for item in intents)


def test_l16_fast_risk_reduction_is_a_rebalance_and_not_a_new_buy() -> None:
    strategy = LeveragedStrategyV3("L16", "TQQQ", "SOXL", ("QQQ", "SOXX"))
    context = _context(features=_features(risk=True), positions={("account", "TQQQ"): _position("account", "TQQQ", age=3)})
    intents = list(strategy.decide(context))
    reduced = [item for item in intents if item.symbol == "TQQQ" and item.mode == SizingMode.REBALANCE]
    assert reduced
    assert reduced[0].target_weight == pytest.approx(0.495)


def test_l16_persistent_risk_does_not_halve_the_reduced_target_each_week() -> None:
    strategy = LeveragedStrategyV3("L16", "TQQQ", "SOXL", ("QQQ", "SOXX"))
    full = {("account", "TQQQ"): PositionView("account", "TQQQ", 9.0, 9_000_000, pd.Timestamp("2024-05-30", tz="UTC"), 3)}
    first = list(strategy.decide(_context(positions=full, execution="2024-06-03")))
    assert any(item.target_weight == pytest.approx(0.99) for item in first)

    reduced_position = {("account", "TQQQ"): PositionView("account", "TQQQ", 9.0, 9_000_000, pd.Timestamp("2024-05-30", tz="UTC"), 4)}
    second = list(strategy.decide(_context(_features(risk=True), positions=reduced_position, execution="2024-06-10")))
    assert [item.target_weight for item in second if item.symbol == "TQQQ"] == [pytest.approx(0.495)]

    third = list(strategy.decide(_context(_features(risk=True), positions=reduced_position, execution="2024-06-17")))
    assert [item.target_weight for item in third if item.symbol == "TQQQ"] == [pytest.approx(0.495)]


def test_l16_non_review_risk_response_is_reduce_only() -> None:
    strategy = LeveragedStrategyV3("L16", "TQQQ", "SOXL", ("QQQ", "SOXX"))
    # Establish the unreduced weekly base first.
    full = {("account", "TQQQ"): PositionView("account", "TQQQ", 9.0, 9_000_000, pd.Timestamp("2024-05-30", tz="UTC"), 3)}
    list(strategy.decide(_context(positions=full, execution="2024-06-03")))
    daily = list(strategy.decide(_context(_features(risk=True), positions=full, weekly=False, monthly=False, execution="2024-06-04")))
    assert any(item.symbol == "TQQQ" and item.mode == SizingMode.REDUCE for item in daily)
    assert not any(item.mode in {SizingMode.ENTER_SLEEVE, SizingMode.REBALANCE} for item in daily)


def test_l16_held_below_cap_still_enters_reduced_state() -> None:
    strategy = LeveragedStrategyV3("L16", "TQQQ", "SOXL", ("QQQ", "SOXX"))
    full = {("account", "TQQQ"): PositionView("account", "TQQQ", 9.0, 9_000_000, pd.Timestamp("2024-05-30", tz="UTC"), 3)}
    list(strategy.decide(_context(positions=full, execution="2024-06-03")))

    # Four shares are already below the 49.5% cap.  A fast-risk event must
    # still mark the sleeve reduced, even though it has no sale to schedule.
    below_cap = {("account", "TQQQ"): _position("account", "TQQQ", age=4)}
    risk = list(strategy.decide(_context(_features(risk=True), positions=below_cap, execution="2024-06-10")))
    assert not any(item.mode == SizingMode.REDUCE for item in risk)
    assert strategy.state.reduced == {"TQQQ"}

    recovered_fast = _features(risk=False)
    recovered_fast["QQQ"]["recovery5"] = 0.0  # fast flag clear, five-day recovery incomplete
    held = list(strategy.decide(_context(recovered_fast, positions=below_cap, execution="2024-06-17")))
    assert [item.target_weight for item in held if item.symbol == "TQQQ"] == [pytest.approx(0.44)]


def test_l16_unrecovered_reduction_uses_half_of_new_base_after_leadership_change() -> None:
    strategy = LeveragedStrategyV3("L16", "TQQQ", "SOXL", ("QQQ", "SOXX"))
    # First establish broad-only base 99%, then reduce it to 49.5% during risk.
    full = {("account", "TQQQ"): PositionView("account", "TQQQ", 9.0, 9_000_000, pd.Timestamp("2024-05-30", tz="UTC"), 3)}
    list(strategy.decide(_context(positions=full, execution="2024-06-03")))
    list(strategy.decide(_context(_features(risk=True), positions=full, execution="2024-06-10")))

    # The sector becomes eligible, changing the L11 base from B=.99 to
    # B=.495/S=.495.  Recovery is still incomplete, so B must remain capped
    # at half of the new base: .5*.495=.2475.
    strategy._pair_frame = pd.DataFrame(
        {"ratio": [2.0], "ratio_sma20": [1.0]},
        index=pd.DatetimeIndex([pd.Timestamp("2024-06-10", tz="UTC")]),
    )
    changed = _features(risk=False)
    changed["QQQ"]["recovery5"] = 0.0
    changed["SOXX"]["r63"] = 0.20
    held = list(strategy.decide(_context(changed, positions=full, execution="2024-06-11")))
    broad_targets = [item.target_weight for item in held if item.symbol == "TQQQ"]
    assert broad_targets == [pytest.approx(0.2475)]
