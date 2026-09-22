"""Pure T08 enhancement decision core shared by the backtest and live adapter.

This module has no broker, clock, filesystem, or network imports.  It computes
only strategy allocation intent and signal-state transitions; an execution
adapter remains responsible for orders and fill feedback.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any, Mapping, Sequence

ZERO = Decimal("0")
HALF = Decimal("0.495")
FULL = Decimal("0.99")


def _number(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime().date()
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _week(value: date) -> str:
    year, week, _ = value.isocalendar()
    return f"{year:04d}-W{week:02d}"


def _as_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


@dataclass(frozen=True)
class T08EnhancementState:
    """Persisted signals and actual-fill feedback needed between sessions."""

    core_target: Decimal = ZERO
    core_initialized: bool = False
    macro_ceiling: Decimal | None = None
    macro_inputs_valid: bool = False
    vol_ceiling: Decimal | None = None
    vol_inputs_valid: bool = False
    last_macro_review_week: str | None = None
    last_vol_review_week: str | None = None
    shock_liquidation_date: date | None = None
    cooldown_completed_sessions: int = 0
    cooldown_last_counted_cutoff: date | None = None
    shock_recovery_required: bool = False
    recovery_entry_date: date | None = None
    recovery_released: bool = False
    last_final_target: Decimal | None = None
    last_execution_date: date | None = None
    processed_feedback_ids: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "T08EnhancementState":
        source = dict(value or {})
        return cls(
            core_target=_number(source.get("core_target")) or ZERO,
            core_initialized=bool(source.get("core_initialized", False)),
            macro_ceiling=_number(source.get("macro_ceiling")),
            macro_inputs_valid=bool(source.get("macro_inputs_valid", False)),
            vol_ceiling=_number(source.get("vol_ceiling")),
            vol_inputs_valid=bool(source.get("vol_inputs_valid", False)),
            last_macro_review_week=source.get("last_macro_review_week"),
            last_vol_review_week=source.get("last_vol_review_week"),
            shock_liquidation_date=_date(source.get("shock_liquidation_date")),
            cooldown_completed_sessions=max(0, int(source.get("cooldown_completed_sessions", 0))),
            cooldown_last_counted_cutoff=_date(source.get("cooldown_last_counted_cutoff")),
            shock_recovery_required=bool(source.get("shock_recovery_required", False)),
            recovery_entry_date=_date(source.get("recovery_entry_date")),
            recovery_released=bool(source.get("recovery_released", False)),
            last_final_target=_number(source.get("last_final_target")),
            last_execution_date=_date(source.get("last_execution_date")),
            processed_feedback_ids=tuple(str(item) for item in source.get("processed_feedback_ids", ())),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "core_target": _as_text(self.core_target),
            "core_initialized": self.core_initialized,
            "macro_ceiling": _as_text(self.macro_ceiling),
            "macro_inputs_valid": self.macro_inputs_valid,
            "vol_ceiling": _as_text(self.vol_ceiling),
            "vol_inputs_valid": self.vol_inputs_valid,
            "last_macro_review_week": self.last_macro_review_week,
            "last_vol_review_week": self.last_vol_review_week,
            "shock_liquidation_date": None if self.shock_liquidation_date is None else self.shock_liquidation_date.isoformat(),
            "cooldown_completed_sessions": self.cooldown_completed_sessions,
            "cooldown_last_counted_cutoff": None if self.cooldown_last_counted_cutoff is None else self.cooldown_last_counted_cutoff.isoformat(),
            "shock_recovery_required": self.shock_recovery_required,
            "recovery_entry_date": None if self.recovery_entry_date is None else self.recovery_entry_date.isoformat(),
            "recovery_released": self.recovery_released,
            "last_final_target": _as_text(self.last_final_target),
            "last_execution_date": None if self.last_execution_date is None else self.last_execution_date.isoformat(),
            "processed_feedback_ids": list(self.processed_feedback_ids),
        }


@dataclass(frozen=True)
class T08EnhancementDecision:
    """Allocation intent with independently auditable rule restrictions."""

    target_weight: Decimal | None
    core_target: Decimal
    ceilings: Mapping[str, Decimal | None]
    reasons: tuple[str, ...]
    missing_inputs: tuple[str, ...]
    increases_blocked: bool
    current_shock: bool
    shock_exit_required: bool
    decision_id: str
    information_cutoff: date
    execution_session: date
    next_state: T08EnhancementState

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_weight": _as_text(self.target_weight),
            "core_target": _as_text(self.core_target),
            "ceilings": {key: _as_text(value) for key, value in sorted(self.ceilings.items())},
            "reasons": list(self.reasons),
            "missing_inputs": list(self.missing_inputs),
            "increases_blocked": self.increases_blocked,
            "current_shock": self.current_shock,
            "shock_exit_required": self.shock_exit_required,
            "decision_id": self.decision_id,
            "information_cutoff": self.information_cutoff.isoformat(),
            "execution_session": self.execution_session.isoformat(),
            "next_state": self.next_state.as_dict(),
        }


def evaluate_t08_enhancement(
    *,
    execution_session: Any,
    information_cutoff: Any,
    features: Mapping[str, Mapping[str, Any]],
    state: T08EnhancementState | Mapping[str, Any] | None = None,
    shock_enabled: bool = False,
    macro_enabled: bool = False,
    volatility_enabled: bool = False,
    weekly_review: bool = False,
    held_quantity: Decimal | str | float = ZERO,
    held_weight: Decimal | str | float = ZERO,
    recent_feedback: Sequence[Mapping[str, Any]] = (),
    operation_purchase_allowed: bool = True,
    macro_credit_threshold: Decimal | str | float = Decimal("-0.02"),
    volatility_target: Decimal | str | float = Decimal("0.50"),
    shock_cooldown_sessions: int = 3,
    asymmetry_threshold: Decimal | str | float = Decimal("1.5"),
    volatility_ratio_threshold: Decimal | str | float = Decimal("1.25"),
    candidate_id: str = "T08E",
    rule_hash: str = "",
) -> T08EnhancementDecision:
    """Evaluate the frozen T08 core plus optional S/M/V target ceilings.

    Inputs must describe the exact completed session in ``information_cutoff``.
    When a feature row carries ``date`` or ``asof``, it is checked here.  The
    adapter remains responsible for checking coverage of every mandatory
    feature symbol before calling this function.
    """

    execution_date = _date(execution_session)
    cutoff_date = _date(information_cutoff)
    if execution_date is None or cutoff_date is None or cutoff_date >= execution_date:
        raise ValueError("T08E_CUTOFF_MUST_PRECEDE_EXECUTION")
    current = state if isinstance(state, T08EnhancementState) else T08EnhancementState.from_mapping(state)
    next_state = current
    reasons: list[str] = []
    missing: list[str] = []
    ceilings: dict[str, Decimal | None] = {"shock": FULL, "macro": FULL, "volatility": FULL}
    target_weight: Decimal | None = None
    current_shock = False
    shock_exit_required = False
    increases_blocked = not operation_purchase_allowed

    def value(symbol: str, name: str, *, check_fresh: bool = True) -> Decimal | None:
        row = features.get(symbol.upper(), {})
        if not isinstance(row, Mapping):
            missing.append(f"{symbol}.{name}:symbol_missing")
            return None
        if check_fresh:
            stamp = _date(row.get("asof", row.get("date")))
            if stamp is None or stamp != cutoff_date:
                missing.append(f"{symbol}.{name}:stale_or_unstamped")
                return None
        result = _number(row.get(name))
        if result is None:
            missing.append(f"{symbol}.{name}:unavailable")
        return result

    # Fill feedback is the only authority for starting a shock cooldown or
    # declaring that a recovery entry actually occurred.
    processed_feedback_ids = list(next_state.processed_feedback_ids)
    # A disabled shock overlay is a true no-op: it must not consume feedback
    # or mutate persisted cooldown/recovery state.  This matters when a
    # candidate is replayed through a shared adapter or when overlay flags
    # differ between registered candidates.
    for feedback in recent_feedback if shock_enabled else ():
        feedback_id = str(feedback.get("activity_id", feedback.get("fill_id", feedback.get("order_id", "")))).strip()
        if not feedback_id:
            # Fixture and older adapters may not yet expose provider IDs.  A
            # canonical event fingerprint still makes replay idempotent.
            feedback_id = sha256(json.dumps({
                "side": str(feedback.get("side", "")).lower(),
                "filled_quantity": str(feedback.get("filled_quantity", "")),
                "reason": str(feedback.get("reason", "")),
                "date": str(feedback.get("execution_session", feedback.get("date", ""))),
                "fully_liquidated": bool(feedback.get("fully_liquidated")),
            }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if feedback_id in processed_feedback_ids:
            continue
        processed_feedback_ids.append(feedback_id)
        if _number(feedback.get("filled_quantity")) in (None, ZERO):
            continue
        reason = str(feedback.get("reason", ""))
        fill_date = _date(feedback.get("execution_session", feedback.get("date")))
        fully_liquidated = bool(feedback.get("fully_liquidated"))
        side = str(feedback.get("side", "")).lower()
        if side == "sell" and fully_liquidated and reason.startswith("T08E_SHOCK_EXIT") and fill_date is not None:
            next_state = replace(
                next_state,
                shock_liquidation_date=fill_date,
                cooldown_completed_sessions=0,
                cooldown_last_counted_cutoff=fill_date,
                shock_recovery_required=True,
                recovery_entry_date=None,
                recovery_released=False,
            )
        if side == "buy" and reason.startswith("T08E_RECOVERY_ENTRY") and fill_date is not None:
            next_state = replace(next_state, recovery_entry_date=fill_date, recovery_released=False)
    if tuple(processed_feedback_ids) != next_state.processed_feedback_ids:
        next_state = replace(next_state, processed_feedback_ids=tuple(processed_feedback_ids))

    if shock_enabled and next_state.shock_recovery_required and next_state.shock_liquidation_date is not None and cutoff_date > next_state.shock_liquidation_date and cutoff_date != next_state.cooldown_last_counted_cutoff:
        # One increment per observed, newly completed information session. If
        # runtime missed an exchange session this conservatively waits longer.
        next_state = replace(
            next_state,
            cooldown_completed_sessions=next_state.cooldown_completed_sessions + 1,
            cooldown_last_counted_cutoff=cutoff_date,
        )

    # The T08 core keeps its own state. A valid trend exit is still applied
    # when another mandatory input is absent.
    xlk_close = value("XLK", "close")
    xlk_sma200 = value("XLK", "sma200")
    core_target = next_state.core_target
    core_initialized = next_state.core_initialized
    core_inputs_valid = True
    if xlk_close is None or xlk_sma200 is None:
        core_inputs_valid = False
        increases_blocked = True
    elif xlk_close < xlk_sma200:
        core_target = ZERO
        core_initialized = True
        reasons.append("T08_XLK_BELOW_SMA200")
        if next_state.shock_recovery_required:
            next_state = replace(next_state, shock_recovery_required=False, recovery_entry_date=None, recovery_released=False)
    elif xlk_close > xlk_sma200:
        d20 = value("XLK", "d20")
        u20 = value("XLK", "u20")
        sigma20_xlk = value("XLK", "sigma20")
        sigma60_xlk = value("XLK", "sigma60")
        if None in (d20, u20, sigma20_xlk, sigma60_xlk):
            core_inputs_valid = False
            increases_blocked = True
        else:
            asymmetry_limit = _number(asymmetry_threshold) or Decimal("1.5")
            ratio_limit = _number(volatility_ratio_threshold) or Decimal("1.25")
            asymmetry = Decimal("1") if d20 == 0 and u20 == 0 else (Decimal("Infinity") if u20 == 0 and d20 > 0 else d20 / u20 if u20 > 0 else Decimal("1"))
            if sigma60_xlk == 0 and sigma20_xlk == 0:
                vol_ratio = ZERO
            elif sigma60_xlk == 0 and sigma20_xlk > 0:
                vol_ratio = Decimal("Infinity")
            elif sigma60_xlk < 0 or sigma20_xlk < 0:
                vol_ratio = None
            else:
                vol_ratio = sigma20_xlk / sigma60_xlk
            if vol_ratio is None:
                core_inputs_valid = False
                increases_blocked = True
            else:
                core_target = HALF if asymmetry > asymmetry_limit and vol_ratio > ratio_limit else FULL
                core_initialized = True
                reasons.append("T08_DOWNSIDE_VOL_BRAKE" if core_target == HALF else "T08_NORMAL_TREND")
    else:
        core_initialized = True
        reasons.append("T08_SMA200_EQUAL_RETAIN_CORE")
    next_state = replace(next_state, core_target=core_target, core_initialized=core_initialized)
    if not core_inputs_valid and xlk_close is not None and xlk_sma200 is not None and xlk_close >= xlk_sma200:
        reasons.append("CORE_INCREASE_BLOCKED_MISSING_FEATURE")

    # Required shock observations are checked every decision session.
    if shock_enabled:
        r1 = value("XLK", "r1")
        r5 = value("XLK", "r5")
        gap = value("XLK", "distribution_adjusted_opening_gap")
        shock_inputs_valid = None not in (r1, r5, gap)
        if not shock_inputs_valid:
            increases_blocked = True
            reasons.append("SHOCK_INCREASE_BLOCKED_MISSING_INPUT")
        else:
            current_shock = bool(r1 <= Decimal("-0.04") or r5 <= Decimal("-0.08") or gap <= Decimal("-0.03"))
            if current_shock:
                ceilings["shock"] = ZERO
                reasons.append("T08E_CURRENT_SHOCK")
                shock_exit_required = _number(held_quantity) is not None and _number(held_quantity) > ZERO
                if next_state.shock_recovery_required:
                    next_state = replace(next_state, shock_recovery_required=False, recovery_entry_date=None, recovery_released=False)
            elif next_state.shock_recovery_required:
                ema10 = value("XLK", "ema10")
                ema20 = value("XLK", "ema20")
                r3 = value("XLK", "r3")
                if None in (ema10, ema20, r3, xlk_close, xlk_sma200):
                    increases_blocked = True
                    ceilings["shock"] = ZERO
                    reasons.append("T08E_RECOVERY_BLOCKED_MISSING_INPUT")
                elif next_state.cooldown_completed_sessions < max(0, shock_cooldown_sessions):
                    ceilings["shock"] = ZERO
                    reasons.append("T08E_SHOCK_COOLDOWN")
                elif not (xlk_close > xlk_sma200 and xlk_close > ema10 and r3 > ZERO):
                    ceilings["shock"] = ZERO
                    reasons.append("T08E_RECOVERY_NOT_CONFIRMED")
                elif next_state.recovery_released:
                    ceilings["shock"] = FULL
                elif next_state.recovery_entry_date is not None:
                    if cutoff_date >= next_state.recovery_entry_date and xlk_close > ema20:
                        ceilings["shock"] = FULL
                        next_state = replace(next_state, shock_recovery_required=False, recovery_entry_date=None, recovery_released=True)
                        reasons.append("T08E_RECOVERY_CEILING_RELEASED")
                    else:
                        ceilings["shock"] = HALF
                        reasons.append("T08E_RECOVERY_HOLD_HALF")
                else:
                    ceilings["shock"] = HALF
                    reasons.append("T08E_RECOVERY_ENTRY_ALLOWED")

    # Weekly ceilings are stateful: missing review data keeps the last valid
    # ceiling and disables increases until a later valid weekly review.
    if macro_enabled:
        if weekly_review:
            rate = value("IEF", "r21")
            credit = value("HYG", "ratio_lqd_r21")
            if rate is None or credit is None:
                next_state = replace(next_state, macro_inputs_valid=False, last_macro_review_week=_week(execution_date))
                increases_blocked = True
                reasons.append("MACRO_REVIEW_INVALID_RETAIN_PREVIOUS_CEILING")
            else:
                rate_pressure = rate < Decimal("-0.04")
                credit_limit = _number(macro_credit_threshold) or Decimal("-0.02")
                credit_pressure = credit < credit_limit
                ceiling = ZERO if rate_pressure and credit_pressure else HALF if rate_pressure or credit_pressure else FULL
                next_state = replace(
                    next_state,
                    macro_ceiling=ceiling,
                    macro_inputs_valid=True,
                    last_macro_review_week=_week(execution_date),
                )
                reasons.append(f"MACRO_WEEKLY_{int(rate_pressure) + int(credit_pressure)}_FLAGS")
        if next_state.macro_ceiling is None or not next_state.macro_inputs_valid:
            increases_blocked = True
            # An unknown initial ceiling is not a zero-allocation signal.
            # Keep valid holdings governed by the other rules while blocking
            # any increase until the first valid weekly macro reading.
            ceilings["macro"] = next_state.macro_ceiling
            reasons.append("MACRO_INCREASE_BLOCKED_UNINITIALIZED_OR_STALE")
        else:
            ceilings["macro"] = next_state.macro_ceiling

    if volatility_enabled:
        if weekly_review:
            sigma = value("TECL", "sigma20")
            if sigma is None or sigma <= ZERO:
                next_state = replace(next_state, vol_inputs_valid=False, last_vol_review_week=_week(execution_date))
                increases_blocked = True
                reasons.append("VOL_REVIEW_INVALID_RETAIN_PREVIOUS_CEILING")
            else:
                target_vol = _number(volatility_target) or Decimal("0.50")
                ceiling = FULL * min(Decimal("1"), target_vol / sigma)
                next_state = replace(
                    next_state,
                    vol_ceiling=ceiling,
                    vol_inputs_valid=True,
                    last_vol_review_week=_week(execution_date),
                )
                reasons.append("VOL_WEEKLY_CEILING_UPDATED")
        if next_state.vol_ceiling is None or not next_state.vol_inputs_valid:
            increases_blocked = True
            # As with macro inputs, an unknown first volatility ceiling blocks
            # purchases but must not manufacture a full-liquidation target.
            ceilings["volatility"] = next_state.vol_ceiling
            reasons.append("VOL_INCREASE_BLOCKED_UNINITIALIZED_OR_STALE")
        else:
            ceilings["volatility"] = next_state.vol_ceiling

    # A shock recovery uses the corresponding actual-fill feedback reason to
    # distinguish its half-sized entry from ordinary core purchases.
    base_ceiling = core_target if core_initialized else FULL
    target_weight = min(base_ceiling, *(value for value in ceilings.values() if value is not None))
    if current_shock:
        target_weight = ZERO
    current_weight = _number(held_weight) or ZERO
    if increases_blocked and target_weight > current_weight:
        target_weight = None
        reasons.append("PURCHASE_BLOCKED_BY_MISSING_INPUT_OR_OPERATIONAL_RESTRICTION")
    elif not operation_purchase_allowed and target_weight > current_weight:
        target_weight = None
        reasons.append("PURCHASE_BLOCKED_BY_OPERATIONAL_RESTRICTION")

    next_state = replace(next_state, last_final_target=target_weight, last_execution_date=execution_date)
    identity_payload = {
        "candidate_id": candidate_id,
        "rule_hash": rule_hash,
        "execution_session": execution_date.isoformat(),
        "information_cutoff": cutoff_date.isoformat(),
        "target_weight": _as_text(target_weight),
        "core_target": _as_text(core_target),
        "ceilings": {key: _as_text(value) for key, value in sorted(ceilings.items())},
        "reasons": reasons,
        "missing_inputs": sorted(set(missing)),
    }
    decision_id = sha256(json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return T08EnhancementDecision(
        target_weight=target_weight,
        core_target=core_target,
        ceilings=ceilings,
        reasons=tuple(reasons),
        missing_inputs=tuple(sorted(set(missing))),
        increases_blocked=increases_blocked,
        current_shock=current_shock,
        shock_exit_required=shock_exit_required,
        decision_id=decision_id,
        information_cutoff=cutoff_date,
        execution_session=execution_date,
        next_state=next_state,
    )
