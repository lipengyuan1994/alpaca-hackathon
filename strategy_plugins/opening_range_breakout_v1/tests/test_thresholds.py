"""Threshold semantics: equality enters, near-misses refuse, strict VWAP alignment.

The frozen rule (plan section 6): equality at either threshold is an entry;
VWAP alignment is strict (an equal close/VWAP never enters); the entry score
is ``min(active_break/threshold, volume_ratio/threshold)``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from opening_range_breakout_v1.plugin import Plugin
from opening_range_breakout_v1.signal import evaluate_signal
from orb_test_support import (
    SMH_BULLISH,
    SOXL_BEARISH,
    SPY_HIGH,
    build_config,
    build_context,
    build_universe,
    decimal_features,
)

from packages.contracts.models import EntryTemplateRequestV1, NoTradeV1

_BULLISH_EQUALITY: dict[str, str] = {
    "close_completed_15m_v1": "101",
    "down_break_fraction_or30_v1": "-1.10",
    "opening_range_high_0930_1000_adjusted_v1": "100",
    "opening_range_low_0930_1000_adjusted_v1": "80",
    "opening_range_width_log_v1": "0.22314355",
    "session_iex_vwap_v1": "100",
    "up_break_fraction_or30_v1": "0.10",
    "volume_ratio_same_time_20_v1": "1.25",
}

_BEARISH_EQUALITY: dict[str, str] = {
    "close_completed_15m_v1": "75",
    "down_break_fraction_or30_v1": "0.10",
    "opening_range_high_0930_1000_adjusted_v1": "100",
    "opening_range_low_0930_1000_adjusted_v1": "80",
    "opening_range_width_log_v1": "0.22314355",
    "session_iex_vwap_v1": "78",
    "up_break_fraction_or30_v1": "-1.10",
    "volume_ratio_same_time_20_v1": "1.25",
}


def _decision(values: dict[str, Decimal]) -> object:
    evaluation = Plugin().evaluate(build_context(values=values), build_config())
    return evaluation.decision


def test_equality_at_both_thresholds_is_a_bullish_entry() -> None:
    pure = evaluate_signal(underlying="QQQ", features=decimal_features(_BULLISH_EQUALITY))
    assert pure.action == "BUY"
    assert pure.score == Decimal("1")
    assert pure.reason_codes == ("OPENING_RANGE_BREAKOUT_BULLISH",)
    decision = _decision(build_universe(qqq=_BULLISH_EQUALITY))
    assert isinstance(decision, EntryTemplateRequestV1)
    assert decision.underlying == "QQQ"
    assert decision.template_id == "CALL_DEBIT_SPREAD_V1"
    assert decision.signal_strength_bucket == "LOW"
    assert decision.entry_reason_codes == pure.reason_codes


def test_equality_at_both_thresholds_is_a_bearish_entry() -> None:
    pure = evaluate_signal(underlying="TQQQ", features=decimal_features(_BEARISH_EQUALITY))
    assert pure.action == "SELL"
    assert pure.score == Decimal("1")
    assert pure.reason_codes == ("OPENING_RANGE_BREAKOUT_BEARISH",)
    decision = _decision(build_universe(tqqq=_BEARISH_EQUALITY))
    assert isinstance(decision, EntryTemplateRequestV1)
    assert decision.underlying == "TQQQ"
    assert decision.template_id == "PUT_DEBIT_SPREAD_V1"
    assert decision.signal_strength_bucket == "LOW"
    assert decision.entry_reason_codes == pure.reason_codes


@pytest.mark.parametrize(
    "overrides",
    [
        {"up_break_fraction_or30_v1": "0.099999", "down_break_fraction_or30_v1": "-1.099999"},
        {"volume_ratio_same_time_20_v1": "1.249999"},
        {"session_iex_vwap_v1": "203.18592"},
        {"session_iex_vwap_v1": "205"},
    ],
    ids=[
        "break_just_below",
        "volume_just_below",
        "vwap_equals_close",
        "vwap_above_close",
    ],
)
def test_near_misses_and_strict_vwap_refuse(overrides: dict[str, str]) -> None:
    block = {**SMH_BULLISH, **overrides}
    pure = evaluate_signal(underlying="SMH", features=decimal_features(block))
    assert pure.action == "NO_TRADE"
    assert pure.reason_codes == ("OPENING_RANGE_BREAKOUT_GATE_NOT_MET",)
    assert pure.score is None
    decision = _decision(build_universe(smh=block))
    assert isinstance(decision, NoTradeV1)
    assert decision.primary_reason_code == "OPENING_RANGE_BREAKOUT_GATE_NOT_MET"


@pytest.mark.parametrize(
    ("block", "symbol", "bucket"),
    [
        (SMH_BULLISH, "SMH", "LOW"),
        (SOXL_BEARISH, "SOXL", "MEDIUM"),
        (SPY_HIGH, "SPY", "HIGH"),
    ],
    ids=["low_score_1.12", "medium_boundary_1.25", "high_boundary_1.75"],
)
def test_plugin_maps_scores_to_signal_strength_buckets(
    block: dict[str, str], symbol: str, bucket: str
) -> None:
    decision = _decision(build_universe(**{symbol.lower(): block}))
    assert isinstance(decision, EntryTemplateRequestV1)
    assert decision.underlying == symbol
    assert decision.signal_strength_bucket == bucket
