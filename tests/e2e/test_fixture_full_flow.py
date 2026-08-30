from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.decision_worker.main import (
    COMPETITION_ENTRY_CUTOFF,
    COMPETITION_FLAT_DEADLINE,
    COMPETITION_FLATTEN_AT,
    FIXTURE_TIME,
    run_approved_fixture,
)
from apps.execution_worker.main import ExecutionWorker
from packages.contracts.models import ExecutionDeploymentV1
from packages.execution_core import FakeBroker


def _deployment(decision) -> ExecutionDeploymentV1:
    assert decision.risk_input is not None
    assert decision.account is not None
    risk_input = decision.risk_input
    return ExecutionDeploymentV1(
        expected_account_id=decision.account.account_id,
        paper_base_url="https://paper-api.alpaca.markets",
        risk_policy_hash=risk_input.risk_policy.policy_hash,
        template_catalog_hash=risk_input.template_catalog_hash,
        strategy_registry_hash=risk_input.strategy_registry_hash,
        strategy_config_hash=risk_input.strategy_config_hash,
        strategy_content_hash=risk_input.strategy_content_hash,
        account_allowlist_hash=risk_input.account_allowlist_hash,
        release_hash=risk_input.release_hash,
        entry_cutoff_at=COMPETITION_ENTRY_CUTOFF,
        flatten_at=COMPETITION_FLATTEN_AT,
        flat_deadline_at=COMPETITION_FLAT_DEADLINE,
    )


def test_fixture_replay_covers_decision_to_fake_fill_and_public_tape() -> None:
    decision = run_approved_fixture()
    assert decision.command is not None
    assert decision.account is not None

    broker = FakeBroker()
    worker = ExecutionWorker(
        ledger=decision.ledger,
        broker=broker,
        deployment=_deployment(decision),
        worker_id="fixture-execution-worker",
        quote_ttl_seconds=30,
    )

    accepted = worker.process_once(now=FIXTURE_TIME)
    assert accepted.status == "AWAITING_TERMINAL"
    assert accepted.broker_event is not None
    assert accepted.broker_event.status == "ACCEPTED"
    assert broker.submit_count == 1

    broker.set_outcome(decision.command.plan.client_order_id, "FILLED")
    reconciled = worker.process_once(now=FIXTURE_TIME + timedelta(seconds=5))
    assert reconciled.status == "RECONCILED"
    assert reconciled.broker_event is not None
    assert reconciled.broker_event.status == "FILLED"
    assert decision.ledger.active_managed_positions(decision.account.account_id)

    response = TestClient(create_app(decision.ledger)).get(f"/v1/replay/{decision.run_id}")
    assert response.status_code == 200
    events = response.json()["events"]
    event_types = [event["event_type"] for event in events]
    assert event_types == [
        "MarketSnapshotRecordedV1",
        "FeatureVectorComputedV1",
        "StrategyDecisionProducedV1",
        "AgentThesisFrozenV1",
        "DailyEconomicContextBoundV1",
        "EconomicAssessmentFrozenV1",
        "TradeIntentResolvedV1",
        "OrderPlanCreatedV1",
        "RiskApprovedAndCapacityReservedV1",
        "BrokerEventV1",
        "BrokerEventV1",
    ]
    thesis = next(event["payload"] for event in events if event["event_type"] == "AgentThesisFrozenV1")
    assert thesis["narrative"]["market_thesis"]
    assert thesis["narrative"]["counter_thesis"]
    assert thesis["narrative"]["explanation"]
    assert [event["payload"]["status"] for event in events[-2:]] == ["ACCEPTED", "FILLED"]
    assert "secret" not in response.text.lower()
