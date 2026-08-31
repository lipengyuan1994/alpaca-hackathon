"""Pure V13.5 strike-regime and management rules."""

from __future__ import annotations

from decimal import Decimal

from .config import WheelStrategyConfig


def trend_is_up(completed_closes: tuple[Decimal, ...], *, sessions: int) -> bool | None:
    """Compare the last completed close with its trailing completed-session mean."""
    if len(completed_closes) < sessions:
        return None
    window = completed_closes[-sessions:]
    return window[-1] > sum(window, Decimal("0")) / Decimal(sessions)


def target_strike_fraction(*, right: str, trend_up: bool, config: WheelStrategyConfig) -> Decimal:
    if right == "PUT":
        distance = config.uptrend_put_otm_fraction if trend_up else config.downtrend_put_otm_fraction
        return Decimal("1") - distance
    if right == "CALL":
        distance = config.uptrend_call_otm_fraction if trend_up else config.downtrend_call_otm_fraction
        return Decimal("1") + distance
    raise ValueError("WHEEL_RIGHT_UNSUPPORTED")


def should_take_profit(*, entry_credit: Decimal, close_debit: Decimal, target_fraction: Decimal) -> bool:
    """Match the research boundary: equality does not trigger an exit."""
    return entry_credit - close_debit > entry_credit * target_fraction
