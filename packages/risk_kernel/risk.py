"""Risk decision.  This function has no broker, database, or network access."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from packages.contracts.models import (
    AccountSnapshotV1,
    MarketSnapshotV1,
    OperatingModeV1,
    OrderRiskSnapshotV1,
    PositionSnapshotV1,
    RiskDecisionV1,
    RiskInputV1,
)


def evaluate_risk(
    risk_input: RiskInputV1,
    market: MarketSnapshotV1,
    account: AccountSnapshotV1,
    positions: PositionSnapshotV1,
    order_risk: OrderRiskSnapshotV1,
    *,
    now: datetime,
) -> RiskDecisionV1:
    """Return an approval only for the exact prospective, current risk input."""
    now = now.astimezone(UTC)
    plan = risk_input.plan
    reasons: list[str] = []
    if risk_input.mode not in {OperatingModeV1.PAPER_ARMED, OperatingModeV1.PAPER_DEMO_ARMED}:
        reasons.append("RISK_MODE_NOT_ARMED")
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
    if plan.account_snapshot_version != account.version or plan.position_snapshot_version != positions.version:
        reasons.append("RISK_SNAPSHOT_VERSION_MISMATCH")
    if plan.order_risk_snapshot_version != order_risk.version:
        reasons.append("RISK_ORDER_RISK_VERSION_MISMATCH")
    if plan.maximum_loss > risk_input.risk_policy.max_per_trade_loss:
        reasons.append("RISK_PER_TRADE_LIMIT")
    foreign_reservations = [item for item in order_risk.reservations if item.plan_hash != plan.plan_hash]
    own_reservations = [item for item in order_risk.reservations if item.plan_hash == plan.plan_hash]
    if foreign_reservations or len(own_reservations) > 1 or order_risk.working_client_order_ids:
        reasons.append("RISK_SINGLE_EXPOSURE_RESERVATION")
    prospective_loss = order_risk.reserved_maximum_loss if own_reservations else (
        order_risk.reserved_maximum_loss + plan.maximum_loss
    )
    if prospective_loss > risk_input.risk_policy.max_total_reserved_loss:
        reasons.append("RISK_TOTAL_RESERVATION_LIMIT")
    quote = market.underlying_quotes.get(plan.underlying)
    if quote is None or now - quote.event_time > timedelta(seconds=risk_input.risk_policy.quote_ttl_seconds):
        reasons.append("RISK_STALE_QUOTE")
    approved = not reasons
    reservation_id = None
    if approved:
        reservation_id = f"reservation-{plan.plan_hash.removeprefix('sha256:')[:24]}"
        reasons = ["RISK_APPROVED"]
    return RiskDecisionV1(
        decision_id=f"risk-{plan.plan_hash.removeprefix('sha256:')[:24]}",
        plan_hash=plan.plan_hash,
        risk_input_hash=risk_input.risk_input_hash,
        approved=approved,
        reason_codes=tuple(reasons),
        maximum_loss=plan.maximum_loss,
        expires_at=now + timedelta(seconds=risk_input.risk_policy.approval_ttl_seconds),
        reservation_id=reservation_id,
    )
