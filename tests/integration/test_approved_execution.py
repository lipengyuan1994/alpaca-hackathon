from __future__ import annotations

from apps.decision_worker.main import FIXTURE_TIME, run_approved_fixture
from apps.execution_worker.main import process_approved_command
from packages.execution_core import FakeBroker


def test_approved_outbox_command_has_one_fake_broker_effect() -> None:
    decision = run_approved_fixture()
    assert decision.status == "APPROVED_AND_ENQUEUED"
    assert decision.command is not None
    assert decision.risk_input is not None
    broker = FakeBroker()
    first = process_approved_command(
        decision.command,
        decision.risk_input,
        decision.market,
        decision.account,
        decision.positions,
        decision.order_risk,
        broker=broker,
        now=FIXTURE_TIME,
        paper_hostname="https://paper-api.alpaca.markets",
        expected_account_id="paper-fixture-account",
    )
    second = process_approved_command(
        decision.command,
        decision.risk_input,
        decision.market,
        decision.account,
        decision.positions,
        decision.order_risk,
        broker=broker,
        now=FIXTURE_TIME,
        paper_hostname="https://paper-api.alpaca.markets",
        expected_account_id="paper-fixture-account",
    )
    assert first.preflight.allowed and first.broker_event is not None
    assert second.preflight.allowed and second.broker_event is not None
    assert broker.submit_count == 1
    assert first.broker_event.client_order_id == second.broker_event.client_order_id
