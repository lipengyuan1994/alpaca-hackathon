from __future__ import annotations

from apps.decision_worker.main import (
    COMPETITION_ENTRY_CUTOFF,
    COMPETITION_FLAT_DEADLINE,
    COMPETITION_FLATTEN_AT,
    FIXTURE_TIME,
    run_approved_fixture,
)
from apps.execution_worker.main import process_approved_command
from packages.contracts.models import ExecutionDeploymentV1
from packages.execution_core import FakeBroker


def test_approved_outbox_command_has_one_fake_broker_effect() -> None:
    decision = run_approved_fixture()
    assert decision.status == "APPROVED_AND_ENQUEUED"
    assert decision.command is not None
    assert decision.risk_input is not None
    deployment = ExecutionDeploymentV1(
        expected_account_id=decision.account.account_id,
        paper_base_url="https://paper-api.alpaca.markets",
        risk_policy_hash=decision.risk_input.risk_policy.policy_hash,
        template_catalog_hash=decision.risk_input.template_catalog_hash,
        strategy_registry_hash=decision.risk_input.strategy_registry_hash,
        strategy_config_hash=decision.risk_input.strategy_config_hash,
        strategy_content_hash=decision.risk_input.strategy_content_hash,
        account_allowlist_hash=decision.risk_input.account_allowlist_hash,
        release_hash=decision.risk_input.release_hash,
        entry_cutoff_at=COMPETITION_ENTRY_CUTOFF,
        flatten_at=COMPETITION_FLATTEN_AT,
        flat_deadline_at=COMPETITION_FLAT_DEADLINE,
    )
    broker = FakeBroker()
    first = process_approved_command(
        decision.command,
        decision.risk_input,
        decision.market,
        decision.account,
        decision.positions,
        decision.order_risk,
        decision.control_state,
        broker=broker,
        now=FIXTURE_TIME,
        deployment=deployment,
    )
    second = process_approved_command(
        decision.command,
        decision.risk_input,
        decision.market,
        decision.account,
        decision.positions,
        decision.order_risk,
        decision.control_state,
        broker=broker,
        now=FIXTURE_TIME,
        deployment=deployment,
    )
    assert first.preflight.allowed and first.broker_event is not None
    assert second.preflight.allowed and second.broker_event is not None
    assert broker.submit_count == 1
    assert first.broker_event.client_order_id == second.broker_event.client_order_id
