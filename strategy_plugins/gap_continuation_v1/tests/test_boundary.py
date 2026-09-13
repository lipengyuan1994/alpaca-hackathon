"""Boundary fixtures: degenerate gaps, nonfinite data, missing keys, decision grid.

A degenerate gap is a boundary fixture pinned at the frozen ``1e-6`` floor
(sigma or log-gap magnitude), not a new parameter: the guard refuses it instead
of letting an amplified z-score trade (plan section 6).  The frozen decision
grid is the single weekday instant 10:30:01 ET.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from gap_continuation_v1.plugin import LOCAL_FEATURE_KEYS, Plugin
from gap_continuation_v1.signal import SignalResult, evaluate_signal
from gap_test_support import (
    NEUTRAL,
    SMH_BULLISH,
    build_config,
    build_context,
    build_universe,
    decimal_features,
)

from packages.contracts.models import EntryTemplateRequestV1, NoTradeV1

_SIGNAL_FEATURE_KEYS = (
    "close_completed_15m_v1",
    "continuation_ratio_v1",
    "gap_log_adjusted_v1",
    "gap_z_60_v1",
    "session_iex_vwap_v1",
    "sigma_gap_60_v1",
)

_QUALIFYING_OVERRIDES = {
    "gap_z_60_v1": "1.5",
    "continuation_ratio_v1": "0.3",
}

_GRID_UNIVERSE = build_universe(smh=SMH_BULLISH)
_NEUTRAL_UNIVERSE = build_universe()


def _signal(block: dict[str, str]) -> SignalResult:
    return evaluate_signal(underlying="SMH", features=decimal_features(block))


@pytest.mark.parametrize("sigma", ["0.000001", "0"], ids=["pinned_floor", "zero"])
def test_degenerate_sigma_refuses_despite_qualifying_z(sigma: str) -> None:
    block = {
        **NEUTRAL,
        **_QUALIFYING_OVERRIDES,
        "sigma_gap_60_v1": sigma,
        "gap_log_adjusted_v1": "0.03",
    }
    result = _signal(block)
    assert result.action == "NO_TRADE"
    assert result.reason_codes == ("GAP_CONTINUATION_GATE_NOT_MET",)
    assert result.score is None


@pytest.mark.parametrize(
    "gap_log", ["0", "-0"], ids=["zero", "negative_zero"]
)
def test_zero_gap_log_refuses_despite_qualifying_z(gap_log: str) -> None:
    block = {**NEUTRAL, **_QUALIFYING_OVERRIDES, "gap_log_adjusted_v1": gap_log}
    result = _signal(block)
    assert result.action == "NO_TRADE"
    assert result.reason_codes == ("GAP_CONTINUATION_GATE_NOT_MET",)
    assert result.score is None


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("close_completed_15m_v1", Decimal("NaN")),
        ("continuation_ratio_v1", Decimal("NaN")),
        ("gap_log_adjusted_v1", Decimal("Infinity")),
        ("gap_z_60_v1", Decimal("NaN")),
        ("session_iex_vwap_v1", Decimal("-Infinity")),
        ("sigma_gap_60_v1", Decimal("NaN")),
    ],
    ids=["close_nan", "cont_nan", "gap_log_inf", "gap_z_nan", "vwap_neg_inf", "sigma_nan"],
)
def test_nonfinite_delivered_features_are_data_missing(key: str, value: Decimal) -> None:
    features = decimal_features(NEUTRAL)
    features[key] = value
    result = evaluate_signal(underlying="SMH", features=features)
    assert result.action == "NO_TRADE"
    assert result.reason_codes == ("DATA_MISSING",)
    assert result.score is None


@pytest.mark.parametrize("key", _SIGNAL_FEATURE_KEYS)
def test_any_missing_signal_feature_key_is_data_missing(key: str) -> None:
    features = decimal_features(NEUTRAL)
    del features[key]
    result = evaluate_signal(underlying="SMH", features=features)
    assert result.action == "NO_TRADE"
    assert result.reason_codes == ("DATA_MISSING",)


def test_neutral_zero_features_pass_the_plugin_guards_then_refuse_at_the_gate() -> None:
    # The full NEUTRAL block carries every guard feature (early-close zero,
    # continuity clear, adjustment basis one), so the plugin loop reaches the
    # signal and refuses on the gate rather than on a guard code.
    features = decimal_features(NEUTRAL)
    assert features["early_close_session_v1"] == 0
    assert features["corporate_action_continuity_clear_v1"] == 1
    assert features["adjustment_basis_v1"] == 1
    result = evaluate_signal(underlying="SMH", features=features)
    assert result.action == "NO_TRADE"
    assert result.reason_codes == ("GAP_CONTINUATION_GATE_NOT_MET",)
    assert LOCAL_FEATURE_KEYS == tuple(sorted(LOCAL_FEATURE_KEYS))


_VALID_GRID = (datetime(2026, 8, 28, 14, 30, 1, tzinfo=UTC),)
_GRID_IDS = ("103001_et",)


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
    datetime(2026, 8, 28, 15, 0, 1, tzinfo=UTC),
    datetime(2026, 8, 28, 14, 30, 2, tzinfo=UTC),
    datetime(2026, 8, 28, 14, 30, 1, 1, tzinfo=UTC),
    datetime(2026, 8, 28, 14, 0, 1, tzinfo=UTC),
    datetime(2026, 8, 29, 14, 30, 1, tzinfo=UTC),
    datetime(2026, 8, 30, 14, 30, 1, tzinfo=UTC),
)
_OFF_GRID_IDS = (
    "second_zero",
    "minute_29",
    "minute_31",
    "later_grid_time_1100_et",
    "second_two",
    "microsecond_one",
    "earlier_grid_time_1000_et",
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
    assert decision.primary_reason_code == "GAP_CONTINUATION_GATE_NOT_MET"
