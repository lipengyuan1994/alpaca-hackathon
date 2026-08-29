from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from apps.decision_worker.main import (
    COMPETITION_ENTRY_CUTOFF,
    COMPETITION_FLAT_DEADLINE,
    COMPETITION_FLATTEN_AT,
    FIXTURE_TIME,
    run_approved_fixture,
)
from apps.execution_worker.main import ExecutionWorker, process_reduce_only_command
from packages.contracts.models import (
    BrokerEventV1,
    ControlCommandV1,
    ControlStateV1,
    ExecutionBundleV1,
    ExecutionDeploymentV1,
    ManagedPositionV1,
    OperatingModeV1,
    OrderRiskSnapshotV1,
    PositionLegV1,
    PositionMarketStateV1,
    PositionPolicyIdV1,
    PositionSnapshotV1,
)
from packages.domain import reconciliation_hash
from packages.execution_core import FakeBroker
from packages.position_manager import (
    authorize_reduce_only,
    build_execute_reduce_command,
    build_reduce_only_plan,
    evaluate_position,
    produce_position_exit,
    reduce_only_violations,
)


def _deployment(decision) -> ExecutionDeploymentV1:
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


def _reduce_bundle():
    decision = run_approved_fixture()
    entry_plan = decision.command.plan
    positions = PositionSnapshotV1(
        snapshot_id="positions-open-2",
        account_id=decision.account.account_id,
        version=decision.control_state.version + 1,
        as_of=FIXTURE_TIME,
        legs=tuple(
            PositionLegV1(
                symbol=leg.symbol,
                quantity=entry_plan.quantity if leg.side == "BUY" else -entry_plan.quantity,
            )
            for leg in entry_plan.legs
        ),
    )
    control = ControlStateV1(
        account_id=decision.account.account_id,
        version=2,
        mode=decision.control_state.mode,
        release_hash=decision.control_state.release_hash,
        config_hash=decision.control_state.config_hash,
        account_allowlist_hash=decision.control_state.account_allowlist_hash,
        reconciliation_hash=reconciliation_hash(decision.account, positions, decision.order_risk),
        reconciled_at=FIXTURE_TIME,
    )
    managed = ManagedPositionV1(
        strategy_position_id="position-regime-1",
        account_id=decision.account.account_id,
        underlying=entry_plan.underlying,
        direction="BULLISH",
        opened_at=FIXTURE_TIME - timedelta(minutes=30),
        current_quantity=entry_plan.quantity,
        position_policy_id=PositionPolicyIdV1.TREND_VWAP_OR_60M_V1,
        entry_plan=entry_plan,
    )
    state = PositionMarketStateV1(
        as_of=FIXTURE_TIME,
        underlying_price="599",
        session_vwap="600",
        competition_flatten_at=FIXTURE_TIME + timedelta(hours=5),
    )
    directive = evaluate_position(managed, state, now=FIXTURE_TIME)
    plan = build_reduce_only_plan(
        directive,
        managed,
        decision.market,
        decision.account,
        positions,
        control,
        now=FIXTURE_TIME,
        quote_ttl_seconds=30,
    )
    approval = authorize_reduce_only(plan, managed, positions, control, now=FIXTURE_TIME)
    command = build_execute_reduce_command(plan, approval, control)
    bundle = ExecutionBundleV1(
        bundle_id="bundle-reduce-position-regime-1",
        command=command,
        market=decision.market,
        account=decision.account,
        positions=positions,
        order_risk=decision.order_risk,
        control_state=control,
        managed_position=managed,
    )
    return decision, directive, bundle


def test_central_vwap_exit_builds_and_executes_exact_reduce_only_plan() -> None:
    decision, directive, bundle = _reduce_bundle()
    assert directive.action == "CLOSE"
    assert directive.reason_codes == ("CENTRAL_ADVERSE_VWAP_CROSS",)
    assert bundle.managed_position is not None
    assert not reduce_only_violations(bundle.command.plan, bundle.managed_position, bundle.positions)
    for entry, close in zip(bundle.managed_position.entry_plan.legs, bundle.command.plan.legs, strict=True):
        assert close.side == ("SELL" if entry.side == "BUY" else "BUY")

    broker = FakeBroker()
    result = process_reduce_only_command(
        bundle,
        broker=broker,
        now=FIXTURE_TIME,
        deployment=_deployment(decision),
        quote_ttl_seconds=30,
    )
    assert result.preflight.allowed
    assert result.broker_event is not None
    assert broker.submit_count == 1


def test_reduce_only_authorization_rejects_leg_that_increases_exposure() -> None:
    _, _, bundle = _reduce_bundle()
    managed = bundle.managed_position
    assert managed is not None
    first = bundle.command.plan.legs[0]
    tampered_leg = first.model_copy(update={"side": "BUY" if first.side == "SELL" else "SELL"})
    tampered = bundle.command.plan.model_copy(update={"legs": (tampered_leg, *bundle.command.plan.legs[1:])})
    decision = authorize_reduce_only(
        tampered,
        managed,
        bundle.positions,
        bundle.control_state,
        now=FIXTURE_TIME,
    )
    assert not decision.allowed
    assert "REDUCE_ONLY_LEG_NOT_EXACT_REVERSE" in decision.reason_codes


def test_execution_preflight_recomputes_reduce_only_close_price() -> None:
    decision, _, bundle = _reduce_bundle()
    tampered_plan = bundle.command.plan.model_copy(
        update={"limit_price": bundle.command.plan.limit_price + 1}
    )
    tampered_command = bundle.command.model_copy(update={"plan": tampered_plan})
    tampered_bundle = bundle.model_copy(update={"command": tampered_command})
    broker = FakeBroker()

    result = process_reduce_only_command(
        tampered_bundle,
        broker=broker,
        now=FIXTURE_TIME,
        deployment=_deployment(decision),
        quote_ttl_seconds=30,
    )

    assert not result.preflight.allowed
    assert "PREFLIGHT_REDUCE_ONLY_PRICE_MISMATCH" in result.preflight.reason_codes
    assert broker.submit_count == 0


def test_competition_flatten_overrides_normal_hold() -> None:
    _, _, bundle = _reduce_bundle()
    managed = bundle.managed_position
    assert managed is not None
    state = PositionMarketStateV1(
        as_of=FIXTURE_TIME,
        underlying_price="601",
        session_vwap="600",
        competition_flatten_at=FIXTURE_TIME,
    )
    directive = evaluate_position(managed, state, now=FIXTURE_TIME)
    assert directive.action == "CLOSE"
    assert directive.urgency == "RISK_EXIT"
    assert directive.reason_codes == ("COMPETITION_FINAL_FLATTEN",)


def test_memory_outbox_worker_reconciles_until_terminal_without_resubmission() -> None:
    decision = run_approved_fixture()
    broker = FakeBroker()
    worker = ExecutionWorker(
        ledger=decision.ledger,
        broker=broker,
        deployment=_deployment(decision),
        worker_id="worker-a",
        quote_ttl_seconds=30,
    )
    first = worker.process_once(now=FIXTURE_TIME)
    second = worker.process_once(now=FIXTURE_TIME + timedelta(seconds=5))
    broker.set_outcome(decision.command.plan.client_order_id, "FILLED")
    third = worker.process_once(now=FIXTURE_TIME + timedelta(seconds=10))
    fourth = worker.process_once(now=FIXTURE_TIME + timedelta(seconds=15))
    assert first.status == "AWAITING_TERMINAL"
    assert second.status == "AWAITING_TERMINAL"
    assert third.status == "RECONCILED"
    assert third.broker_event is not None
    assert third.broker_event.status == "FILLED"
    assert fourth.status == "IDLE"
    assert broker.submit_count == 1
    assert len(decision.ledger.broker_events) == 3


def test_filled_entry_is_projected_and_central_flatten_closes_it() -> None:
    decision = run_approved_fixture()
    broker = FakeBroker()
    worker = ExecutionWorker(
        ledger=decision.ledger,
        broker=broker,
        deployment=_deployment(decision),
        worker_id="worker-a",
        quote_ttl_seconds=30,
    )
    submitted = worker.process_once(now=FIXTURE_TIME)
    assert submitted.status == "AWAITING_TERMINAL"
    broker.set_outcome(decision.command.plan.client_order_id, "FILLED")
    filled_at = FIXTURE_TIME + timedelta(seconds=5)
    terminal = worker.process_once(now=filled_at)
    assert terminal.status == "RECONCILED"

    active = decision.ledger.active_managed_positions(decision.account.account_id)
    assert len(active) == 1
    managed = active[0]
    assert managed.position_policy_id == decision.command.plan.position_policy_id
    positions = PositionSnapshotV1(
        snapshot_id="positions-after-entry-fill",
        account_id=decision.account.account_id,
        version=decision.positions.version + 1,
        as_of=filled_at,
        legs=tuple(
            PositionLegV1(
                symbol=leg.symbol,
                quantity=managed.current_quantity if leg.side == "BUY" else -managed.current_quantity,
            )
            for leg in managed.entry_plan.legs
        ),
    )
    order_risk = OrderRiskSnapshotV1(
        snapshot_id="order-risk-after-entry-fill",
        account_id=decision.account.account_id,
        version=decision.order_risk.version + 1,
        as_of=filled_at,
    )
    control = decision.ledger.refresh_reconciliation(
        decision.account,
        positions,
        order_risk,
        now=filled_at,
    )
    state = PositionMarketStateV1(
        as_of=filled_at,
        underlying_price="601",
        session_vwap="600",
        competition_flatten_at=filled_at,
    )
    produced = produce_position_exit(
        ledger=decision.ledger,
        managed=managed,
        state=state,
        market=decision.market,
        account=decision.account,
        positions=positions,
        order_risk=order_risk,
        control=control,
        now=filled_at,
        quote_ttl_seconds=30,
        flat_deadline_at=filled_at + timedelta(minutes=15),
    )
    assert produced.status == "EXIT_ENQUEUED"
    assert produced.directive.reason_codes == ("COMPETITION_FINAL_FLATTEN",)

    close_submitted = worker.process_once(now=filled_at)
    assert close_submitted.status == "AWAITING_TERMINAL"
    close_id = decision.ledger.outbox[-1].command.plan.client_order_id
    broker.set_outcome(close_id, "FILLED")
    close_terminal = worker.process_once(now=filled_at + timedelta(seconds=5))
    assert close_terminal.status == "RECONCILED"
    assert decision.ledger.active_managed_positions(decision.account.account_id) == ()


def test_flatten_time_cancels_an_accepted_entry_instead_of_leaving_it_working() -> None:
    decision = run_approved_fixture()
    broker = FakeBroker()
    worker = ExecutionWorker(
        ledger=decision.ledger,
        broker=broker,
        deployment=_deployment(decision),
        worker_id="worker-a",
        quote_ttl_seconds=30,
    )
    assert worker.process_once(now=FIXTURE_TIME).status == "AWAITING_TERMINAL"

    cancelled = worker.process_once(now=COMPETITION_FLATTEN_AT)

    assert cancelled.status == "ENTRY_CANCELLED_FOR_FLATTEN"
    assert cancelled.broker_event is not None
    assert cancelled.broker_event.status == "CANCELLED"
    assert broker.submit_count == 1
    assert decision.ledger.active_managed_positions(decision.account.account_id) == ()


def test_unknown_submission_reconciles_before_any_retry() -> None:
    class UnknownBroker(FakeBroker):
        def submit(self, plan, *, now):
            return super().submit(plan, now=now, outcome="UNKNOWN")

    decision = run_approved_fixture()
    broker = UnknownBroker()
    worker = ExecutionWorker(
        ledger=decision.ledger,
        broker=broker,
        deployment=_deployment(decision),
        worker_id="worker-a",
        quote_ttl_seconds=30,
    )
    first = worker.process_once(now=FIXTURE_TIME)
    second = worker.process_once(now=FIXTURE_TIME + timedelta(seconds=5))
    assert first.status == "RECONCILIATION_REQUIRED"
    assert second.status == "RECONCILIATION_REQUIRED"
    assert broker.submit_count == 1


def test_unknown_submission_that_remains_not_found_never_resubmits() -> None:
    class InvisibleUnknownBroker(FakeBroker):
        def submit(self, plan, *, now):
            self.submit_count += 1
            return BrokerEventV1(
                client_order_id=plan.client_order_id,
                status="UNKNOWN",
                occurred_at=now,
                reason_code="AMBIGUOUS_WITHOUT_VISIBLE_ORDER",
            )

    decision = run_approved_fixture()
    broker = InvisibleUnknownBroker()
    worker = ExecutionWorker(
        ledger=decision.ledger,
        broker=broker,
        deployment=_deployment(decision),
        worker_id="worker-a",
        quote_ttl_seconds=30,
    )
    first = worker.process_once(now=FIXTURE_TIME)
    second = worker.process_once(now=FIXTURE_TIME + timedelta(seconds=5))
    third = worker.process_once(now=FIXTURE_TIME + timedelta(seconds=10))
    assert first.status == "RECONCILIATION_REQUIRED"
    assert second.status == "RECONCILIATION_REQUIRED"
    assert third.status == "RECONCILIATION_REQUIRED"
    assert broker.submit_count == 1


def test_worker_loads_current_halt_and_invalidates_queued_entry() -> None:
    decision = run_approved_fixture()
    prior = decision.control_state
    flatten = ControlCommandV1(
        nonce=uuid4(),
        issued_at=FIXTURE_TIME,
        expires_at=FIXTURE_TIME + timedelta(seconds=30),
        operator_id="risk-operator",
        expected_mode=prior.mode,
        expected_version=prior.version,
        target_mode=OperatingModeV1.FLATTENING,
        account_id=prior.account_id,
        release_hash=prior.release_hash,
        config_hash=prior.config_hash,
        account_allowlist_hash=prior.account_allowlist_hash,
        reconciliation_hash=prior.reconciliation_hash,
        reason_code="RISK_OPERATOR_FLATTEN",
    )
    decision.ledger.apply_control_command(
        flatten,
        now=FIXTURE_TIME,
        account_is_flat=False,
        no_working_or_unknown_orders=False,
    )
    broker = FakeBroker()
    worker = ExecutionWorker(
        ledger=decision.ledger,
        broker=broker,
        deployment=_deployment(decision),
        worker_id="worker-a",
        quote_ttl_seconds=30,
    )

    outcome = worker.process_once(now=FIXTURE_TIME)

    assert outcome.status == "PREFLIGHT_REJECTED"
    assert broker.submit_count == 0


def test_execution_bundle_rejects_cross_account_position_snapshot() -> None:
    decision = run_approved_fixture()
    foreign = decision.positions.model_copy(
        update={"account_id": "other-paper-account", "content_hash": None}
    )
    payload = decision.ledger.outbox[0].bundle.model_dump(exclude={"content_hash"})
    payload["positions"] = foreign.model_dump(mode="json")

    with pytest.raises(ValidationError, match="position account mismatch"):
        ExecutionBundleV1.model_validate(payload)
