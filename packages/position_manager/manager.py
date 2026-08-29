"""Central V1 exit policies; strategy plug-ins remain entry-only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from packages.contracts.canonical import hash_without
from packages.contracts.models import (
    AccountSnapshotV1,
    ControlStateV1,
    ExecuteReduceOnlyPlanV1,
    ManagedPositionV1,
    MarketSnapshotV1,
    OperatingModeV1,
    OrderLegV1,
    PositionDirectiveV1,
    PositionMarketStateV1,
    PositionPolicyIdV1,
    PositionSnapshotV1,
    ReduceOnlyDecisionV1,
    ReduceOnlyOrderPlanV1,
)
from packages.domain.identifiers import deterministic_client_order_id


class PositionManagerError(ValueError):
    """Stable refusal from central position management."""


_REDUCE_CAPABLE_MODES = {
    OperatingModeV1.PAPER_DEMO_ARMED,
    OperatingModeV1.PAPER_ARMED,
    OperatingModeV1.FLATTENING,
    OperatingModeV1.HALTED,
}


def evaluate_position(
    position: ManagedPositionV1,
    state: PositionMarketStateV1,
    *,
    now: datetime,
) -> PositionDirectiveV1:
    """Evaluate one frozen platform exit policy on a completed bar."""
    now = now.astimezone(UTC)
    if state.as_of > now:
        raise PositionManagerError("POSITION_STATE_FROM_FUTURE")
    if position.status == "CLOSING":
        return PositionDirectiveV1(
            strategy_position_id=position.strategy_position_id,
            action="HOLD",
            urgency="NORMAL",
            reason_codes=("POSITION_ALREADY_CLOSING",),
            directive_expires_at=now + timedelta(seconds=60),
        )

    reason: str | None = None
    urgency = "NORMAL"
    if now >= state.competition_flatten_at:
        reason = "COMPETITION_FINAL_FLATTEN"
        urgency = "RISK_EXIT"
    elif now - position.opened_at >= timedelta(minutes=60):
        reason = "CENTRAL_MAX_HOLD_60M"
    elif position.position_policy_id == PositionPolicyIdV1.TREND_VWAP_OR_60M_V1:
        adverse_cross = (
            position.direction == "BULLISH" and state.underlying_price < state.session_vwap
        ) or (position.direction == "BEARISH" and state.underlying_price > state.session_vwap)
        if adverse_cross:
            reason = "CENTRAL_ADVERSE_VWAP_CROSS"
    elif position.position_policy_id == PositionPolicyIdV1.REVERSION_VWAP_TOUCH_OR_60M_V1:
        touched = (
            position.direction == "BULLISH" and state.underlying_price >= state.session_vwap
        ) or (position.direction == "BEARISH" and state.underlying_price <= state.session_vwap)
        if touched:
            reason = "CENTRAL_REVERSION_VWAP_TOUCH"
    else:  # pragma: no cover - enum makes this defensive
        raise PositionManagerError("POSITION_POLICY_NOT_SUPPORTED")

    return PositionDirectiveV1(
        strategy_position_id=position.strategy_position_id,
        action="CLOSE" if reason else "HOLD",
        urgency=urgency,
        reason_codes=(reason or "CENTRAL_POSITION_HOLD",),
        directive_expires_at=now + timedelta(seconds=60),
    )


def _signed_quantity(side: str, quantity: int) -> int:
    return quantity if side == "BUY" else -quantity


def reduce_only_violations(
    plan: ReduceOnlyOrderPlanV1,
    managed: ManagedPositionV1,
    positions: PositionSnapshotV1,
) -> tuple[str, ...]:
    """Independently prove that every close leg reduces the reconciled entry leg."""
    reasons: list[str] = []
    if plan.plan_hash != hash_without(plan, "plan_hash"):
        reasons.append("REDUCE_ONLY_PLAN_HASH_MISMATCH")
    if plan.strategy_position_id != managed.strategy_position_id:
        reasons.append("REDUCE_ONLY_POSITION_ID_MISMATCH")
    if plan.account_id != managed.account_id or positions.account_id != managed.account_id:
        reasons.append("REDUCE_ONLY_ACCOUNT_MISMATCH")
    if plan.underlying != managed.underlying or plan.template_id != managed.entry_plan.template_id:
        reasons.append("REDUCE_ONLY_ENTRY_BINDING_MISMATCH")
    if plan.position_policy_id != managed.position_policy_id:
        reasons.append("REDUCE_ONLY_POLICY_MISMATCH")
    if plan.quantity != managed.current_quantity:
        reasons.append("REDUCE_ONLY_QUANTITY_MISMATCH")
    if plan.quantity > managed.entry_plan.quantity:
        reasons.append("REDUCE_ONLY_QUANTITY_EXCEEDS_ENTRY")

    entry_legs = {leg.symbol: leg for leg in managed.entry_plan.legs}
    close_legs = {leg.symbol: leg for leg in plan.legs}
    reconciled = {leg.symbol: leg.quantity for leg in positions.legs}
    if set(entry_legs) != set(close_legs):
        reasons.append("REDUCE_ONLY_LEG_SET_MISMATCH")
    for symbol, entry_leg in entry_legs.items():
        close_leg = close_legs.get(symbol)
        if close_leg is None:
            continue
        expected_close_side = "SELL" if entry_leg.side == "BUY" else "BUY"
        if (
            close_leg.side != expected_close_side
            or close_leg.right != entry_leg.right
            or close_leg.strike != entry_leg.strike
            or close_leg.expiration != entry_leg.expiration
            or close_leg.multiplier != entry_leg.multiplier
        ):
            reasons.append("REDUCE_ONLY_LEG_NOT_EXACT_REVERSE")
        current = reconciled.get(symbol, 0)
        expected_sign = _signed_quantity(entry_leg.side, 1)
        if current == 0 or (current > 0) != (expected_sign > 0) or abs(current) < plan.quantity:
            reasons.append("REDUCE_ONLY_RECONCILED_POSITION_MISMATCH")
    return tuple(dict.fromkeys(reasons))


def build_reduce_only_plan(
    directive: PositionDirectiveV1,
    managed: ManagedPositionV1,
    market: MarketSnapshotV1,
    account: AccountSnapshotV1,
    positions: PositionSnapshotV1,
    control: ControlStateV1,
    *,
    now: datetime,
    quote_ttl_seconds: int,
) -> ReduceOnlyOrderPlanV1:
    """Build one atomic exact-reverse close plan from current option quotes."""
    now = now.astimezone(UTC)
    if directive.action not in {"REDUCE", "CLOSE"}:
        raise PositionManagerError("POSITION_DIRECTIVE_NOT_ACTIONABLE")
    if now > directive.directive_expires_at:
        raise PositionManagerError("POSITION_DIRECTIVE_EXPIRED")
    if managed.status != "OPEN":
        raise PositionManagerError("POSITION_NOT_OPEN")
    if account.account_id != managed.account_id or control.account_id != managed.account_id:
        raise PositionManagerError("POSITION_ACCOUNT_MISMATCH")

    contracts = {item.symbol: item for item in market.option_contracts}
    close_legs: list[OrderLegV1] = []
    net_credit = Decimal("0")
    for entry_leg in managed.entry_plan.legs:
        contract = contracts.get(entry_leg.symbol)
        if contract is None:
            raise PositionManagerError("POSITION_CLOSE_QUOTE_MISSING")
        if (
            contract.quote.event_time > now
            or contract.quote.available_time > now
            or now - contract.quote.event_time > timedelta(seconds=quote_ttl_seconds)
        ):
            raise PositionManagerError("POSITION_CLOSE_QUOTE_STALE")
        close_side = "SELL" if entry_leg.side == "BUY" else "BUY"
        close_legs.append(
            OrderLegV1(
                symbol=entry_leg.symbol,
                side=close_side,
                quantity=managed.current_quantity,
                right=entry_leg.right,
                strike=entry_leg.strike,
                expiration=entry_leg.expiration,
                multiplier=entry_leg.multiplier,
            )
        )
        net_credit += contract.quote.bid if close_side == "SELL" else -contract.quote.ask

    price_effect = "CREDIT" if net_credit >= 0 else "DEBIT"
    limit_price = max(abs(net_credit), Decimal("0.01"))
    material = {
        "strategy_position_id": managed.strategy_position_id,
        "legs": [leg.model_dump(mode="json") for leg in close_legs],
        "limit_price": limit_price,
        "price_effect": price_effect,
        "versions": [account.version, positions.version, control.version],
        "reasons": directive.reason_codes,
    }
    client_order_id = deterministic_client_order_id(f"exit-{managed.strategy_position_id}", material)
    plan = ReduceOnlyOrderPlanV1(
        plan_id=f"close-{client_order_id.removeprefix('paper-')[:24]}",
        strategy_position_id=managed.strategy_position_id,
        account_id=managed.account_id,
        underlying=managed.underlying,
        template_id=managed.entry_plan.template_id,
        legs=tuple(close_legs),
        quantity=managed.current_quantity,
        limit_price=limit_price,
        price_effect=price_effect,
        client_order_id=client_order_id,
        market_snapshot_hash=market.content_hash,
        account_snapshot_version=account.version,
        position_snapshot_version=positions.version,
        control_state_version=control.version,
        position_policy_id=managed.position_policy_id,
        close_reason_codes=directive.reason_codes,
    )
    violations = reduce_only_violations(plan, managed, positions)
    if violations:
        raise PositionManagerError(violations[0])
    return plan


def authorize_reduce_only(
    plan: ReduceOnlyOrderPlanV1,
    managed: ManagedPositionV1,
    positions: PositionSnapshotV1,
    control: ControlStateV1,
    *,
    now: datetime,
    ttl_seconds: int = 30,
) -> ReduceOnlyDecisionV1:
    """Authorize only an exact reduction against current reconciled state."""
    now = now.astimezone(UTC)
    reasons = list(reduce_only_violations(plan, managed, positions))
    if control.mode not in _REDUCE_CAPABLE_MODES:
        reasons.append("REDUCE_ONLY_MODE_NOT_ALLOWED")
    if plan.control_state_version != control.version:
        reasons.append("REDUCE_ONLY_CONTROL_VERSION_MISMATCH")
    if plan.position_snapshot_version != positions.version:
        reasons.append("REDUCE_ONLY_POSITION_VERSION_MISMATCH")
    allowed = not reasons
    return ReduceOnlyDecisionV1(
        decision_id=f"reduce-{plan.plan_hash.removeprefix('sha256:')[:24]}",
        plan_hash=plan.plan_hash,
        position_snapshot_hash=positions.content_hash,
        managed_position_hash=managed.content_hash,
        control_state_hash=control.content_hash,
        allowed=allowed,
        reason_codes=("REDUCE_ONLY_APPROVED",) if allowed else tuple(dict.fromkeys(reasons)),
        expires_at=now + timedelta(seconds=ttl_seconds),
    )


def build_execute_reduce_command(
    plan: ReduceOnlyOrderPlanV1,
    approval: ReduceOnlyDecisionV1,
    control: ControlStateV1,
) -> ExecuteReduceOnlyPlanV1:
    return ExecuteReduceOnlyPlanV1(
        command_id=f"command-{plan.plan_hash.removeprefix('sha256:')[:24]}",
        plan=plan,
        approval=approval,
        market_snapshot_hash=plan.market_snapshot_hash,
        account_snapshot_version=plan.account_snapshot_version,
        position_snapshot_version=plan.position_snapshot_version,
        control_state_hash=control.content_hash,
        control_state_version=control.version,
    )
