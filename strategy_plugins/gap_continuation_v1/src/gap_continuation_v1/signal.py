"""Pure frozen signal for Group B standardized gap continuation.

This module has no I/O and deliberately knows nothing about option symbols,
strikes, prices, contracts, accounts, or orders.  It consumes only the frozen
delivered features of
``research/candidates/gap_continuation__all_feasible__o2_v1/feature_contract.yaml``.
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
    gap_z_threshold: Decimal = Decimal("1.00"),
    continuation_ratio_threshold: Decimal = Decimal("0.25"),
    gap_floor: Decimal = Decimal("0.000001"),
) -> SignalResult:
    """Evaluate the preregistered standardized-gap-continuation rule.

    Equality at either threshold is an entry.  VWAP alignment is strict, so an
    equal close/VWAP never enters.  The entry score is
    ``min(active_z/threshold, continuation_ratio/threshold)`` where the active
    z-score is ``gap_z_60_v1`` for a bullish entry and its negation for a
    bearish entry.  A degenerate gap (sigma pinned at the frozen floor or a log
    gap at or below the floor) refuses instead of entering on an amplified
    z-score.
    """
    required = (
        "close_completed_15m_v1",
        "continuation_ratio_v1",
        "gap_log_adjusted_v1",
        "gap_z_60_v1",
        "session_iex_vwap_v1",
        "sigma_gap_60_v1",
    )
    if any(key not in features for key in required):
        return SignalResult(underlying, "NO_TRADE", None, ("DATA_MISSING",))
    close = features["close_completed_15m_v1"]
    continuation_ratio = features["continuation_ratio_v1"]
    gap_log = features["gap_log_adjusted_v1"]
    gap_z = features["gap_z_60_v1"]
    session_vwap = features["session_iex_vwap_v1"]
    sigma_gap = features["sigma_gap_60_v1"]
    if not all(
        value.is_finite()
        for value in (
            close,
            continuation_ratio,
            gap_log,
            gap_z,
            session_vwap,
            sigma_gap,
        )
    ):
        return SignalResult(underlying, "NO_TRADE", None, ("DATA_MISSING",))
    if sigma_gap <= gap_floor or abs(gap_log) <= gap_floor:
        return SignalResult(underlying, "NO_TRADE", None, ("GAP_CONTINUATION_GATE_NOT_MET",))
    if (
        gap_z >= gap_z_threshold
        and continuation_ratio >= continuation_ratio_threshold
        and close > session_vwap
    ):
        score = min(
            gap_z / gap_z_threshold,
            continuation_ratio / continuation_ratio_threshold,
        )
        return SignalResult(
            underlying,
            "BUY",
            score,
            ("GAP_CONTINUATION_BULLISH",),
        )
    if (
        gap_z <= -gap_z_threshold
        and continuation_ratio >= continuation_ratio_threshold
        and close < session_vwap
    ):
        score = min(
            -gap_z / gap_z_threshold,
            continuation_ratio / continuation_ratio_threshold,
        )
        return SignalResult(
            underlying,
            "SELL",
            score,
            ("GAP_CONTINUATION_BEARISH",),
        )
    return SignalResult(
        underlying,
        "NO_TRADE",
        None,
        ("GAP_CONTINUATION_GATE_NOT_MET",),
    )
