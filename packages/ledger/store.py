"""In-memory reference ledger with the same atomic semantics as the Postgres design.

The production migration lives in ``infra/postgres``.  This adapter is intentional:
fixtures and replay require no credentials, network, or database process.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from threading import RLock
from typing import Any

from packages.contracts.models import EventEnvelopeV1, ExecuteApprovedPlanV1, RiskDecisionV1


class LedgerError(ValueError):
    pass


@dataclass(frozen=True)
class OutboxMessage:
    message_id: str
    topic: str
    command: ExecuteApprovedPlanV1


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
        account_id: str,
        approval: RiskDecisionV1,
        command: ExecuteApprovedPlanV1,
        event: EventEnvelopeV1,
    ) -> OutboxMessage:
        """Atomically record a reservation and publish one immutable command."""
        if not approval.approved:
            raise LedgerError("LEDGER_REJECTED_APPROVAL_NOT_ENQUEUED")
        if command.approval.decision_hash != approval.decision_hash:
            raise LedgerError("LEDGER_APPROVAL_COMMAND_MISMATCH")
        with self._lock:
            intent_key = (command.plan.intent_id, command.plan.plan_hash)
            client_key = (account_id, command.plan.client_order_id)
            if intent_key in self._intent_plans or client_key in self._client_orders:
                raise LedgerError("LEDGER_DUPLICATE_PLAN_OR_CLIENT_ORDER")
            expected = self._aggregate_versions[event.aggregate_id] + 1
            if event.aggregate_version != expected:
                raise LedgerError("LEDGER_AGGREGATE_VERSION_CONFLICT")
            self._intent_plans.add(intent_key)
            self._client_orders.add(client_key)
            self._aggregate_versions[event.aggregate_id] = event.aggregate_version
            self.events.append(event)
            message = OutboxMessage(
                message_id=f"outbox-{command.command_hash.removeprefix('sha256:')[:24]}",
                topic="execute-approved-plan/v1",
                command=command,
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
