"""In-memory reference ledger with the same atomic semantics as the Postgres design.

The production migration lives in ``infra/postgres``.  This adapter is intentional:
fixtures and replay require no credentials, network, or database process.
"""

from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any, Iterator

from packages.contracts.models import (
    AccountSnapshotV1,
    BrokerEventV1,
    ControlCommandV1,
    ControlStateV1,
    EventEnvelopeV1,
    ExecuteApprovedPlanV1,
    ExecuteReduceOnlyPlanV1,
    ExecutionBundleV1,
    ManagedPositionV1,
    OrderRiskSnapshotV1,
    PositionSnapshotV1,
)
from packages.domain import (
    apply_control_command as transition_control_state,
)
from packages.domain import (
    reconciliation_hash,
)


class LedgerError(ValueError):
    pass


@dataclass(frozen=True)
class OutboxMessage:
    message_id: str
    topic: str
    command: ExecuteApprovedPlanV1 | ExecuteReduceOnlyPlanV1
    bundle: ExecutionBundleV1 | None = None


@dataclass(frozen=True)
class ClaimedOutbox:
    message: OutboxMessage
    worker_id: str
    lease_until: datetime
    last_error: str | None = None
    submission_state: str = "READY"


class MemoryLedger:
    """Thread-safe reference of append/CAS/outbox/inbox operations."""

    def __init__(self) -> None:
        self._lock = RLock()
        self.events: list[EventEnvelopeV1] = []
        self.outbox: list[OutboxMessage] = []
        self._inbox: set[str] = set()
        self._aggregate_versions: dict[str, int] = defaultdict(int)
        self._intent_plans: set[tuple[str, str]] = set()
        self._client_orders: set[tuple[str, str]] = set()
        self._order_risk_snapshots: dict[str, OrderRiskSnapshotV1] = {}
        self._outbox_claims: dict[str, ClaimedOutbox] = {}
        self._outbox_completed: set[str] = set()
        self._outbox_errors: dict[str, str] = {}
        self._submission_states: dict[str, str] = {}
        self._outbox_next_attempt: dict[str, datetime] = {}
        self._account_leases: dict[str, tuple[str, datetime]] = {}
        self._control_states: dict[str, ControlStateV1] = {}
        self._control_nonces: set[str] = set()
        self._managed_positions: dict[str, ManagedPositionV1] = {}
        self.broker_events: list[BrokerEventV1] = []

    def append(self, event: EventEnvelopeV1) -> EventEnvelopeV1:
        with self._lock:
            expected = self._aggregate_versions[event.aggregate_id] + 1
            if event.aggregate_version != expected:
                raise LedgerError("LEDGER_AGGREGATE_VERSION_CONFLICT")
            self._aggregate_versions[event.aggregate_id] = event.aggregate_version
            self.events.append(event)
            return event

    def reserve_and_enqueue(
        self,
        *,
        bundle: ExecutionBundleV1,
        prospective_order_risk: OrderRiskSnapshotV1,
        expected_prior_order_risk_version: int,
        expected_prior_control_state: ControlStateV1,
        event: EventEnvelopeV1,
    ) -> OutboxMessage:
        """Atomically record a reservation and publish one immutable command."""
        if not isinstance(bundle.command, ExecuteApprovedPlanV1):
            raise LedgerError("LEDGER_APPROVED_ENTRY_COMMAND_REQUIRED")
        command = bundle.command
        approval = command.approval
        account_id = command.plan.account_id
        if not approval.approved:
            raise LedgerError("LEDGER_REJECTED_APPROVAL_NOT_ENQUEUED")
        with self._lock:
            self._validate_reservation_control_transition(
                expected_prior_control_state,
                bundle.control_state,
                bundle,
                prospective_order_risk,
            )
            intent_key = (command.plan.intent_id, command.plan.plan_hash)
            client_key = (account_id, command.plan.client_order_id)
            if intent_key in self._intent_plans or client_key in self._client_orders:
                raise LedgerError("LEDGER_DUPLICATE_PLAN_OR_CLIENT_ORDER")
            expected = self._aggregate_versions[event.aggregate_id] + 1
            if event.aggregate_version != expected:
                raise LedgerError("LEDGER_AGGREGATE_VERSION_CONFLICT")
            if prospective_order_risk.account_id != account_id:
                raise LedgerError("LEDGER_ORDER_RISK_ACCOUNT_MISMATCH")
            if prospective_order_risk.version != command.order_risk_snapshot_version:
                raise LedgerError("LEDGER_ORDER_RISK_COMMAND_VERSION_MISMATCH")
            prior = self._order_risk_snapshots.get(account_id)
            if prior is None:
                raise LedgerError("LEDGER_ORDER_RISK_STATE_UNAVAILABLE")
            actual_prior_version = prior.version
            if actual_prior_version != expected_prior_order_risk_version:
                raise LedgerError("LEDGER_ORDER_RISK_CAS_CONFLICT")
            own = [
                item
                for item in prospective_order_risk.reservations
                if item.plan_hash == command.plan.plan_hash
            ]
            if (
                len(own) != 1
                or own[0].maximum_loss != command.plan.maximum_loss
                or own[0].reservation_id != approval.reservation_id
                or own[0].status != "APPROVED"
                or own[0].expires_at != approval.expires_at
            ):
                raise LedgerError("LEDGER_RESERVATION_NOT_PERSISTED")
            self._intent_plans.add(intent_key)
            self._client_orders.add(client_key)
            self._order_risk_snapshots[account_id] = prospective_order_risk
            self._aggregate_versions[event.aggregate_id] = event.aggregate_version
            self._control_states[account_id] = bundle.control_state
            self.events.append(event)
            message = OutboxMessage(
                message_id=f"outbox-{command.command_hash.removeprefix('sha256:')[:24]}",
                topic="execute-approved-plan/v1",
                command=command,
                bundle=bundle,
            )
            self.outbox.append(message)
            return message

    def enqueue_reduce_only(
        self,
        *,
        bundle: ExecutionBundleV1,
        event: EventEnvelopeV1,
    ) -> OutboxMessage:
        """Atomically enqueue one centrally authorized reduce-only command."""
        if not isinstance(bundle.command, ExecuteReduceOnlyPlanV1):
            raise LedgerError("LEDGER_REDUCE_ONLY_COMMAND_REQUIRED")
        command = bundle.command
        with self._lock:
            self._validate_control_state(bundle.control_state)
            managed = bundle.managed_position
            if managed is None:
                raise LedgerError("LEDGER_MANAGED_POSITION_REQUIRED")
            current_managed = self._managed_positions.get(managed.strategy_position_id)
            if current_managed is None or current_managed.content_hash != managed.content_hash:
                raise LedgerError("LEDGER_MANAGED_POSITION_STALE")
            if current_managed.status != "OPEN":
                raise LedgerError("LEDGER_MANAGED_POSITION_NOT_OPEN")
            client_key = (command.plan.account_id, command.plan.client_order_id)
            if client_key in self._client_orders:
                raise LedgerError("LEDGER_DUPLICATE_PLAN_OR_CLIENT_ORDER")
            expected = self._aggregate_versions[event.aggregate_id] + 1
            if event.aggregate_version != expected:
                raise LedgerError("LEDGER_AGGREGATE_VERSION_CONFLICT")
            self._client_orders.add(client_key)
            self._aggregate_versions[event.aggregate_id] = event.aggregate_version
            self._control_states.setdefault(command.plan.account_id, bundle.control_state)
            self._managed_positions[managed.strategy_position_id] = ManagedPositionV1.model_validate(
                managed.model_dump(mode="json", exclude={"content_hash"}) | {"status": "CLOSING"}
            )
            self.events.append(event)
            message = OutboxMessage(
                message_id=f"outbox-{command.command_hash.removeprefix('sha256:')[:24]}",
                topic="execute-reduce-only-plan/v1",
                command=command,
                bundle=bundle,
            )
            self.outbox.append(message)
            return message

    def claim_inbox(self, message_id: str) -> bool:
        """Return true once; duplicate at-least-once deliveries have no second effect."""
        with self._lock:
            if message_id in self._inbox:
                return False
            self._inbox.add(message_id)
            return True

    def claim_next_outbox(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int = 30,
    ) -> ClaimedOutbox | None:
        """Atomically lease one pending message; expired claims are recoverable."""
        now = now.astimezone(UTC)
        with self._lock:
            for message in sorted(
                self.outbox,
                key=lambda item: (
                    0 if item.topic == "execute-reduce-only-plan/v1" else 100,
                    item.message_id,
                ),
            ):
                if message.message_id in self._outbox_completed:
                    continue
                if self._outbox_next_attempt.get(message.message_id, now) > now:
                    continue
                claim = self._outbox_claims.get(message.message_id)
                if claim is not None and claim.lease_until > now:
                    continue
                claimed = ClaimedOutbox(
                    message=message,
                    worker_id=worker_id,
                    lease_until=now + timedelta(seconds=lease_seconds),
                    last_error=self._outbox_errors.get(message.message_id),
                    submission_state=self._submission_states.get(message.message_id, "READY"),
                )
                self._outbox_claims[message.message_id] = claimed
                return claimed
        return None

    def acquire_account_lease(
        self,
        *,
        account_id: str,
        worker_id: str,
        now: datetime,
        lease_seconds: int = 30,
    ) -> bool:
        now = now.astimezone(UTC)
        with self._lock:
            current = self._account_leases.get(account_id)
            if current is not None and current[0] != worker_id and current[1] > now:
                return False
            self._account_leases[account_id] = (worker_id, now + timedelta(seconds=lease_seconds))
            return True

    def release_account_lease(self, *, account_id: str, worker_id: str) -> None:
        with self._lock:
            current = self._account_leases.get(account_id)
            if current is not None and current[0] == worker_id:
                del self._account_leases[account_id]

    def complete_outbox(
        self,
        *,
        message_id: str,
        worker_id: str,
        broker_event: BrokerEventV1,
        bundle: ExecutionBundleV1,
    ) -> None:
        """Persist reconciliation evidence and inbox/outbox completion atomically."""
        with self._lock:
            claim = self._outbox_claims.get(message_id)
            if claim is None or claim.worker_id != worker_id:
                raise LedgerError("LEDGER_OUTBOX_CLAIM_MISMATCH")
            if message_id in self._outbox_completed:
                return
            if all(item.content_hash != broker_event.content_hash for item in self.broker_events):
                self.broker_events.append(broker_event)
            self._apply_terminal_projection(bundle, broker_event)
            self._inbox.add(message_id)
            self._outbox_completed.add(message_id)
            self._outbox_errors.pop(message_id, None)
            del self._outbox_claims[message_id]

    def _apply_terminal_projection(
        self,
        bundle: ExecutionBundleV1,
        broker_event: BrokerEventV1,
    ) -> None:
        if broker_event.status not in {"FILLED", "REJECTED", "CANCELLED", "EXPIRED"}:
            raise LedgerError("LEDGER_OUTBOX_COMPLETION_REQUIRES_TERMINAL_EVENT")
        command = bundle.command
        if isinstance(command, ExecuteApprovedPlanV1):
            if broker_event.filled_quantity > command.plan.quantity:
                raise LedgerError("LEDGER_ENTRY_FILL_QUANTITY_EXCEEDS_PLAN")
            if broker_event.status == "FILLED" and broker_event.filled_quantity != command.plan.quantity:
                raise LedgerError("LEDGER_ENTRY_FILL_QUANTITY_MISMATCH")
            if broker_event.filled_quantity == 0:
                return
            position_id = f"position-{command.plan.plan_hash.removeprefix('sha256:')[:24]}"
            direction = (
                "BULLISH"
                if command.plan.template_id in {"CALL_DEBIT_SPREAD_V1", "LONG_CALL_V1"}
                else "BEARISH"
            )
            self._managed_positions[position_id] = ManagedPositionV1(
                strategy_position_id=position_id,
                account_id=command.plan.account_id,
                underlying=command.plan.underlying,
                direction=direction,
                opened_at=broker_event.occurred_at,
                current_quantity=broker_event.filled_quantity,
                position_policy_id=command.plan.position_policy_id,
                entry_plan=command.plan,
            )
            return

        managed = bundle.managed_position
        if managed is None:
            raise LedgerError("LEDGER_MANAGED_POSITION_REQUIRED")
        if broker_event.filled_quantity > managed.current_quantity:
            raise LedgerError("LEDGER_EXIT_FILL_QUANTITY_EXCEEDS_POSITION")
        remaining = managed.current_quantity - broker_event.filled_quantity
        if broker_event.status == "FILLED" and remaining != 0:
            raise LedgerError("LEDGER_EXIT_FILL_QUANTITY_MISMATCH")
        if remaining == 0:
            self._managed_positions.pop(managed.strategy_position_id, None)
            return
        self._managed_positions[managed.strategy_position_id] = ManagedPositionV1.model_validate(
            managed.model_dump(mode="json", exclude={"content_hash"})
            | {"status": "OPEN", "current_quantity": remaining}
        )

    def active_managed_positions(self, account_id: str) -> tuple[ManagedPositionV1, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        position
                        for position in self._managed_positions.values()
                        if position.account_id == account_id
                    ),
                    key=lambda position: position.strategy_position_id,
                )
            )

    def refresh_reconciliation(
        self,
        account: AccountSnapshotV1,
        positions: PositionSnapshotV1,
        order_risk: OrderRiskSnapshotV1,
        *,
        now: datetime,
    ) -> ControlStateV1:
        """CAS broker-sanitized projections without changing control authority."""
        now = now.astimezone(UTC)
        account_id = account.account_id
        if positions.account_id != account_id or order_risk.account_id != account_id:
            raise LedgerError("LEDGER_RECONCILIATION_ACCOUNT_MISMATCH")
        if any(snapshot.as_of > now for snapshot in (account, positions, order_risk)):
            raise LedgerError("LEDGER_RECONCILIATION_FROM_FUTURE")
        with self._lock:
            current_control = self._control_states.get(account_id)
            current_risk = self._order_risk_snapshots.get(account_id)
            if current_control is None or current_risk is None:
                raise LedgerError("LEDGER_RECONCILIATION_STATE_UNAVAILABLE")
            if order_risk.version != current_risk.version + 1:
                raise LedgerError("LEDGER_RECONCILIATION_RISK_VERSION_NOT_NEXT")
            next_control = ControlStateV1(
                account_id=account_id,
                version=current_control.version + 1,
                mode=current_control.mode,
                release_hash=current_control.release_hash,
                config_hash=current_control.config_hash,
                account_allowlist_hash=current_control.account_allowlist_hash,
                reconciliation_hash=reconciliation_hash(account, positions, order_risk),
                reconciled_at=now,
            )
            self._order_risk_snapshots[account_id] = order_risk
            self._control_states[account_id] = next_control
            return next_control

    def release_outbox(
        self,
        *,
        message_id: str,
        worker_id: str,
        error: str | None = None,
        now: datetime | None = None,
        retry_after_seconds: int = 0,
    ) -> None:
        with self._lock:
            claim = self._outbox_claims.get(message_id)
            if claim is not None and claim.worker_id == worker_id:
                del self._outbox_claims[message_id]
                if error is not None:
                    self._outbox_errors[message_id] = error
                if now is not None:
                    self._outbox_next_attempt[message_id] = now.astimezone(UTC) + timedelta(
                        seconds=retry_after_seconds
                    )

    def _validate_control_state(self, control: ControlStateV1) -> None:
        current = self._control_states.get(control.account_id)
        if current is not None and (
            current.version != control.version or current.content_hash != control.content_hash
        ):
            raise LedgerError("LEDGER_CONTROL_STATE_STALE")

    def _validate_reservation_control_transition(
        self,
        prior: ControlStateV1,
        prospective: ControlStateV1,
        bundle: ExecutionBundleV1,
        order_risk: OrderRiskSnapshotV1 | None,
    ) -> None:
        current = self._control_states.get(prior.account_id)
        if current is None or current.content_hash != prior.content_hash:
            raise LedgerError("LEDGER_CONTROL_STATE_CAS_CONFLICT")
        if order_risk is None:
            raise LedgerError("LEDGER_ORDER_RISK_SNAPSHOT_REQUIRED")
        authority_fields = ("account_id", "mode", "release_hash", "config_hash", "account_allowlist_hash")
        if any(getattr(prior, field) != getattr(prospective, field) for field in authority_fields):
            raise LedgerError("LEDGER_CONTROL_AUTHORITY_CHANGED_BY_RESERVATION")
        if prospective.version != prior.version + 1:
            raise LedgerError("LEDGER_CONTROL_STATE_VERSION_NOT_NEXT")
        expected_reconciliation = reconciliation_hash(bundle.account, bundle.positions, order_risk)
        if prospective.reconciliation_hash != expected_reconciliation:
            raise LedgerError("LEDGER_PROSPECTIVE_RECONCILIATION_MISMATCH")

    def initialize_control_state(self, control: ControlStateV1) -> None:
        """Seed the in-memory fixture; production bootstrap is DISARMED V0 only."""
        with self._lock:
            if control.account_id in self._control_states:
                raise LedgerError("LEDGER_CONTROL_STATE_ALREADY_EXISTS")
            self._control_states[control.account_id] = control

    def initialize_order_risk_state(self, order_risk: OrderRiskSnapshotV1) -> None:
        with self._lock:
            if order_risk.account_id in self._order_risk_snapshots:
                raise LedgerError("LEDGER_ORDER_RISK_STATE_ALREADY_EXISTS")
            self._order_risk_snapshots[order_risk.account_id] = order_risk

    def apply_control_command(
        self,
        command: ControlCommandV1,
        *,
        now: datetime,
        account_is_flat: bool,
        no_working_or_unknown_orders: bool,
    ) -> ControlStateV1:
        """Validate nonce/CAS/transition facts and atomically persist the result."""
        with self._lock:
            current = self._control_states.get(command.account_id)
            if current is None:
                raise LedgerError("LEDGER_CONTROL_STATE_UNAVAILABLE")
            next_state = transition_control_state(
                current,
                command,
                now=now,
                used_nonces=self._control_nonces,
                account_is_flat=account_is_flat,
                no_working_or_unknown_orders=no_working_or_unknown_orders,
            )
            self._control_states[command.account_id] = next_state
            return next_state

    def current_control_state(self, account_id: str) -> ControlStateV1 | None:
        with self._lock:
            return self._control_states.get(account_id)

    @contextmanager
    def control_state_guard(self, account_id: str) -> Iterator[ControlStateV1 | None]:
        """Serialize a control transition against the final submit decision."""
        with self._lock:
            yield self._control_states.get(account_id)

    def record_broker_event(self, broker_event: BrokerEventV1) -> None:
        with self._lock:
            if all(item.content_hash != broker_event.content_hash for item in self.broker_events):
                self.broker_events.append(broker_event)

    def mark_submission_started(
        self,
        *,
        message_id: str,
        worker_id: str,
        now: datetime,
    ) -> None:
        """One-way fence: after this point every future claim is reconcile-only."""
        with self._lock:
            claim = self._outbox_claims.get(message_id)
            if claim is None or claim.worker_id != worker_id:
                raise LedgerError("LEDGER_OUTBOX_CLAIM_MISMATCH")
            if self._submission_states.get(message_id, "READY") != "READY":
                raise LedgerError("LEDGER_SUBMISSION_ALREADY_STARTED")
            self._submission_states[message_id] = "RECONCILE_ONLY"

    def outbox_completed(self, message_id: str) -> bool:
        with self._lock:
            return message_id in self._outbox_completed

    def decision_tape(self, run_id: str) -> list[dict[str, Any]]:
        return [
            {
                "event_type": event.event_type,
                "aggregate_id": event.aggregate_id,
                "aggregate_version": event.aggregate_version,
                "occurred_at": event.occurred_at,
                "payload": event.payload,
                "content_hash": event.content_hash,
            }
            for event in self.events
            if event.run_id == run_id
        ]
