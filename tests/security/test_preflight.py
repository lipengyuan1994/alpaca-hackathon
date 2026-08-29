from __future__ import annotations

from apps.decision_worker.main import FIXTURE_TIME, run_approved_fixture
from apps.execution_worker.main import process_approved_command
from packages.execution_core import FakeBroker


def test_preflight_rejects_non_paper_host_without_broker_call() -> None:
    decision = run_approved_fixture()
    broker = FakeBroker()
    outcome = process_approved_command(
        decision.command,
        decision.risk_input,
        decision.market,
        decision.account,
        decision.positions,
        decision.order_risk,
        broker=broker,
        now=FIXTURE_TIME,
        paper_hostname="https://unsafe.example.test",
        expected_account_id="paper-fixture-account",
    )
    assert not outcome.preflight.allowed
    assert "PREFLIGHT_NON_PAPER_HOST" in outcome.preflight.reason_codes
    assert outcome.broker_event is None
    assert broker.submit_count == 0


def test_preflight_rejects_sibling_authorization_swap_without_broker_call() -> None:
    decision = run_approved_fixture()
    altered_input = decision.risk_input.model_copy(update={"release_hash": "sha256:" + "f" * 64})
    broker = FakeBroker()
    outcome = process_approved_command(
        decision.command,
        altered_input,
        decision.market,
        decision.account,
        decision.positions,
        decision.order_risk,
        broker=broker,
        now=FIXTURE_TIME,
        paper_hostname="https://paper-api.alpaca.markets",
        expected_account_id="paper-fixture-account",
    )
    assert not outcome.preflight.allowed
    assert "PREFLIGHT_RISK_INPUT_MISMATCH" in outcome.preflight.reason_codes
    assert broker.submit_count == 0
