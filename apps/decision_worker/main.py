"""Fixture-driven decision worker that demonstrates both refusal and approved paths."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from apps.common import assert_native_developer_runtime
from packages.agent import fixture_thesis
from packages.contracts.canonical import canonical_hash
from packages.contracts.models import (
    AccountSnapshotV1,
    ControlStateV1,
    EventEnvelopeV1,
    ExecuteApprovedPlanV1,
    ExecutionBundleV1,
    FeedIdentityV1,
    MarketClockV1,
    MarketSnapshotV1,
    OperatingModeV1,
    OptionContractV1,
    OrderRiskSnapshotV1,
    PositionSnapshotV1,
    QuoteV1,
    RiskInputV1,
    RiskReservationV1,
    StrategyConfigV1,
    StrategyContextV1,
    StrategyStateV1,
)
from packages.decision_core.registry import default_registry
from packages.decision_core.resolver import NoTradeRecordedV1, resolve
from packages.domain import reconciliation_hash
from packages.ledger import MemoryLedger
from packages.market_data import compute_feature_vector, load_feature_contract
from packages.order_planner import build_plan, template_catalog_hash
from packages.risk_kernel import default_policy, evaluate_risk
from packages.strategy_runner import PluginAuthorization, run_plugin

FIXTURE_TIME = datetime(2026, 8, 31, 14, 15, tzinfo=UTC)
RELEASE_HASH = canonical_hash({"release": "fixture-v1"})
ALLOWLIST_HASH = canonical_hash({"accounts": ["paper-fixture-account"]})
COMPETITION_ENTRY_CUTOFF = datetime(2026, 9, 3, 17, 30, tzinfo=UTC)
COMPETITION_FLATTEN_AT = datetime(2026, 9, 3, 19, 15, tzinfo=UTC)
COMPETITION_FLAT_DEADLINE = datetime(2026, 9, 3, 19, 30, tzinfo=UTC)


@dataclass(frozen=True)
class RunResult:
    status: str
    run_id: str
    ledger: MemoryLedger
    tape: list[dict[str, Any]]
    details: dict[str, Any]
    command: ExecuteApprovedPlanV1 | None = None
    risk_input: RiskInputV1 | None = None
    market: MarketSnapshotV1 | None = None
    account: AccountSnapshotV1 | None = None
    positions: PositionSnapshotV1 | None = None
    order_risk: OrderRiskSnapshotV1 | None = None
    control_state: ControlStateV1 | None = None


def _event(
    *,
    event_type: str,
    aggregate_id: str,
    version: int,
    payload: Any,
    run_id: str,
) -> EventEnvelopeV1:
    event_id = f"event-{canonical_hash([run_id, event_type, aggregate_id, version]).removeprefix('sha256:')[:24]}"
    return EventEnvelopeV1(
        event_id=event_id,
        event_type=event_type,
        aggregate_id=aggregate_id,
        aggregate_version=version,
        occurred_at=FIXTURE_TIME,
        received_at=FIXTURE_TIME,
        producer="decision-worker",
        run_id=run_id,
        correlation_id=run_id,
        payload=payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload,
    )


def _append_decision_trace(
    ledger: MemoryLedger,
    *,
    run_id: str,
    market: MarketSnapshotV1,
    context: StrategyContextV1,
    evaluation: Any,
    thesis: Any,
    outcome: Any,
) -> int:
    """Persist the public, replayable part of a decision before any order exists.

    The tape carries only normalized/frozen artifacts.  In particular, it has
    no broker client, credential, account-secret, or mutable model response.
    """
    records = (
        ("MarketSnapshotRecordedV1", market),
        (
            "FeatureVectorComputedV1",
            {
                "feature_vector_id": context.feature_vector_id,
                "feature_vector_hash": context.feature_vector_hash,
                "feature_contract_hash": context.feature_contract_hash,
                "available_time": context.feature_available_time,
                "values": context.universe_features,
            },
        ),
        ("StrategyDecisionProducedV1", evaluation),
        ("AgentThesisFrozenV1", thesis),
        (
            "NoTradeRecordedV1" if isinstance(outcome, NoTradeRecordedV1) else "TradeIntentResolvedV1",
            outcome.__dict__ if isinstance(outcome, NoTradeRecordedV1) else outcome,
        ),
    )
    for version, (event_type, payload) in enumerate(records, start=1):
        ledger.append(
            _event(
                event_type=event_type,
                aggregate_id=run_id,
                version=version,
                payload=payload,
                run_id=run_id,
            )
        )
    return len(records)


def fixture_inputs(*, momentum: bool = False) -> tuple[
    MarketSnapshotV1,
    AccountSnapshotV1,
    PositionSnapshotV1,
    OrderRiskSnapshotV1,
    StrategyContextV1,
    StrategyConfigV1,
]:
    # This keeps the 605 call short just outside the published 1%-OTM target.
    # It is deliberately not an executable price source; all fixtures are
    # offline and frozen.
    quote = QuoteV1(bid="599", ask="599.01", event_time=FIXTURE_TIME, available_time=FIXTURE_TIME)
    market = MarketSnapshotV1(
        snapshot_id="market-fixture-1",
        as_of=FIXTURE_TIME,
        feed_identity=FeedIdentityV1(entitlement="alpaca-basic-fixture"),
        clock=MarketClockV1(
            is_open=True,
            as_of=FIXTURE_TIME,
            next_open=FIXTURE_TIME + timedelta(days=1),
            next_close=FIXTURE_TIME + timedelta(hours=6),
        ),
        underlying_quotes={"SPY": quote},
        option_contracts=(
            OptionContractV1(
                symbol="SPY260914C00600000",
                underlying="SPY",
                right="CALL",
                strike="600",
                expiration=FIXTURE_TIME + timedelta(days=14),
                quote=QuoteV1(bid="1.80", ask="2.00", event_time=FIXTURE_TIME, available_time=FIXTURE_TIME),
            ),
            OptionContractV1(
                symbol="SPY260914C00605000",
                underlying="SPY",
                right="CALL",
                strike="605",
                expiration=FIXTURE_TIME + timedelta(days=14),
                quote=QuoteV1(bid="0.75", ask="0.90", event_time=FIXTURE_TIME, available_time=FIXTURE_TIME),
            ),
        ),
    )
    account = AccountSnapshotV1(
        snapshot_id="account-fixture-1",
        account_id="paper-fixture-account",
        version=1,
        as_of=FIXTURE_TIME,
        equity="100000",
        day_start_equity="100000",
        cash="100000",
        buying_power="100000",
    )
    positions = PositionSnapshotV1(
        snapshot_id="positions-fixture-1", account_id=account.account_id, version=1, as_of=FIXTURE_TIME
    )
    order_risk = OrderRiskSnapshotV1(
        snapshot_id="order-risk-fixture-1", account_id=account.account_id, version=1, as_of=FIXTURE_TIME
    )
    config = StrategyConfigV1(values={"momentum_threshold": Decimal("1.0")})
    feature_contract = load_feature_contract()
    feature = compute_feature_vector(
        market,
        feature_id="features-fixture-1",
        calculated_at=FIXTURE_TIME - timedelta(seconds=1),
        values={
            "SPY__momentum_z": Decimal("1.2") if momentum else Decimal("0.0"),
            "QQQ__momentum_z": Decimal("0.8") if momentum else Decimal("0.0"),
        },
        contract=feature_contract,
    )
    registry = default_registry()
    context = StrategyContextV1(
        evaluation_id="evaluation-fixture-1",
        as_of=FIXTURE_TIME,
        market_snapshot_id=market.snapshot_id,
        market_snapshot_hash=market.content_hash,
        feature_vector_id=feature.feature_id,
        feature_vector_hash=feature.content_hash,
        feature_contract_hash=feature.feature_contract_hash,
        feature_available_time=feature.available_time,
        feed_identity=market.feed_identity,
        universe_features=feature.values,
        allowed_intent_tuples=registry.entry("always_no_trade", "1.0.0").allowed_intent_tuples,
        prior_state=StrategyStateV1(
            plugin_id="always_no_trade",
            plugin_version="1.0.0",
            as_of=FIXTURE_TIME,
            sequence=0,
        ),
        config_hash=config.config_hash,
    )
    return market, account, positions, order_risk, context, config


def _evaluate(plugin_id: str, *, momentum: bool) -> tuple[Any, Any, Any, Any, Any, Any, Any]:
    market, account, positions, order_risk, context, config = fixture_inputs(momentum=momentum)
    registry = default_registry()
    assert config.config_hash is not None
    entry = registry.authorize(
        plugin_id,
        "1.0.0",
        config_hash=config.config_hash,
        mode=OperatingModeV1.PAPER_DEMO_ARMED,
    )
    context = context.model_copy(
        update={
            "allowed_intent_tuples": entry.allowed_intent_tuples,
            "universe_features": {
                key: context.universe_features[key]
                for key in entry.data_requirements.required_feature_keys
            },
            "prior_state": context.prior_state.model_copy(
                update={
                    "plugin_id": entry.plugin_id,
                    "plugin_version": entry.plugin_version,
                    "state_hash": None,
                }
            ),
        }
    )
    # Derived hash must change with the registry-projected authority and state.
    context = StrategyContextV1.model_validate(context.model_dump(exclude={"context_hash"}))
    authorization = PluginAuthorization(
        registry_hash=registry.registry_hash,
        entrypoint=entry.entrypoint,
        content_hash=entry.content_hash,
        expected_metadata=entry.expected_metadata,
        expected_data_requirements=entry.data_requirements,
        config_hash=entry.config_hash,
        allowed_underlyings=entry.allowed_underlyings,
        allowed_intent_tuples=entry.allowed_intent_tuples,
    )
    evaluation = run_plugin(authorization=authorization, context=context, config=config)
    thesis = fixture_thesis(context, evaluation)
    return market, account, positions, order_risk, context, config, (evaluation, thesis)


def run_refusal_fixture() -> RunResult:
    """Produce the required complete, idempotent visible NO_TRADE decision tape."""
    run_id = "run-fixture-no-trade"
    ledger = MemoryLedger()
    market, _, _, _, context, _, pair = _evaluate("always_no_trade", momentum=False)
    evaluation, thesis = pair
    entry = default_registry().entry(evaluation.plugin_id, evaluation.plugin_version)
    outcome = resolve(
        evaluation,
        thesis,
        context,
        now=FIXTURE_TIME,
        position_policy_id=entry.position_policy_ref,
    )
    assert isinstance(outcome, NoTradeRecordedV1)
    _append_decision_trace(
        ledger,
        run_id=run_id,
        market=market,
        context=context,
        evaluation=evaluation,
        thesis=thesis,
        outcome=outcome,
    )
    return RunResult(
        status="NO_TRADE",
        run_id=run_id,
        ledger=ledger,
        tape=ledger.decision_tape(run_id),
        details={"reason_code": outcome.reason_code, "evaluation_hash": evaluation.evaluation_hash},
    )


def run_approved_fixture() -> RunResult:
    """Run the full semantic-intent → plan → approval → fake-fill fixture path."""
    run_id = "run-fixture-approved"
    ledger = MemoryLedger()
    market, account, positions, baseline_risk, context, config, pair = _evaluate("regime_momentum", momentum=True)
    evaluation, thesis = pair
    registry = default_registry()
    entry = registry.entry(evaluation.plugin_id, evaluation.plugin_version)
    intent = resolve(
        evaluation,
        thesis,
        context,
        now=FIXTURE_TIME,
        position_policy_id=entry.position_policy_ref,
    )
    if isinstance(intent, NoTradeRecordedV1):
        raise RuntimeError(f"fixture unexpectedly refused: {intent.reason_code}")
    tape_version = _append_decision_trace(
        ledger,
        run_id=run_id,
        market=market,
        context=context,
        evaluation=evaluation,
        thesis=thesis,
        outcome=intent,
    )
    plan = build_plan(intent, market, account, positions, baseline_risk, now=FIXTURE_TIME)
    tape_version += 1
    ledger.append(
        _event(
            event_type="OrderPlanCreatedV1",
            aggregate_id=run_id,
            version=tape_version,
            payload=plan,
            run_id=run_id,
        )
    )
    policy = default_policy()
    prospective_risk = OrderRiskSnapshotV1(
        snapshot_id="order-risk-fixture-2",
        account_id=account.account_id,
        version=baseline_risk.version + 1,
        as_of=FIXTURE_TIME,
        reservations=(
            RiskReservationV1(
                reservation_id=f"reservation-{plan.plan_hash.removeprefix('sha256:')[:24]}",
                plan_hash=plan.plan_hash,
                maximum_loss=plan.maximum_loss,
                remaining_quantity=plan.quantity,
                expires_at=FIXTURE_TIME + timedelta(seconds=policy.approval_ttl_seconds),
                status="APPROVED",
            ),
        ),
    )
    prior_control_state = ControlStateV1(
        account_id=account.account_id,
        version=1,
        mode=OperatingModeV1.PAPER_DEMO_ARMED,
        release_hash=RELEASE_HASH,
        config_hash=config.config_hash,
        account_allowlist_hash=ALLOWLIST_HASH,
        reconciliation_hash=reconciliation_hash(account, positions, baseline_risk),
        reconciled_at=FIXTURE_TIME,
    )
    control_state = ControlStateV1(
        account_id=account.account_id,
        version=prior_control_state.version + 1,
        mode=prior_control_state.mode,
        release_hash=prior_control_state.release_hash,
        config_hash=prior_control_state.config_hash,
        account_allowlist_hash=prior_control_state.account_allowlist_hash,
        reconciliation_hash=reconciliation_hash(account, positions, prospective_risk),
        reconciled_at=FIXTURE_TIME,
    )
    ledger.initialize_control_state(prior_control_state)
    ledger.initialize_order_risk_state(baseline_risk)
    assert control_state.content_hash is not None
    risk_input = RiskInputV1(
        plan=plan,
        market_snapshot_hash=market.content_hash,
        account_snapshot_hash=account.content_hash,
        position_snapshot_hash=positions.content_hash,
        order_risk_snapshot_hash=prospective_risk.content_hash,
        risk_policy=policy,
        template_catalog_hash=template_catalog_hash,
        strategy_registry_hash=registry.registry_hash,
        strategy_config_hash=config.config_hash,
        strategy_content_hash=evaluation.plugin_content_hash,
        mode=OperatingModeV1.PAPER_DEMO_ARMED,
        control_state_hash=control_state.content_hash,
        control_state_version=control_state.version,
        account_allowlist_hash=ALLOWLIST_HASH,
        release_hash=RELEASE_HASH,
        entry_cutoff_at=COMPETITION_ENTRY_CUTOFF,
        flatten_at=COMPETITION_FLATTEN_AT,
    )
    approval = evaluate_risk(
        risk_input,
        market,
        account,
        positions,
        prospective_risk,
        control_state,
        now=FIXTURE_TIME,
    )
    command = ExecuteApprovedPlanV1(
        command_id=f"command-{plan.plan_hash.removeprefix('sha256:')[:24]}",
        plan=plan,
        approval=approval,
        risk_input_hash=risk_input.risk_input_hash,
        market_snapshot_hash=market.content_hash,
        account_snapshot_version=account.version,
        position_snapshot_version=positions.version,
        order_risk_snapshot_version=prospective_risk.version,
        control_state_hash=control_state.content_hash,
        control_state_version=control_state.version,
    )
    bundle = ExecutionBundleV1(
        bundle_id=f"bundle-{command.command_hash.removeprefix('sha256:')[:24]}",
        command=command,
        risk_input=risk_input,
        market=market,
        account=account,
        positions=positions,
        order_risk=prospective_risk,
        control_state=control_state,
    )
    ledger.reserve_and_enqueue(
        bundle=bundle,
        prospective_order_risk=prospective_risk,
        expected_prior_order_risk_version=baseline_risk.version,
        expected_prior_control_state=prior_control_state,
        event=_event(
            event_type="RiskApprovedAndCapacityReservedV1",
            aggregate_id=run_id,
            version=tape_version + 1,
            payload=approval,
            run_id=run_id,
        ),
    )
    return RunResult(
        status="APPROVED_AND_ENQUEUED",
        run_id=run_id,
        ledger=ledger,
        tape=ledger.decision_tape(run_id),
        details={
            "plan_hash": plan.plan_hash,
            "risk_input_hash": risk_input.risk_input_hash,
            "command_hash": command.command_hash,
        },
        command=command,
        risk_input=risk_input,
        market=market,
        account=account,
        positions=positions,
        order_risk=prospective_risk,
        control_state=control_state,
    )


def main() -> None:
    assert_native_developer_runtime()
    parser = argparse.ArgumentParser(description="Run frozen paper-system fixtures; no network or broker credentials.")
    parser.add_argument("--approved", action="store_true", help="run the fake-broker fixture path")
    args = parser.parse_args()
    result = run_approved_fixture() if args.approved else run_refusal_fixture()
    print(json.dumps({"status": result.status, "run_id": result.run_id, "details": result.details}, sort_keys=True))


if __name__ == "__main__":
    main()
