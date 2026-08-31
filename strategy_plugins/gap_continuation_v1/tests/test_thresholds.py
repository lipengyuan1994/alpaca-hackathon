"""Threshold semantics: equality enters, near-misses refuse, strict VWAP alignment.

The frozen rule (plan section 6): equality at either threshold is an entry;
VWAP alignment is strict (an equal close/VWAP never enters); the entry score
is ``min(active_z/threshold, continuation_ratio/threshold)`` where the active
z-score is ``gap_z_60_v1`` for a bullish entry and its negation for a bearish
entry.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from gap_continuation_v1.plugin import Plugin
from gap_continuation_v1.signal import evaluate_signal
from gap_test_support import (
    NEUTRAL,
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
    **NEUTRAL,
    "close_completed_15m_v1": "102.53184",
    "continuation_ratio_v1": "0.25",
    "gap_log_adjusted_v1": "0.02",
    "gap_z_60_v1": "1",
    "session_iex_vwap_v1": "102.4",
    "sigma_gap_60_v1": "0.02",
}

_BEARISH_EQUALITY: dict[str, str] = {
    **NEUTRAL,
    "close_completed_15m_v1": "97.53096",
    "continuation_ratio_v1": "0.25",
    "gap_log_adjusted_v1": "-0.02",
    "gap_z_60_v1": "-1",
    "session_iex_vwap_v1": "97.6",
    "sigma_gap_60_v1": "0.02",
}


def _decision(values: dict[str, Decimal]) -> object:
    evaluation = Plugin().evaluate(build_context(values=values), build_config())
    return evaluation.decision


def test_equality_at_both_thresholds_is_a_bullish_entry() -> None:
    pure = evaluate_signal(underlying="QQQ", features=decimal_features(_BULLISH_EQUALITY))
    assert pure.action == "BUY"
    assert pure.score == Decimal("1")
    assert pure.reason_codes == ("GAP_CONTINUATION_BULLISH",)
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
    assert pure.reason_codes == ("GAP_CONTINUATION_BEARISH",)
    decision = _decision(build_universe(tqqq=_BEARISH_EQUALITY))
    assert isinstance(decision, EntryTemplateRequestV1)
    assert decision.underlying == "TQQQ"
    assert decision.template_id == "PUT_DEBIT_SPREAD_V1"
    assert decision.signal_strength_bucket == "LOW"
    assert decision.entry_reason_codes == pure.reason_codes


@pytest.mark.parametrize(
    ("base", "symbol", "overrides"),
    [
        (SMH_BULLISH, "SMH", {"gap_z_60_v1": "0.999999"}),
        (SMH_BULLISH, "SMH", {"continuation_ratio_v1": "0.249999"}),
        (SMH_BULLISH, "SMH", {"session_iex_vwap_v1": "192.89830"}),
        (SOXL_BEARISH, "SOXL", {"session_iex_vwap_v1": "37"}),
    ],
    ids=[
        "gap_z_just_below",
        "continuation_just_below",
        "vwap_equals_close",
        "vwap_below_close_bearish",
    ],
)
def test_near_misses_and_strict_vwap_refuse(
    base: dict[str, str], symbol: str, overrides: dict[str, str]
) -> None:
    block = {**base, **overrides}
    pure = evaluate_signal(underlying=symbol, features=decimal_features(block))
    assert pure.action == "NO_TRADE"
    assert pure.reason_codes == ("GAP_CONTINUATION_GATE_NOT_MET",)
    assert pure.score is None
    decision = _decision(build_universe(**{symbol.lower(): block}))
    assert isinstance(decision, NoTradeV1)
    assert decision.primary_reason_code == "GAP_CONTINUATION_GATE_NOT_MET"


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
