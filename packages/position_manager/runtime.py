"""Durable producer that turns central exit policy decisions into outbox work."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from packages.contracts.canonical import canonical_hash
from packages.contracts.models import (
    AccountSnapshotV1,
    ControlStateV1,
    EventEnvelopeV1,
    ExecutionBundleV1,
    ManagedPositionV1,
    MarketSnapshotV1,
    OrderRiskSnapshotV1,
    PositionDirectiveV1,
    PositionMarketStateV1,
    PositionSnapshotV1,
)

from .manager import (
    authorize_reduce_only,
    build_execute_reduce_command,
    build_reduce_only_plan,
    evaluate_position,
)


@dataclass(frozen=True)
class ExitProductionResult:
    status: str
    directive: PositionDirectiveV1
    message_id: str | None
    flat_deadline_breached: bool


def produce_position_exit(
    *,
    ledger: object,
    managed: ManagedPositionV1,
    state: PositionMarketStateV1,
    market: MarketSnapshotV1,
    account: AccountSnapshotV1,
    positions: PositionSnapshotV1,
    order_risk: OrderRiskSnapshotV1,
    control: ControlStateV1,
    now: datetime,
    quote_ttl_seconds: int,
    flat_deadline_at: datetime,
) -> ExitProductionResult:
    """Evaluate one position and atomically enqueue an exact reduce-only close."""
    now = now.astimezone(UTC)
    flat_deadline_at = flat_deadline_at.astimezone(UTC)
    deadline_breached = now >= flat_deadline_at
    directive = evaluate_position(managed, state, now=now)
    if directive.action == "HOLD":
        return ExitProductionResult(
            status="FLAT_DEADLINE_BREACH" if deadline_breached else "HOLD",
            directive=directive,
            message_id=None,
            flat_deadline_breached=deadline_breached,
        )

    plan = build_reduce_only_plan(
        directive,
        managed,
        market,
        account,
        positions,
        control,
        now=now,
        quote_ttl_seconds=quote_ttl_seconds,
    )
    approval = authorize_reduce_only(plan, managed, positions, control, now=now)
    if not approval.allowed:
        return ExitProductionResult(
            status="REDUCE_ONLY_REJECTED",
            directive=directive,
            message_id=None,
            flat_deadline_breached=deadline_breached,
        )
    command = build_execute_reduce_command(plan, approval, control)
    bundle = ExecutionBundleV1(
        bundle_id=f"bundle-{command.command_hash.removeprefix('sha256:')[:24]}",
        command=command,
        market=market,
        account=account,
        positions=positions,
        order_risk=order_risk,
        control_state=control,
        managed_position=managed,
    )
    run_id = f"position-cycle-{plan.plan_hash.removeprefix('sha256:')[:24]}"
    event_id = f"event-{canonical_hash([run_id, 'ReduceOnlyApprovedV1']).removeprefix('sha256:')[:24]}"
    event = EventEnvelopeV1(
        event_id=event_id,
        event_type="ReduceOnlyApprovedV1",
        aggregate_id=f"exit-{plan.plan_hash}",
        aggregate_version=1,
        occurred_at=now,
        received_at=now,
        producer="position-manager",
        run_id=run_id,
        correlation_id=managed.strategy_position_id,
        payload=approval.model_dump(mode="json"),
    )
    message_id = ledger.enqueue_reduce_only(bundle=bundle, event=event)  # type: ignore[attr-defined]
    if not isinstance(message_id, str):
        message_id = message_id.message_id
    return ExitProductionResult(
        status="FLAT_DEADLINE_BREACH_EXIT_ENQUEUED" if deadline_breached else "EXIT_ENQUEUED",
        directive=directive,
        message_id=message_id,
        flat_deadline_breached=deadline_breached,
    )
