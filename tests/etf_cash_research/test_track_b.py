from __future__ import annotations

import pandas as pd

from packages.etf_cash_research.strategies_track_b import (
    TRACK_B_STRATEGY_IDS,
    TrackBState,
    TrackSignal,
    build_track_b,
    strategy_variants,
)


def _frames(periods: int = 520, *, volatile_broad: bool = False) -> dict[str, pd.DataFrame]:
    """Build deterministic proxy/tradable fixtures with a long uptrend."""

    dates = pd.bdate_range("2022-01-03", periods=periods, tz="UTC")
    rows: dict[str, list[dict[str, object]]] = {symbol: [] for symbol in ("TQQQ", "SPXL", "SOXL", "QQQ", "SPY", "SOXX")}
    for index, date in enumerate(dates):
        broad_proxy = 100.0 * (1.0 + 0.00055 * index)
        sp_proxy = 95.0 * (1.0 + 0.00045 * index)
        sector_proxy = 120.0 * (1.0 + 0.00075 * index)
        # Actual leveraged series are deliberately independent of the signal
        # proxy levels; L02/L04/L08 must use these actual returns for sizing.
        broad_move = 1.0 + (0.05 if volatile_broad and index % 2 == 0 else -0.05 if volatile_broad else 0.0009)
        tqqq = 30.0 * (1.0 + 0.0008 * index) * (broad_move if volatile_broad else 1.0)
        spxl = 40.0 * (1.0 + 0.0007 * index)
        soxl_move = 1.0 + (0.05 if volatile_broad and index % 2 == 0 else -0.05 if volatile_broad else 0.0011)
        soxl = 25.0 * (1.0 + 0.0011 * index) * (soxl_move if volatile_broad else 1.0)
        values = {"TQQQ": tqqq, "SPXL": spxl, "SOXL": soxl, "QQQ": broad_proxy, "SPY": sp_proxy, "SOXX": sector_proxy}
        for symbol, close in values.items():
            rows[symbol].append(
                {
                    "date": date,
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": 1000,
                }
            )
    return {symbol: pd.DataFrame(symbol_rows) for symbol, symbol_rows in rows.items()}


def test_factory_exposes_explicit_b_s_and_u_v_symbols() -> None:
    tqqq = build_track_b("L01", "TQQQ")
    spxl = build_track_b("L01", "SPXL")
    assert tqqq.symbols == ("TQQQ", "SOXL")
    assert tqqq.signal_symbols == ("QQQ", "SOXX")
    assert spxl.symbols == ("SPXL", "SOXL")
    assert spxl.signal_symbols == ("SPY", "SOXX")
    assert tqqq.all_symbols == ("TQQQ", "SOXL", "QQQ", "SOXX")


def test_all_primary_strategies_return_track_signal_after_warmup() -> None:
    frames = _frames()
    for strategy_id in TRACK_B_STRATEGY_IDS:
        signal = build_track_b(strategy_id, "TQQQ").evaluate(frames, 500)
        assert isinstance(signal, TrackSignal)
        assert signal.asof == frames["QQQ"].iloc[500]["date"]
        assert signal.reason_code.startswith(strategy_id)
        assert sum(signal.target_weights.values()) <= 0.99 + 1e-12


def test_missing_proxy_or_tradable_bar_fails_closed() -> None:
    frames = _frames()
    frames.pop("SOXX")
    signal = build_track_b("L03", "TQQQ").evaluate(frames, 500)
    assert signal.reason_code == "MISSING_SYMBOL"
    assert signal.target_weights == {}


def test_monthly_rotation_can_select_semiconductor_proxy_leader() -> None:
    frames = _frames()
    strategy = build_track_b("L01", "TQQQ")
    index = next(
        item
        for item in range(201, len(frames["QQQ"]))
        if frames["QQQ"].iloc[item]["date"].month != frames["QQQ"].iloc[item - 1]["date"].month
    )
    signal = strategy.evaluate(frames, index)
    # SOXX has the stronger proxy momentum in this fixture, so the matching
    # leveraged sleeve is selected at the first monthly review.
    assert signal.target_weights == {"SOXL": 0.99}
    assert signal.entries == ("SOXL",)


def test_l02_volatility_target_uses_actual_leveraged_returns() -> None:
    calm_frames = _frames()
    volatile_frames = _frames(volatile_broad=True)
    index = next(
        item
        for item in range(201, len(calm_frames["QQQ"]))
        if calm_frames["QQQ"].iloc[item]["date"].isocalendar().week
        != calm_frames["QQQ"].iloc[item - 1]["date"].isocalendar().week
    )
    calm = build_track_b("L02", "TQQQ").evaluate(calm_frames, index)
    volatile = build_track_b("L02", "TQQQ").evaluate(volatile_frames, index)
    # Both use the same QQQ/SOXX trend signals; only actual TQQQ prices differ.
    assert set(calm.target_weights) == set(volatile.target_weights)
    assert volatile.target_weights["SOXL"] < calm.target_weights["SOXL"]


def test_l05_risk_budget_is_bounded_and_stateful() -> None:
    strategy = build_track_b("L05", "TQQQ")
    frames = _frames()
    first = strategy.evaluate(frames, 500)
    assert sum(first.target_weights.values()) <= 0.99 + 1e-12
    state = strategy.state
    assert isinstance(state, TrackBState)
    # Fixed-share strategy retains its target between entry and exit.
    second = strategy.evaluate(frames, 501, state)
    assert second.target_weights == first.target_weights


def test_l10_components_are_weighted_and_state_can_be_externalized() -> None:
    frames = _frames()
    strategy = build_track_b("L10", "TQQQ")
    external_state = TrackBState(active={"TQQQ": False, "SOXL": False})
    signal = strategy.evaluate(frames, 500, external_state)
    assert signal.reason_code == "L10_LEVERAGED_TREND_PULLBACK_ENSEMBLE"
    assert set(external_state.component_states) == {"L02", "L06"}
    assert sum(signal.target_weights.values()) <= 0.99


def test_variants_are_frozen_and_parseable() -> None:
    for strategy_id in TRACK_B_STRATEGY_IDS:
        variants = strategy_variants(strategy_id)
        assert variants[0] == "primary"
        assert len(variants) == 3
    assert strategy_variants("L09") == ("primary", "cooldown_5", "cooldown_15")
