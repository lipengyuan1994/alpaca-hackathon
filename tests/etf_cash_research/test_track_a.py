from __future__ import annotations

import numpy as np
import pandas as pd

from packages.etf_cash_research.strategies_track_a import (
    TrackAState,
    TrackSignal,
    build_track_a,
    strategy_variants,
)


def _bars(periods: int = 340, *, shock_index: int | None = None) -> dict[str, pd.DataFrame]:
    dates = pd.bdate_range("2022-01-03", periods=periods, tz="UTC")
    result: dict[str, pd.DataFrame] = {}
    for symbol, base, slope in (("QQQM", 100.0, 0.22), ("SMH", 120.0, 0.30)):
        close = base + slope * np.arange(periods, dtype=float)
        if shock_index is not None:
            close[shock_index:] -= 90.0
        result[symbol] = pd.DataFrame(
            {
                "date": dates,
                "open": close,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 1000,
            }
        )
    return result


def test_factory_and_variant_registry_expose_all_ten_candidates() -> None:
    assert len({f"A{i:02d}" for i in range(1, 11)}) == 10
    for strategy_id in (f"A{i:02d}" for i in range(1, 11)):
        strategy = build_track_a(strategy_id)
        assert strategy.strategy_id == strategy_id
        assert len(strategy_variants(strategy_id)) == 3


def test_weekly_review_only_changes_scheduled_vote_target() -> None:
    frames = _bars()
    strategy = build_track_a("A02")
    # 2022-12-16 is Friday; the following Monday starts a new review week.
    friday = strategy.evaluate(frames, 249)
    monday = strategy.evaluate(frames, 250)
    tuesday = strategy.evaluate(frames, 251)
    assert friday.asof.date().isoformat() == "2022-12-16"
    assert monday.asof.date().isoformat() == "2022-12-19"
    assert monday.target_weights == tuesday.target_weights
    assert monday.entries


def test_hysteresis_requires_three_up_closes_and_two_down_closes() -> None:
    frames = _bars(shock_index=270)
    strategy = build_track_a("A04")
    # First eligible streak occurs after the 200-session warm-up.
    signals = [strategy.evaluate(frames, index) for index in (250, 251, 252)]
    assert all(signal.target_weights for signal in signals[:2]) is False
    assert signals[-1].entries
    assert all(weight == 0.495 for weight in signals[-1].target_weights.values())

    # The shock persists for two sessions, so the second close below the
    # buffered SMA removes each sleeve.
    first_down = strategy.evaluate(frames, 270)
    second_down = strategy.evaluate(frames, 271)
    assert not first_down.exits
    assert second_down.exits
    assert not second_down.target_weights


def test_risk_scaled_breakout_uses_atr_and_never_exceeds_sleeve_cap() -> None:
    frames = _bars()
    # Make the current bar a clear breakout while retaining a non-zero ATR.
    for frame in frames.values():
        frame.loc[250, ["open", "high", "close"]] = [220.0, 225.0, 220.0]
        frame.loc[250, "low"] = 215.0
    strategy = build_track_a("A05")
    signal = strategy.evaluate(frames, 250)
    assert signal.entries
    assert signal.target_weights
    assert all(0.0 < weight <= 0.495 for weight in signal.target_weights.values())


def test_drawdown_throttle_scales_s10_component_weights() -> None:
    frames = _bars()
    strategy = build_track_a("A09")
    no_drawdown = strategy.evaluate(frames, 260, context={"shadow_drawdown": 0.0})
    # Use a fresh state so the comparison is at the same monthly/weekly review.
    throttled = build_track_a("A09").evaluate(frames, 260, context={"shadow_drawdown": 0.20})
    assert no_drawdown.target_weights
    assert throttled.target_weights
    assert set(no_drawdown.target_weights) == set(throttled.target_weights)
    for symbol in no_drawdown.target_weights:
        assert throttled.target_weights[symbol] == no_drawdown.target_weights[symbol] * 0.5


def test_a10_uses_strong_trend_budget_and_preserves_ninety_nine_percent_cap() -> None:
    frames = _bars()
    strategy = build_track_a("A10")
    signal = strategy.evaluate(frames, 260)
    # The monotonic fixture meets the strong-trend test.  S01 is the only
    # active component, therefore its 0.80 budget produces 0.792 target.
    assert signal.target_weights
    assert max(signal.target_weights.values()) <= 0.99
    assert any(abs(weight - 0.792) < 1e-9 for weight in signal.target_weights.values())


def test_evaluate_does_not_use_rows_after_requested_information_cutoff() -> None:
    left = _bars()
    right = _bars()
    # Change only a future row.  Earlier A04 signals must remain identical.
    right["SMH"].loc[300:, "close"] *= 4.0
    right["SMH"].loc[300:, "high"] *= 4.0
    strategy_left = build_track_a("A04")
    strategy_right = build_track_a("A04")
    for index in range(250, 300):
        assert strategy_left.evaluate(left, index) == strategy_right.evaluate(right, index)


def test_symbols_and_state_are_integration_friendly() -> None:
    state = TrackAState()
    strategy = build_track_a("A01", state=state)
    signal = strategy.evaluate(_bars(), 260)
    assert isinstance(signal, TrackSignal)
    assert strategy.state is state
    assert signal.asof.tzinfo is not None
