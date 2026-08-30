from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace

from apps.decision_worker.main import (
    ALLOWLIST_HASH,
    COMPETITION_ENTRY_CUTOFF,
    COMPETITION_FLATTEN_AT,
    FIXTURE_TIME,
    RELEASE_HASH,
    fixture_inputs,
)
from apps.decision_worker.runtime import DurableDecisionWorker
from packages.contracts.agent_input import sanitized_model_input
from packages.contracts.canonical import canonical_hash
from packages.contracts.economic_input import sanitized_economic_model_input
from packages.contracts.models import (
    AgentNarrativeV1,
    AgentThesisV1,
    ControlStateV1,
    DecisionJobV1,
    EconomicAssessmentV1,
    FeatureVectorV1,
    OperatingModeV1,
    StrategyStateV1,
)
from packages.domain import reconciliation_hash
from packages.economic_context.frozen import fixture_daily_economic_context


def _job() -> DecisionJobV1:
    market, account, positions, order_risk, context, config = fixture_inputs(momentum=True)
    feature = FeatureVectorV1(
        feature_id="durable-feature-1",
        feature_contract_hash=context.feature_contract_hash,
        calculated_at=FIXTURE_TIME - timedelta(seconds=1),
        available_time=FIXTURE_TIME - timedelta(seconds=1),
        values=context.universe_features,
        source_market_hash=market.content_hash,
    )
    prior_state = StrategyStateV1(
        plugin_id="regime_momentum",
        plugin_version="1.0.0",
        as_of=FIXTURE_TIME,
        sequence=0,
    )
    control = ControlStateV1(
        account_id=account.account_id,
        version=1,
        mode=OperatingModeV1.PAPER_DEMO_ARMED,
        release_hash=RELEASE_HASH,
        config_hash=config.config_hash,
        account_allowlist_hash=ALLOWLIST_HASH,
        reconciliation_hash=reconciliation_hash(account, positions, order_risk),
        reconciled_at=FIXTURE_TIME,
    )
    return DecisionJobV1(
        job_id="decision-job-economic-veto",
        run_id="run-economic-veto-durable",
        as_of=FIXTURE_TIME,
        plugin_id="regime_momentum",
        plugin_version="1.0.0",
        market=market,
        feature_vector=feature,
        account=account,
        positions=positions,
        order_risk=order_risk,
        control_state=control,
        prior_state=prior_state,
        config=config,
        entry_cutoff_at=COMPETITION_ENTRY_CUTOFF,
        flatten_at=COMPETITION_FLATTEN_AT,
    )


class _Ledger:
    def __init__(self, job: DecisionJobV1) -> None:
        self._job = job
        self.claimed = False
        self.reserve_called = False

    def claim_next_decision_job(self, *, worker_id, now):
        del worker_id, now
        if self.claimed:
            return None
        self.claimed = True
        return SimpleNamespace(job=self._job)

    def release_decision_job(self, **kwargs):  # pragma: no cover - assertion path
        raise AssertionError(f"economic veto must complete, not retry: {kwargs}")

    def reserve_and_enqueue(self, **kwargs):  # pragma: no cover - assertion path
        self.reserve_called = True
        raise AssertionError(f"economic veto must precede outbox: {kwargs}")


@dataclass
class _AuditStore:
    daily: object
    record: object | None = None

    def load_daily_context(self, trading_date):
        assert trading_date == self.daily.trading_date
        return self.daily

    def record_signal_and_complete_decision_job(self, *, record, job_id, worker_id, result_status):
        assert job_id == "decision-job-economic-veto"
        assert worker_id == "decision-worker-test"
        assert result_status == "NO_TRADE"
        self.record = record


class _Advisory:
    def create_thesis(self, request):
        raw_hash = canonical_hash({"kind": "thesis", "input": request.model_dump(mode="json")})
        return AgentThesisV1(
            thesis_id="thesis-durable-economic-veto",
            context_hash=request.context.context_hash,
            strategy_evaluation_hash=request.evaluation.evaluation_hash,
            model_input_hash=canonical_hash(sanitized_model_input(request)),
            model_version="fixture/advisory",
            prompt_version="fixture_v1",
            raw_output_hash=raw_hash,
            recommendation="ALLOW_UNCHANGED",
            diagnostic_confidence="0.8",
            expires_at=FIXTURE_TIME + timedelta(minutes=5),
            reason_code="AGENT_CONTEXT_ALIGNED",
            narrative=AgentNarrativeV1(
                market_thesis="Fixture thesis allows the semantic signal.",
                counter_thesis="Fixture limits remain in force.",
                explanation="Continue to the economic gate.",
            ),
        )

    def create_economic_assessment(self, request):
        raw_hash = canonical_hash({"kind": "economic", "input": request.model_dump(mode="json")})
        return EconomicAssessmentV1(
            assessment_id="economic-durable-veto",
            economic_context_hash=request.economic_context.content_hash,
            strategy_evaluation_hash=request.signal.strategy_evaluation_hash,
            trade_intent_hash=request.signal.trade_intent_hash,
            model_input_hash=canonical_hash(sanitized_economic_model_input(request)),
            model_version="fixture/economic-advisory",
            prompt_version="fixture_economic_v1",
            raw_output_hash=raw_hash,
            recommendation="VETO",
            diagnostic_confidence="0.9",
            expires_at=request.signal.expires_at,
            reason_code="ECONOMIC_CONTEXT_CONTRADICTS_SIGNAL",
            narrative=AgentNarrativeV1(
                market_thesis="The frozen context conflicts with the trade direction.",
                counter_thesis="The signal was otherwise valid.",
                explanation="Veto before planning an option order.",
            ),
        )


def test_durable_worker_persists_economic_veto_and_never_enqueues_an_option_order() -> None:
    job = _job()
    ledger = _Ledger(job)
    daily = fixture_daily_economic_context(collected_at=FIXTURE_TIME - timedelta(hours=1, minutes=30))
    audit_store = _AuditStore(daily=daily)
    worker = DurableDecisionWorker(
        ledger=ledger,  # type: ignore[arg-type]
        audit_store=audit_store,  # type: ignore[arg-type]
        advisory=_Advisory(),  # type: ignore[arg-type]
        worker_id="decision-worker-test",
    )

    outcome = worker.process_once(now=FIXTURE_TIME)

    assert outcome.status == "NO_TRADE"
    assert outcome.reason_code == "ECONOMIC_VETO"
    assert ledger.reserve_called is False
    assert audit_store.record is not None
    assert audit_store.record.placement_state == "NOT_PLACED"
    assert audit_store.record.order_placed is False
    assert audit_store.record.economic_context_hash == daily.content_hash
    assert audit_store.record.economic_assessment_hash is not None
