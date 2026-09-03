"""Boundary fixtures: degenerate ranges, nonfinite data, missing keys, decision grid.

A zero observed range is a boundary fixture pinned at the frozen ``1e-6``
log-width floor, not a new parameter: the guard refuses it instead of letting
an amplified break fraction trade (plan section 6).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from opening_range_breakout_v1.plugin import LOCAL_FEATURE_KEYS, Plugin
from opening_range_breakout_v1.signal import SignalResult, evaluate_signal
from orb_test_support import (
    NEUTRAL,
    SMH_BULLISH,
    build_config,
    build_context,
    build_universe,
    decimal_features,
)

from packages.contracts.models import EntryTemplateRequestV1, NoTradeV1

_QUALIFYING_OVERRIDES = {
    "up_break_fraction_or30_v1": "0.15",
    "down_break_fraction_or30_v1": "-1.15",
    "volume_ratio_same_time_20_v1": "1.5",
}

_GRID_UNIVERSE = build_universe(smh=SMH_BULLISH)
_NEUTRAL_UNIVERSE = build_universe()


def _signal(block: dict[str, str]) -> SignalResult:
    return evaluate_signal(underlying="SMH", features=decimal_features(block))


@pytest.mark.parametrize(
    "width",
    ["0.000001", "0", "-0.05"],
    ids=["pinned_floor", "zero", "negative"],
)
def test_degenerate_opening_range_width_refuses_despite_qualifying_breaks(
    width: str,
) -> None:
    block = {**NEUTRAL, **_QUALIFYING_OVERRIDES, "opening_range_width_log_v1": width}
    result = _signal(block)
    assert result.action == "NO_TRADE"
    assert result.reason_codes == ("OPENING_RANGE_BREAKOUT_GATE_NOT_MET",)
    assert result.score is None


@pytest.mark.parametrize(
    ("high", "low", "width"),
    [
        ("95", "100", "-0.05129329"),
        ("100", "0", "1"),
        ("100", "-5", "1.05"),
    ],
    ids=["inverted", "zero_low", "negative_low"],
)
def test_inverted_or_nonpositive_ranges_refuse(high: str, low: str, width: str) -> None:
    block = {
        **NEUTRAL,
        **_QUALIFYING_OVERRIDES,
        "opening_range_high_0930_1000_adjusted_v1": high,
        "opening_range_low_0930_1000_adjusted_v1": low,
        "opening_range_width_log_v1": width,
    }
    result = _signal(block)
    assert result.action == "NO_TRADE"
    assert result.reason_codes == ("OPENING_RANGE_BREAKOUT_GATE_NOT_MET",)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("up_break_fraction_or30_v1", Decimal("NaN")),
        ("down_break_fraction_or30_v1", Decimal("NaN")),
        ("volume_ratio_same_time_20_v1", Decimal("Infinity")),
        ("session_iex_vwap_v1", Decimal("-Infinity")),
        ("opening_range_width_log_v1", Decimal("NaN")),
        ("close_completed_15m_v1", Decimal("NaN")),
    ],
    ids=["up_nan", "down_nan", "volume_inf", "vwap_neg_inf", "width_nan", "close_nan"],
)
def test_nonfinite_delivered_features_are_data_missing(key: str, value: Decimal) -> None:
    features = decimal_features(NEUTRAL)
    features[key] = value
    result = evaluate_signal(underlying="SMH", features=features)
    assert result.action == "NO_TRADE"
    assert result.reason_codes == ("DATA_MISSING",)
    assert result.score is None


@pytest.mark.parametrize("key", LOCAL_FEATURE_KEYS)
def test_any_missing_required_feature_key_is_data_missing(key: str) -> None:
    features = decimal_features(NEUTRAL)
    del features[key]
    result = evaluate_signal(underlying="SMH", features=features)
    assert result.action == "NO_TRADE"
    assert result.reason_codes == ("DATA_MISSING",)


_VALID_GRID = (
    datetime(2026, 8, 28, 14, 30, 1, tzinfo=UTC),
    datetime(2026, 8, 28, 15, 0, 1, tzinfo=UTC),
    datetime(2026, 8, 28, 15, 30, 1, tzinfo=UTC),
    datetime(2026, 8, 28, 16, 0, 1, tzinfo=UTC),
    datetime(2026, 8, 28, 16, 30, 1, tzinfo=UTC),
    datetime(2026, 8, 28, 17, 0, 1, tzinfo=UTC),
    datetime(2026, 8, 28, 17, 30, 1, tzinfo=UTC),
    datetime(2026, 8, 28, 18, 0, 1, tzinfo=UTC),
    datetime(2026, 8, 28, 18, 30, 1, tzinfo=UTC),
)
_GRID_IDS = ("1030", "1100", "1130", "1200", "1230", "1300", "1330", "1400", "1430")


@pytest.mark.parametrize("as_of", _VALID_GRID, ids=_GRID_IDS)
def test_every_frozen_grid_time_produces_the_entry(as_of: datetime) -> None:
    context = build_context(values=_GRID_UNIVERSE, as_of=as_of)
    evaluation = Plugin().evaluate(context, build_config())
    decision = evaluation.decision
    assert isinstance(decision, EntryTemplateRequestV1)
    assert decision.underlying == "SMH"


_OFF_GRID = (
    datetime(2026, 8, 28, 14, 30, 0, tzinfo=UTC),
    datetime(2026, 8, 28, 14, 29, 1, tzinfo=UTC),
    datetime(2026, 8, 28, 14, 31, 1, tzinfo=UTC),
    datetime(2026, 8, 28, 15, 1, 1, tzinfo=UTC),
    datetime(2026, 8, 28, 18, 31, 1, tzinfo=UTC),
    datetime(2026, 8, 28, 14, 30, 2, tzinfo=UTC),
    datetime(2026, 8, 28, 14, 30, 1, 500, tzinfo=UTC),
    datetime(2026, 8, 29, 14, 30, 1, tzinfo=UTC),
    datetime(2026, 8, 30, 14, 30, 1, tzinfo=UTC),
)
_OFF_GRID_IDS = (
    "second_zero",
    "minute_29",
    "minute_31",
    "minute_01",
    "past_end",
    "second_two",
    "microsecond",
    "saturday",
    "sunday",
)


@pytest.mark.parametrize("as_of", _OFF_GRID, ids=_OFF_GRID_IDS)
def test_off_grid_times_refuse_outside_decision_window(as_of: datetime) -> None:
    context = build_context(values=_GRID_UNIVERSE, as_of=as_of)
    evaluation = Plugin().evaluate(context, build_config())
    decision = evaluation.decision
    assert isinstance(decision, NoTradeV1)
    assert decision.primary_reason_code == "OUTSIDE_DECISION_WINDOW"


def test_neutral_universe_refuses_with_gate_not_met() -> None:
    evaluation = Plugin().evaluate(build_context(values=_NEUTRAL_UNIVERSE), build_config())
    decision = evaluation.decision
    assert isinstance(decision, NoTradeV1)
    assert decision.primary_reason_code == "OPENING_RANGE_BREAKOUT_GATE_NOT_MET"
