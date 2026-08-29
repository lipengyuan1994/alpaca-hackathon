"""Independent execution-side replay of immutable risk and freshness invariants."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from packages.contracts.canonical import hash_without
from packages.contracts.models import (
    AccountSnapshotV1,
    BrokerEventV1,
    ExecuteApprovedPlanV1,
    ExecutionPreflightDecisionV1,
    MarketSnapshotV1,
    OperatingModeV1,
    OrderRiskSnapshotV1,
    PositionSnapshotV1,
    RiskInputV1,
)
from packages.execution_core.fake_broker import FakeBroker


@dataclass(frozen=True)
class ExecutionResult:
    preflight: ExecutionPreflightDecisionV1
    broker_event: BrokerEventV1 | None


def preflight_and_submit(
    command: ExecuteApprovedPlanV1,
    risk_input: RiskInputV1,
    market: MarketSnapshotV1,
    account: AccountSnapshotV1,
    positions: PositionSnapshotV1,
    order_risk: OrderRiskSnapshotV1,
    broker: FakeBroker,
    *,
    now: datetime,
    paper_hostname: str,
    expected_account_id: str,
) -> ExecutionResult:
    """Reject before broker I/O unless all independently recomputed bindings match."""
    now = now.astimezone(UTC)
    reasons: list[str] = []
    plan = command.plan
    if command.command_hash != hash_without(command, "command_hash"):
        reasons.append("PREFLIGHT_COMMAND_HASH_MISMATCH")
    if plan.plan_hash != hash_without(plan, "plan_hash"):
        reasons.append("PREFLIGHT_PLAN_HASH_MISMATCH")
    if risk_input.risk_input_hash != hash_without(risk_input, "risk_input_hash"):
        reasons.append("PREFLIGHT_RISK_INPUT_MISMATCH")
    if command.approval.decision_hash != hash_without(command.approval, "decision_hash"):
        reasons.append("PREFLIGHT_APPROVAL_HASH_MISMATCH")
    if not paper_hostname.startswith("https://paper-api."):
        reasons.append("PREFLIGHT_NON_PAPER_HOST")
    if account.account_id != expected_account_id or plan.account_id != expected_account_id:
        reasons.append("PREFLIGHT_ACCOUNT_MISMATCH")
    if risk_input.risk_input_hash != command.risk_input_hash:
        reasons.append("PREFLIGHT_RISK_INPUT_MISMATCH")
    if command.approval.plan_hash != plan.plan_hash or command.approval.risk_input_hash != command.risk_input_hash:
        reasons.append("PREFLIGHT_APPROVAL_BINDING_MISMATCH")
    if now > command.approval.expires_at:
        reasons.append("PREFLIGHT_APPROVAL_EXPIRED")
    if risk_input.mode not in {OperatingModeV1.PAPER_ARMED, OperatingModeV1.PAPER_DEMO_ARMED}:
        reasons.append("PREFLIGHT_MODE_NOT_ARMED")
    if command.account_snapshot_version != account.version or command.position_snapshot_version != positions.version:
        reasons.append("PREFLIGHT_STALE_RECONCILIATION_VERSION")
    if command.order_risk_snapshot_version != order_risk.version:
        reasons.append("PREFLIGHT_ORDER_RISK_VERSION")
    if command.market_snapshot_hash != market.content_hash:
        reasons.append("PREFLIGHT_MARKET_HASH_MISMATCH")
    quote = market.underlying_quotes.get(plan.underlying)
    if quote is None or now - quote.event_time > timedelta(seconds=risk_input.risk_policy.quote_ttl_seconds):
        reasons.append("PREFLIGHT_STALE_QUOTE")
    if plan.client_order_id in order_risk.working_client_order_ids:
        reasons.append("PREFLIGHT_CLIENT_ORDER_ALREADY_WORKING")
    allowed = not reasons
    preflight = ExecutionPreflightDecisionV1(
        command_hash=command.command_hash,
        allowed=allowed,
        reason_codes=tuple(reasons or ["PREFLIGHT_APPROVED"]),
        checked_at=now,
    )
    if not allowed:
        return ExecutionResult(preflight=preflight, broker_event=None)
    return ExecutionResult(preflight=preflight, broker_event=broker.submit(plan, now=now))
