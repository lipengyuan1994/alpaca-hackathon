"""Pure frozen signal for Group B opening-range breakout.

This module has no I/O and deliberately knows nothing about option symbols,
strikes, prices, contracts, accounts, or orders.  It consumes only the frozen
delivered features of
``research/candidates/opening_range_breakout__all_feasible__o2_v1/feature_contract.yaml``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping


@dataclass(frozen=True)
class SignalResult:
    """One symbol's deterministic semantic result."""

    underlying: str
    action: str
    score: Decimal | None
    reason_codes: tuple[str, ...]


def evaluate_signal(
    *,
    underlying: str,
    features: Mapping[str, Decimal],
    break_fraction_threshold: Decimal = Decimal("0.10"),
    volume_ratio_threshold: Decimal = Decimal("1.25"),
    range_floor: Decimal = Decimal("0.000001"),
) -> SignalResult:
    """Evaluate the preregistered breakout-plus-participation rule.

    Equality at either threshold is an entry.  VWAP alignment is strict, so an
    equal close/VWAP never enters.  The entry score is
    ``min(active_break_fraction/threshold, volume_ratio/threshold)`` where the
    active break fraction is ``up_break_fraction_or30_v1`` for a bullish entry
    and ``down_break_fraction_or30_v1`` for a bearish entry.  A degenerate
    opening range (nonpositive low, inverted high/low, or width pinned at the
    frozen floor) refuses instead of entering on an amplified break fraction.
    """
    required = (
        "close_completed_15m_v1",
        "down_break_fraction_or30_v1",
        "opening_range_high_0930_1000_adjusted_v1",
        "opening_range_low_0930_1000_adjusted_v1",
        "opening_range_width_log_v1",
        "session_iex_vwap_v1",
        "up_break_fraction_or30_v1",
        "volume_ratio_same_time_20_v1",
    )
    if any(key not in features for key in required):
        return SignalResult(underlying, "NO_TRADE", None, ("DATA_MISSING",))
    close = features["close_completed_15m_v1"]
    up_break = features["up_break_fraction_or30_v1"]
    down_break = features["down_break_fraction_or30_v1"]
    or_high = features["opening_range_high_0930_1000_adjusted_v1"]
    or_low = features["opening_range_low_0930_1000_adjusted_v1"]
    or_width = features["opening_range_width_log_v1"]
    volume_ratio = features["volume_ratio_same_time_20_v1"]
    session_vwap = features["session_iex_vwap_v1"]
    if not all(
        value.is_finite()
        for value in (
            close,
            up_break,
            down_break,
            or_high,
            or_low,
            or_width,
            volume_ratio,
            session_vwap,
        )
    ):
        return SignalResult(underlying, "NO_TRADE", None, ("DATA_MISSING",))
    if or_low <= 0 or or_high < or_low or or_width <= range_floor:
        return SignalResult(underlying, "NO_TRADE", None, ("OPENING_RANGE_BREAKOUT_GATE_NOT_MET",))
    if (
        up_break >= break_fraction_threshold
        and volume_ratio >= volume_ratio_threshold
        and close > session_vwap
    ):
        score = min(
            up_break / break_fraction_threshold,
            volume_ratio / volume_ratio_threshold,
        )
        return SignalResult(
            underlying,
            "BUY",
            score,
            ("OPENING_RANGE_BREAKOUT_BULLISH",),
        )
    if (
        down_break >= break_fraction_threshold
        and volume_ratio >= volume_ratio_threshold
        and close < session_vwap
    ):
        score = min(
            down_break / break_fraction_threshold,
            volume_ratio / volume_ratio_threshold,
        )
        return SignalResult(
            underlying,
            "SELL",
            score,
            ("OPENING_RANGE_BREAKOUT_BEARISH",),
        )
    return SignalResult(
        underlying,
        "NO_TRADE",
        None,
        ("OPENING_RANGE_BREAKOUT_GATE_NOT_MET",),
    )
