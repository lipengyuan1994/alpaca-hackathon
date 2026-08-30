"""Pure frozen signal for Group A normalized VWAP reversion."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping


@dataclass(frozen=True)
class SignalResult:
    underlying: str
    action: str
    score: Decimal | None
    reason_codes: tuple[str, ...]


def evaluate_signal(
    *,
    underlying: str,
    features: Mapping[str, Decimal],
    deviation_threshold: Decimal = Decimal("1.50"),
    momentum_neutral_abs_max: Decimal = Decimal("0.50"),
) -> SignalResult:
    """Evaluate deviation-from-VWAP only in the preregistered low-trend region."""
    required = ("deviation_z_same_time_v1", "momentum_z_60m_same_time_v1")
    if any(key not in features for key in required):
        return SignalResult(underlying, "NO_TRADE", None, ("DATA_MISSING",))
    deviation_z = features["deviation_z_same_time_v1"]
    momentum_z = features["momentum_z_60m_same_time_v1"]
    if not all(value.is_finite() for value in (deviation_z, momentum_z)):
        return SignalResult(underlying, "NO_TRADE", None, ("DATA_MISSING",))
    # This gate is intentionally strict: abs(momentum_z) == 0.50 refuses.
    if abs(momentum_z) >= momentum_neutral_abs_max:
        return SignalResult(
            underlying,
            "NO_TRADE",
            abs(deviation_z) / deviation_threshold,
            ("VWAP_REVERSION_GATE_NOT_MET",),
        )
    if deviation_z <= -deviation_threshold:
        return SignalResult(
            underlying,
            "BUY",
            abs(deviation_z) / deviation_threshold,
            ("VWAP_REVERSION_BULLISH",),
        )
    if deviation_z >= deviation_threshold:
        return SignalResult(
            underlying,
            "SELL",
            abs(deviation_z) / deviation_threshold,
            ("VWAP_REVERSION_BEARISH",),
        )
    return SignalResult(
        underlying,
        "NO_TRADE",
        abs(deviation_z) / deviation_threshold,
        ("VWAP_REVERSION_GATE_NOT_MET",),
    )
