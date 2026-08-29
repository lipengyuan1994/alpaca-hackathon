"""Fixture-driven decision worker that demonstrates both refusal and approved paths."""

from __future__ import annotations

import argparse
import json
import platform
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from packages.agent import fixture_thesis
from packages.contracts.canonical import canonical_hash
from packages.contracts.models import (
    AccountSnapshotV1,
    EventEnvelopeV1,
    ExecuteApprovedPlanV1,
    FeedIdentityV1,
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
from packages.ledger import MemoryLedger
from packages.market_data import compute_feature_vector
from packages.order_planner import build_plan, template_catalog_hash
from packages.risk_kernel import default_policy, evaluate_risk
from packages.strategy_runner import run_plugin

FIXTURE_TIME = datetime(2026, 8, 31, 14, 15, tzinfo=UTC)
RELEASE_HASH = canonical_hash({"release": "fixture-v1"})
ALLOWLIST_HASH = canonical_hash({"accounts": ["paper-fixture-account"]})


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


def assert_native_developer_runtime() -> None:
    if platform.machine() != "arm64":
        raise RuntimeError("LOCAL_RUNTIME_MUST_BE_ARM64")


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


def fixture_inputs(*, momentum: bool = False) -> tuple[
    MarketSnapshotV1,
    AccountSnapshotV1,
    PositionSnapshotV1,
    OrderRiskSnapshotV1,
    StrategyContextV1,
    StrategyConfigV1,
]:
    quote = QuoteV1(bid="600", ask="600.01", event_time=FIXTURE_TIME, available_time=FIXTURE_TIME)
    market = MarketSnapshotV1(
        snapshot_id="market-fixture-1",
        as_of=FIXTURE_TIME,
        feed_identity=FeedIdentityV1(entitlement="alpaca-basic-fixture"),
        underlying_quotes={"SPY": quote},
        option_contracts=(
            OptionContractV1(
                symbol="SPY260915C00600000",
                underlying="SPY",
                right="CALL",
                strike="600",
                expiration=FIXTURE_TIME + timedelta(days=15),
                quote=QuoteV1(bid="1.80", ask="2.00", event_time=FIXTURE_TIME, available_time=FIXTURE_TIME),
            ),
            OptionContractV1(
                symbol="SPY260915C00605000",
                underlying="SPY",
                right="CALL",
                strike="605",
                expiration=FIXTURE_TIME + timedelta(days=15),
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
    feature = compute_feature_vector(
        market,
        feature_id="features-fixture-1",
        calculated_at=FIXTURE_TIME,
        values={"momentum_z": Decimal("1.2") if momentum else Decimal("0.0")},
    )
    registry = default_registry()
    context = StrategyContextV1(
        evaluation_id="evaluation-fixture-1",
        as_of=FIXTURE_TIME,
        market_snapshot_id=market.snapshot_id,
        market_snapshot_hash=market.content_hash,
        feature_vector_id=feature.feature_id,
        feature_vector_hash=feature.content_hash,
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
    entry = registry.entry(plugin_id, "1.0.0")
    if plugin_id == "regime_momentum":
        context = context.model_copy(
            update={
                "prior_state": context.prior_state.model_copy(
                    update={
                        "plugin_id": "regime_momentum",
                        "plugin_version": "1.0.0",
                        "state_hash": None,
                    }
                )
            }
        )
        # Derived hash must change with the substituted prior state.
        context = StrategyContextV1.model_validate(context.model_dump(exclude={"context_hash"}))
    evaluation = run_plugin(entrypoint=entry.entrypoint, context=context, config=config)
    thesis = fixture_thesis(context, evaluation)
    return market, account, positions, order_risk, context, config, (evaluation, thesis)


def run_refusal_fixture() -> RunResult:
    """Produce the required complete, idempotent visible NO_TRADE decision tape."""
    run_id = "run-fixture-no-trade"
    ledger = MemoryLedger()
    market, _, _, _, context, _, pair = _evaluate("always_no_trade", momentum=False)
    evaluation, thesis = pair
    outcome = resolve(evaluation, thesis, context, now=FIXTURE_TIME)
    assert isinstance(outcome, NoTradeRecordedV1)
    ledger.append(_event(event_type="MarketSnapshotRecordedV1", aggregate_id=run_id, version=1, payload=market, run_id=run_id))
    ledger.append(
        _event(event_type="NoTradeRecordedV1", aggregate_id=run_id, version=2, payload=outcome.__dict__, run_id=run_id)
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
    intent = resolve(evaluation, thesis, context, now=FIXTURE_TIME)
    if isinstance(intent, NoTradeRecordedV1):
        raise RuntimeError(f"fixture unexpectedly refused: {intent.reason_code}")
    plan = build_plan(intent, market, account, positions, baseline_risk, now=FIXTURE_TIME)
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
    registry = default_registry()
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
        account_allowlist_hash=ALLOWLIST_HASH,
        release_hash=RELEASE_HASH,
    )
    approval = evaluate_risk(risk_input, market, account, positions, prospective_risk, now=FIXTURE_TIME)
    command = ExecuteApprovedPlanV1(
        command_id=f"command-{plan.plan_hash.removeprefix('sha256:')[:24]}",
        plan=plan,
        approval=approval,
        risk_input_hash=risk_input.risk_input_hash,
        market_snapshot_hash=market.content_hash,
        account_snapshot_version=account.version,
        position_snapshot_version=positions.version,
        order_risk_snapshot_version=prospective_risk.version,
    )
    ledger.reserve_and_enqueue(
        account_id=account.account_id,
        approval=approval,
        command=command,
        event=_event(
            event_type="RiskApprovedAndCapacityReservedV1",
            aggregate_id=run_id,
            version=1,
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
