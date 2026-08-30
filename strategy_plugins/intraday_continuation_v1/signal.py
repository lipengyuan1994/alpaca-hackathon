"""Pure frozen signal for Group A normalized intraday continuation.

This module has no I/O and deliberately knows nothing about option symbols,
strikes, prices, contracts, accounts, or orders.
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
    momentum_threshold: Decimal = Decimal("1.00"),
) -> SignalResult:
    """Evaluate the preregistered 60-minute momentum plus VWAP rule.

    Equality at either signed threshold is an entry.  VWAP alignment is strict,
    so an equal close/VWAP never enters.
    """
    required = (
        "close_completed_15m_v1",
        "momentum_z_60m_same_time_v1",
        "session_iex_vwap_v1",
    )
    if any(key not in features for key in required):
        return SignalResult(underlying, "NO_TRADE", None, ("DATA_MISSING",))
    close = features["close_completed_15m_v1"]
    momentum_z = features["momentum_z_60m_same_time_v1"]
    session_vwap = features["session_iex_vwap_v1"]
    if not all(value.is_finite() for value in (close, momentum_z, session_vwap)):
        return SignalResult(underlying, "NO_TRADE", None, ("DATA_MISSING",))
    if momentum_z >= momentum_threshold and close > session_vwap:
        return SignalResult(
            underlying,
            "BUY",
            abs(momentum_z),
            ("INTRADAY_CONTINUATION_BULLISH",),
        )
    if momentum_z <= -momentum_threshold and close < session_vwap:
        return SignalResult(
            underlying,
            "SELL",
            abs(momentum_z),
            ("INTRADAY_CONTINUATION_BEARISH",),
        )
    return SignalResult(
        underlying,
        "NO_TRADE",
        abs(momentum_z),
        ("INTRADAY_CONTINUATION_GATE_NOT_MET",),
    )
