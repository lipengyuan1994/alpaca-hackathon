from __future__ import annotations

import json
from contextlib import nullcontext
from datetime import UTC, datetime

from packages.contracts.canonical import canonical_hash
from packages.contracts.models import (
    BrokerEventV1,
    SignalDecisionAuditV1,
    SignalDecisionStatusV1,
    SignalPlacementStateV1,
)
from packages.ledger.postgres import PostgresRuntimeLedger


def _hash(label: str) -> str:
    return canonical_hash({"label": label})


def _enqueued_record() -> SignalDecisionAuditV1:
    return SignalDecisionAuditV1(
        record_id="signal-audit-projection",
        run_id="run-audit-projection",
        trading_date=datetime(2026, 8, 31, tzinfo=UTC).date(),
        recorded_at=datetime(2026, 8, 31, 14, 15, tzinfo=UTC),
        strategy_evaluation_hash=_hash("evaluation"),
        agent_thesis_hash=_hash("thesis"),
        trade_intent_hash=_hash("intent"),
        economic_context_hash=_hash("context"),
        economic_assessment_hash=_hash("assessment"),
        decision_status=SignalDecisionStatusV1.APPROVED_AND_ENQUEUED,
        placement_state=SignalPlacementStateV1.ENQUEUED,
        order_placed=False,
        reason_code="RISK_APPROVED",
        plan_hash=_hash("plan"),
        client_order_id="order-audit-001",
        signal_payload={"direction": "BULLISH"},
        supplemental={"economic_reason_code": "ECONOMIC_CONTEXT_SUPPORTS_SIGNAL"},
    )


class _Cursor:
    def __init__(self, row=None) -> None:
        self._row = row

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self, record: SignalDecisionAuditV1) -> None:
        self._record = record
        self.calls: list[tuple[str, tuple[object, ...] | None]] = []

    def execute(self, query, params=None):
        self.calls.append((query, params))
        if "SELECT payload FROM signal_decision_audit_v1" in query:
            return _Cursor((self._record.model_dump(mode="json"),))
        return _Cursor()

    def transaction(self):
        return nullcontext()

    def commit(self):
        pass


def test_broker_acceptance_updates_the_same_signal_audit_row_to_actually_placed() -> None:
    connection = _Connection(_enqueued_record())
    ledger = PostgresRuntimeLedger(connection)
    broker_event = BrokerEventV1(
        client_order_id="order-audit-001",
        status="ACCEPTED",
        occurred_at=datetime(2026, 8, 31, 14, 16, tzinfo=UTC),
        reason_code="ALPACA_PAPER_ORDER_STATE",
    )

    ledger._update_signal_audit_from_broker_event(broker_event)

    insert_query, insert_params = connection.calls[-1]
    assert "INSERT INTO signal_decision_audit_v1" in insert_query
    assert insert_params is not None
    assert insert_params[10] == "ACCEPTED"
    assert insert_params[11] is True
    payload = json.loads(insert_params[17])
    assert payload["placement_state"] == "ACCEPTED"
    assert payload["order_placed"] is True
    assert payload["supplemental"]["last_broker_reason_code"] == "ALPACA_PAPER_ORDER_STATE"


def test_terminal_broker_projection_is_not_rewritten_by_a_later_stale_event() -> None:
    terminal = SignalDecisionAuditV1.model_validate(
        _enqueued_record().model_dump(mode="json", exclude={"content_hash"})
        | {
            "placement_state": "FILLED",
            "order_placed": True,
        }
    )
    connection = _Connection(terminal)
    ledger = PostgresRuntimeLedger(connection)

    ledger._update_signal_audit_from_broker_event(
        BrokerEventV1(
            client_order_id="order-audit-001",
            status="ACCEPTED",
            occurred_at=datetime(2026, 8, 31, 14, 17, tzinfo=UTC),
            reason_code="STALE_ACCEPTED",
        )
    )

    assert len(connection.calls) == 1
    assert "SELECT payload FROM signal_decision_audit_v1" in connection.calls[0][0]
