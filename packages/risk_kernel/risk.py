"""Risk decision.  This function has no broker, database, or network access."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from packages.contracts.canonical import hash_without
from packages.contracts.models import (
    AccountSnapshotV1,
    ContractError,
    ControlStateV1,
    MarketSnapshotV1,
    OperatingModeV1,
    OrderPlanV1,
    OrderRiskSnapshotV1,
    PositionSnapshotV1,
    QuoteV1,
    RiskDecisionV1,
    RiskInputV1,
    validate_occ_identity,
)
from packages.domain import reconciliation_hash


def _recompute_plan_economics(plan: OrderPlanV1) -> tuple[Decimal, bool]:
    """Recompute loss and validate width without trusting derived plan fields."""
    try:
        maximum_loss = plan.recompute_maximum_loss()
        width = plan.vertical_width()
    except (ContractError, IndexError, TypeError, ValueError):
        return plan.maximum_loss, False
    valid_width = width is None or plan.limit_debit < width
    return maximum_loss, valid_width


def _quote_is_current(quote: QuoteV1, *, now: datetime, ttl_seconds: int) -> bool:
    return (
        quote.event_time <= now
        and quote.available_time <= now
        and now - quote.event_time <= timedelta(seconds=ttl_seconds)
    )


def evaluate_risk(
    risk_input: RiskInputV1,
    market: MarketSnapshotV1,
    account: AccountSnapshotV1,
    positions: PositionSnapshotV1,
    order_risk: OrderRiskSnapshotV1,
    control: ControlStateV1,
    *,
    now: datetime,
) -> RiskDecisionV1:
    """Return an approval only for the exact prospective, current risk input."""
    now = now.astimezone(UTC)
    plan = risk_input.plan
    reasons: list[str] = []
    economic_maximum_loss, valid_vertical_debit = _recompute_plan_economics(plan)
    if plan.plan_hash != hash_without(plan, "plan_hash"):
        reasons.append("RISK_PLAN_HASH_MISMATCH")
    if risk_input.risk_input_hash != hash_without(risk_input, "risk_input_hash"):
        reasons.append("RISK_INPUT_HASH_MISMATCH")
    if market.content_hash != hash_without(market, "content_hash"):
        reasons.append("RISK_MARKET_CONTENT_HASH_MISMATCH")
    if account.content_hash != hash_without(account, "content_hash"):
        reasons.append("RISK_ACCOUNT_CONTENT_HASH_MISMATCH")
    if positions.content_hash != hash_without(positions, "content_hash"):
        reasons.append("RISK_POSITION_CONTENT_HASH_MISMATCH")
    if order_risk.content_hash != hash_without(order_risk, "content_hash"):
        reasons.append("RISK_ORDER_RISK_CONTENT_HASH_MISMATCH")
    if control.content_hash != hash_without(control, "content_hash"):
        reasons.append("RISK_CONTROL_CONTENT_HASH_MISMATCH")
    if risk_input.risk_policy.policy_hash != hash_without(
        risk_input.risk_policy, "policy_hash"
    ):
        reasons.append("RISK_POLICY_HASH_MISMATCH")
    if economic_maximum_loss != plan.maximum_loss:
        reasons.append("RISK_MAXIMUM_LOSS_MISMATCH")
    if not valid_vertical_debit:
        reasons.append("RISK_INVALID_VERTICAL_DEBIT")
    if risk_input.mode not in {OperatingModeV1.PAPER_ARMED, OperatingModeV1.PAPER_DEMO_ARMED}:
        reasons.append("RISK_MODE_NOT_ARMED")
    if now >= risk_input.entry_cutoff_at:
        reasons.append("RISK_ENTRY_CUTOFF_REACHED")
    if now + timedelta(minutes=60) > risk_input.flatten_at:
        reasons.append("RISK_HOLD_WINDOW_CROSSES_FLATTEN")
    if (
        control.mode != risk_input.mode
        or control.mode not in {OperatingModeV1.PAPER_ARMED, OperatingModeV1.PAPER_DEMO_ARMED}
    ):
        reasons.append("RISK_CURRENT_CONTROL_MODE_MISMATCH")
    if (
        risk_input.control_state_hash != control.content_hash
        or risk_input.control_state_version != control.version
    ):
        reasons.append("RISK_CONTROL_STATE_MISMATCH")
    if control.account_id != account.account_id:
        reasons.append("RISK_CONTROL_ACCOUNT_MISMATCH")
    if now - control.reconciled_at > timedelta(seconds=15) or control.reconciled_at > now:
        reasons.append("RISK_RECONCILIATION_STALE")
    if control.reconciliation_hash != reconciliation_hash(account, positions, order_risk):
        reasons.append("RISK_RECONCILIATION_HASH_MISMATCH")
    if risk_input.market_snapshot_hash != market.content_hash:
        reasons.append("RISK_MARKET_HASH_MISMATCH")
    if risk_input.account_snapshot_hash != account.content_hash:
        reasons.append("RISK_ACCOUNT_HASH_MISMATCH")
    if risk_input.position_snapshot_hash != positions.content_hash:
        reasons.append("RISK_POSITION_HASH_MISMATCH")
    if risk_input.order_risk_snapshot_hash != order_risk.content_hash:
        reasons.append("RISK_ORDER_RISK_HASH_MISMATCH")
    if plan.account_id != account.account_id:
        reasons.append("RISK_ACCOUNT_MISMATCH")
    if positions.account_id != account.account_id or order_risk.account_id != account.account_id:
        reasons.append("RISK_CROSS_ACCOUNT_SNAPSHOT")
    if positions.legs:
        reasons.append("RISK_EXISTING_POSITION")
    if plan.account_snapshot_version != account.version or plan.position_snapshot_version != positions.version:
        reasons.append("RISK_SNAPSHOT_VERSION_MISMATCH")
    if plan.order_risk_snapshot_version != order_risk.version:
        reasons.append("RISK_ORDER_RISK_VERSION_MISMATCH")
    if economic_maximum_loss > risk_input.risk_policy.max_per_trade_loss:
        reasons.append("RISK_PER_TRADE_LIMIT")
    if economic_maximum_loss > account.buying_power:
        reasons.append("RISK_INSUFFICIENT_BUYING_POWER")
    if account.day_start_equity is None:
        reasons.append("RISK_DAY_START_EQUITY_MISSING")
    else:
        realized_day_loss = max(account.day_start_equity - account.equity, Decimal("0"))
        if realized_day_loss + economic_maximum_loss > risk_input.risk_policy.max_daily_loss:
            reasons.append("RISK_DAILY_LOSS_LIMIT")
    foreign_reservations = [item for item in order_risk.reservations if item.plan_hash != plan.plan_hash]
    own_reservations = [item for item in order_risk.reservations if item.plan_hash == plan.plan_hash]
    if foreign_reservations or len(own_reservations) != 1 or order_risk.working_client_order_ids:
        reasons.append("RISK_SINGLE_EXPOSURE_RESERVATION")
    expected_reservation_id = f"reservation-{plan.plan_hash.removeprefix('sha256:')[:24]}"
    expected_reservation_expiry = now + timedelta(
        seconds=risk_input.risk_policy.approval_ttl_seconds
    )
    if own_reservations and any(
        item.maximum_loss != economic_maximum_loss
        or item.remaining_quantity != plan.quantity
        or item.reservation_id != expected_reservation_id
        or item.status != "APPROVED"
        or item.expires_at != expected_reservation_expiry
        for item in own_reservations
    ):
        reasons.append("RISK_RESERVATION_MISMATCH")
    prospective_loss = sum(
        (item.maximum_loss for item in foreign_reservations), Decimal("0")
    ) + economic_maximum_loss
    if prospective_loss > risk_input.risk_policy.max_total_reserved_loss:
        reasons.append("RISK_TOTAL_RESERVATION_LIMIT")
    quote = market.underlying_quotes.get(plan.underlying)
    if quote is None or not _quote_is_current(
        quote, now=now, ttl_seconds=risk_input.risk_policy.quote_ttl_seconds
    ):
        reasons.append("RISK_STALE_QUOTE")
    if market.clock is None:
        reasons.append("RISK_MARKET_CLOCK_MISSING")
    else:
        if not market.clock.is_open:
            reasons.append("RISK_MARKET_CLOSED")
        if market.clock.as_of > now or now - market.clock.as_of > timedelta(
            seconds=risk_input.risk_policy.quote_ttl_seconds
        ):
            reasons.append("RISK_MARKET_CLOCK_STALE")
        if market.clock.next_close <= now:
            reasons.append("RISK_MARKET_CLOSE_PASSED")
        if now + timedelta(minutes=60) > market.clock.next_close:
            reasons.append("RISK_HOLD_WINDOW_CROSSES_MARKET_CLOSE")
    option_contracts = {contract.symbol: contract for contract in market.option_contracts}
    for leg in plan.legs:
        contract = option_contracts.get(leg.symbol)
        if contract is None:
            reasons.append("RISK_OPTION_QUOTE_MISSING")
            continue
        try:
            validate_occ_identity(
                symbol=leg.symbol,
                underlying=plan.underlying,
                right=leg.right,
                strike=leg.strike,
                expiration=leg.expiration,
            )
            validate_occ_identity(
                symbol=contract.symbol,
                underlying=contract.underlying,
                right=contract.right,
                strike=contract.strike,
                expiration=contract.expiration,
            )
        except ContractError:
            reasons.append("RISK_OCC_IDENTITY_MISMATCH")
        if (
            contract.underlying != plan.underlying
            or contract.right != leg.right
            or contract.strike != leg.strike
            or contract.expiration != leg.expiration
        ):
            reasons.append("RISK_OPTION_CONTRACT_MISMATCH")
        if contract.expiration <= now:
            reasons.append("RISK_OPTION_EXPIRED")
        if contract.quote.ask <= 0 or contract.quote.ask < contract.quote.bid:
            reasons.append("RISK_OPTION_QUOTE_INVALID")
        elif not _quote_is_current(
            contract.quote, now=now, ttl_seconds=risk_input.risk_policy.quote_ttl_seconds
        ):
            reasons.append("RISK_STALE_OPTION_QUOTE")
    reasons = list(dict.fromkeys(reasons))
    approved = not reasons
    reservation_id = None
    if approved:
        reservation_id = expected_reservation_id
        reasons = ["RISK_APPROVED"]
    return RiskDecisionV1(
        decision_id=f"risk-{plan.plan_hash.removeprefix('sha256:')[:24]}",
        plan_hash=plan.plan_hash,
        risk_input_hash=risk_input.risk_input_hash,
        approved=approved,
        reason_codes=tuple(reasons),
        maximum_loss=economic_maximum_loss,
        expires_at=now + timedelta(seconds=risk_input.risk_policy.approval_ttl_seconds),
        reservation_id=reservation_id,
    )
