"""Durable decision-job worker with an economic veto before option planning."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from packages.contracts.agent_input import agent_request_from_strategy
from packages.contracts.canonical import canonical_hash
from packages.contracts.economic_input import economic_assessment_request_from_intent
from packages.contracts.models import (
    ControlStateV1,
    EventEnvelopeV1,
    ExecuteApprovedPlanV1,
    ExecutionBundleV1,
    OrderRiskSnapshotV1,
    RiskInputV1,
    RiskReservationV1,
    SignalDecisionAuditV1,
    SignalDecisionStatusV1,
    SignalPlacementStateV1,
    StrategyContextV1,
)
from packages.decision_core import apply_economic_gate
from packages.decision_core.registry import RegistryError, default_registry
from packages.decision_core.resolver import NoTradeRecordedV1, resolve
from packages.domain import reconciliation_hash
from packages.economic_context.collector import EconomicContextError, nyse_trading_date
from packages.economic_context.store import PostgresEconomicContextStore
from packages.ledger import PostgresRuntimeLedger
from packages.order_planner import build_plan, template_catalog_hash
from packages.risk_kernel import default_policy, evaluate_risk
from packages.strategy_runner import PluginAuthorization, run_plugin

from .agent_gateway import InternalAdvisoryClient, InternalAdvisoryError


@dataclass(frozen=True)
class DurableDecisionOutcome:
    job_id: str | None
    status: str
    record_id: str | None = None
    reason_code: str | None = None


class DurableDecisionWorker:
    """Consumes one frozen job; it has no broker or Alpaca dependency."""

    def __init__(
        self,
        *,
        ledger: PostgresRuntimeLedger,
        audit_store: PostgresEconomicContextStore,
        advisory: InternalAdvisoryClient,
        worker_id: str,
    ) -> None:
        self._ledger = ledger
        self._audit_store = audit_store
        self._advisory = advisory
        self._worker_id = worker_id

    def process_once(self, *, now: datetime) -> DurableDecisionOutcome:
        now = now.astimezone(UTC)
        claim = self._ledger.claim_next_decision_job(worker_id=self._worker_id, now=now)
        if claim is None:
            return DurableDecisionOutcome(job_id=None, status="IDLE")
        job = claim.job
        try:
            return self._process_claim(job, now=now)
        except Exception as exc:
            self._ledger.release_decision_job(
                job_id=job.job_id,
                worker_id=self._worker_id,
                error="DECISION_RUNTIME_RETRY_REQUIRED",
                now=now,
            )
            return DurableDecisionOutcome(
                job_id=job.job_id,
                status="RETRY_SCHEDULED",
                reason_code=type(exc).__name__,
            )

    def _process_claim(self, job: Any, *, now: datetime) -> DurableDecisionOutcome:
        try:
            entry, context, evaluation = self._evaluate(job)
        except (KeyError, RegistryError, ValueError) as exc:
            return self._complete_no_trade(
                job,
                now=now,
                evaluation_hash=canonical_hash({"job_hash": job.job_hash, "failure": "strategy"}),
                reason_code="STRATEGY_EVALUATION_UNAVAILABLE",
                supplemental={"failure_class": type(exc).__name__},
            )
        try:
            thesis = self._advisory.create_thesis(agent_request_from_strategy(context, evaluation))
        except InternalAdvisoryError as exc:
            return self._complete_no_trade(
                job,
                now=now,
                evaluation_hash=evaluation.evaluation_hash,
                reason_code="AGENT_GATEWAY_UNAVAILABLE",
                signal_payload=self._signal_payload(evaluation),
                supplemental={"failure_code": str(exc)},
            )
        outcome = resolve(
            evaluation,
            thesis,
            context,
            now=now,
            position_policy_id=entry.position_policy_ref,
        )
        if isinstance(outcome, NoTradeRecordedV1):
            return self._complete_no_trade(
                job,
                now=now,
                evaluation_hash=evaluation.evaluation_hash,
                reason_code=outcome.reason_code,
                agent_thesis_hash=thesis.content_hash,
                signal_payload=self._signal_payload(evaluation),
                supplemental={"thesis_reason_code": thesis.reason_code},
            )
        intent = outcome
        try:
            economic_context = self._audit_store.load_daily_context(nyse_trading_date(job.as_of))
        except EconomicContextError as exc:
            economic_context = None
            context_failure = str(exc)
        else:
            context_failure = None
        if economic_context is None:
            return self._complete_no_trade(
                job,
                now=now,
                evaluation_hash=evaluation.evaluation_hash,
                reason_code=context_failure or "ECONOMIC_CONTEXT_UNAVAILABLE",
                agent_thesis_hash=thesis.content_hash,
                trade_intent_hash=intent.content_hash,
                signal_payload=self._signal_payload(evaluation),
                supplemental={"economic_context_status": "MISSING_OR_UNAVAILABLE"},
            )
        try:
            assessment = self._advisory.create_economic_assessment(
                economic_assessment_request_from_intent(economic_context, evaluation, intent)
            )
        except InternalAdvisoryError as exc:
            return self._complete_no_trade(
                job,
                now=now,
                evaluation_hash=evaluation.evaluation_hash,
                reason_code="ECONOMIC_AGENT_GATEWAY_UNAVAILABLE",
                agent_thesis_hash=thesis.content_hash,
                trade_intent_hash=intent.content_hash,
                economic_context_hash=economic_context.content_hash,
                signal_payload=self._signal_payload(evaluation),
                supplemental={"failure_code": str(exc)},
            )
        economic_outcome = apply_economic_gate(
            evaluation,
            intent,
            economic_context,
            assessment,
            now=now,
        )
        if isinstance(economic_outcome, NoTradeRecordedV1):
            return self._complete_no_trade(
                job,
                now=now,
                evaluation_hash=evaluation.evaluation_hash,
                reason_code=economic_outcome.reason_code,
                agent_thesis_hash=thesis.content_hash,
                trade_intent_hash=intent.content_hash,
                economic_context_hash=economic_context.content_hash,
                economic_assessment_hash=assessment.content_hash,
                signal_payload=self._signal_payload(evaluation),
                supplemental={
                    "economic_assessment_reason_code": assessment.reason_code,
                    "economic_narrative": assessment.narrative.model_dump(mode="json"),
                },
            )
        return self._approve_or_record_risk_rejection(
            job,
            now=now,
            entry=entry,
            evaluation=evaluation,
            thesis=thesis,
            intent=economic_outcome,
            economic_context=economic_context,
            assessment=assessment,
        )

    @staticmethod
    def _evaluate(job: Any) -> tuple[Any, StrategyContextV1, Any]:
        registry = default_registry()
        entry = registry.authorize(
            job.plugin_id,
            job.plugin_version,
            config_hash=job.config.config_hash,
            mode=job.control_state.mode,
        )
        context = StrategyContextV1(
            evaluation_id=f"evaluation-{job.job_hash.removeprefix('sha256:')[:24]}",
            as_of=job.as_of,
            market_snapshot_id=job.market.snapshot_id,
            market_snapshot_hash=job.market.content_hash,
            feature_vector_id=job.feature_vector.feature_id,
            feature_vector_hash=job.feature_vector.content_hash,
            feature_contract_hash=job.feature_vector.feature_contract_hash,
            feature_available_time=job.feature_vector.available_time,
            feed_identity=job.market.feed_identity,
            quality_flags=job.market.quality_flags,
            universe_features={
                key: job.feature_vector.values[key]
                for key in entry.data_requirements.required_feature_keys
            },
            allowed_intent_tuples=entry.allowed_intent_tuples,
            prior_state=job.prior_state,
            config_hash=job.config.config_hash,
        )
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
        evaluation = run_plugin(authorization=authorization, context=context, config=job.config)
        return entry, context, evaluation

    def _approve_or_record_risk_rejection(
        self,
        job: Any,
        *,
        now: datetime,
        entry: Any,
        evaluation: Any,
        thesis: Any,
        intent: Any,
        economic_context: Any,
        assessment: Any,
    ) -> DurableDecisionOutcome:
        plan = build_plan(intent, job.market, job.account, job.positions, job.order_risk, now=now)
        policy = default_policy()
        prospective_risk = OrderRiskSnapshotV1(
            snapshot_id=f"order-risk-{plan.plan_hash.removeprefix('sha256:')[:24]}",
            account_id=job.account.account_id,
            version=job.order_risk.version + 1,
            as_of=now,
            reservations=job.order_risk.reservations
            + (
                RiskReservationV1(
                    reservation_id=f"reservation-{plan.plan_hash.removeprefix('sha256:')[:24]}",
                    plan_hash=plan.plan_hash,
                    maximum_loss=plan.maximum_loss,
                    remaining_quantity=plan.quantity,
                    expires_at=now + timedelta(seconds=policy.approval_ttl_seconds),
                    status="APPROVED",
                ),
            ),
            working_client_order_ids=job.order_risk.working_client_order_ids,
        )
        prospective_control = ControlStateV1(
            account_id=job.control_state.account_id,
            version=job.control_state.version + 1,
            mode=job.control_state.mode,
            release_hash=job.control_state.release_hash,
            config_hash=job.control_state.config_hash,
            account_allowlist_hash=job.control_state.account_allowlist_hash,
            reconciliation_hash=reconciliation_hash(job.account, job.positions, prospective_risk),
            reconciled_at=job.control_state.reconciled_at,
        )
        risk_input = RiskInputV1(
            plan=plan,
            market_snapshot_hash=job.market.content_hash,
            account_snapshot_hash=job.account.content_hash,
            position_snapshot_hash=job.positions.content_hash,
            order_risk_snapshot_hash=prospective_risk.content_hash,
            risk_policy=policy,
            template_catalog_hash=template_catalog_hash,
            strategy_registry_hash=default_registry().registry_hash,
            strategy_config_hash=job.config.config_hash,
            strategy_content_hash=evaluation.plugin_content_hash,
            mode=job.control_state.mode,
            control_state_hash=prospective_control.content_hash,
            control_state_version=prospective_control.version,
            account_allowlist_hash=job.control_state.account_allowlist_hash,
            release_hash=job.control_state.release_hash,
            entry_cutoff_at=job.entry_cutoff_at,
            flatten_at=job.flatten_at,
        )
        approval = evaluate_risk(
            risk_input,
            job.market,
            job.account,
            job.positions,
            prospective_risk,
            prospective_control,
            now=now,
        )
        if not approval.approved:
            return self._complete_no_trade(
                job,
                now=now,
                evaluation_hash=evaluation.evaluation_hash,
                reason_code="RISK_REJECTED",
                decision_status=SignalDecisionStatusV1.RISK_REJECTED,
                agent_thesis_hash=thesis.content_hash,
                trade_intent_hash=intent.content_hash,
                economic_context_hash=economic_context.content_hash,
                economic_assessment_hash=assessment.content_hash,
                plan_hash=plan.plan_hash,
                client_order_id=plan.client_order_id,
                signal_payload=self._signal_payload(evaluation),
                supplemental={"risk_reason_codes": list(approval.reason_codes)},
            )
        command = ExecuteApprovedPlanV1(
            command_id=f"command-{plan.plan_hash.removeprefix('sha256:')[:24]}",
            plan=plan,
            approval=approval,
            risk_input_hash=risk_input.risk_input_hash,
            market_snapshot_hash=job.market.content_hash,
            account_snapshot_version=job.account.version,
            position_snapshot_version=job.positions.version,
            order_risk_snapshot_version=prospective_risk.version,
            control_state_hash=prospective_control.content_hash,
            control_state_version=prospective_control.version,
        )
        bundle = ExecutionBundleV1(
            bundle_id=f"bundle-{command.command_hash.removeprefix('sha256:')[:24]}",
            command=command,
            risk_input=risk_input,
            market=job.market,
            account=job.account,
            positions=job.positions,
            order_risk=prospective_risk,
            control_state=prospective_control,
        )
        record = self._audit_record(
            job,
            now=now,
            evaluation_hash=evaluation.evaluation_hash,
            reason_code="RISK_APPROVED",
            decision_status=SignalDecisionStatusV1.APPROVED_AND_ENQUEUED,
            placement_state=SignalPlacementStateV1.ENQUEUED,
            agent_thesis_hash=thesis.content_hash,
            trade_intent_hash=intent.content_hash,
            economic_context_hash=economic_context.content_hash,
            economic_assessment_hash=assessment.content_hash,
            plan_hash=plan.plan_hash,
            client_order_id=plan.client_order_id,
            signal_payload=self._signal_payload(evaluation),
            supplemental={
                "economic_assessment_reason_code": assessment.reason_code,
                "economic_narrative": assessment.narrative.model_dump(mode="json"),
                "risk_decision_hash": approval.decision_hash,
            },
        )
        self._ledger.reserve_and_enqueue(
            bundle=bundle,
            prospective_order_risk=prospective_risk,
            expected_prior_order_risk_version=job.order_risk.version,
            expected_prior_control_state=job.control_state,
            event=self._event(
                job=job,
                now=now,
                event_type="RiskApprovedAndCapacityReservedV1",
                payload=approval.model_dump(mode="json"),
            ),
            signal_audit=record,
            decision_job_id=job.job_id,
            decision_worker_id=self._worker_id,
        )
        return DurableDecisionOutcome(
            job_id=job.job_id,
            status="APPROVED_AND_ENQUEUED",
            record_id=record.record_id,
        )

    def _complete_no_trade(
        self,
        job: Any,
        *,
        now: datetime,
        evaluation_hash: str,
        reason_code: str,
        decision_status: SignalDecisionStatusV1 = SignalDecisionStatusV1.NO_TRADE,
        agent_thesis_hash: str | None = None,
        trade_intent_hash: str | None = None,
        economic_context_hash: str | None = None,
        economic_assessment_hash: str | None = None,
        plan_hash: str | None = None,
        client_order_id: str | None = None,
        signal_payload: dict[str, Any] | None = None,
        supplemental: dict[str, Any] | None = None,
    ) -> DurableDecisionOutcome:
        record = self._audit_record(
            job,
            now=now,
            evaluation_hash=evaluation_hash,
            reason_code=reason_code,
            decision_status=decision_status,
            placement_state=SignalPlacementStateV1.NOT_PLACED,
            agent_thesis_hash=agent_thesis_hash,
            trade_intent_hash=trade_intent_hash,
            economic_context_hash=economic_context_hash,
            economic_assessment_hash=economic_assessment_hash,
            plan_hash=plan_hash,
            client_order_id=client_order_id,
            signal_payload=signal_payload or {},
            supplemental=supplemental or {},
        )
        self._audit_store.record_signal_and_complete_decision_job(
            record=record,
            job_id=job.job_id,
            worker_id=self._worker_id,
            result_status=decision_status.value,
        )
        return DurableDecisionOutcome(
            job_id=job.job_id,
            status=decision_status.value,
            record_id=record.record_id,
            reason_code=reason_code,
        )

    @staticmethod
    def _signal_payload(evaluation: Any) -> dict[str, Any]:
        return {
            "evaluation_id": evaluation.evaluation_id,
            "plugin_id": evaluation.plugin_id,
            "plugin_version": evaluation.plugin_version,
            "decision": evaluation.decision.model_dump(mode="json"),
        }

    @staticmethod
    def _event(*, job: Any, now: datetime, event_type: str, payload: dict[str, Any]) -> EventEnvelopeV1:
        event_id = f"event-{canonical_hash([job.run_id, job.job_id, event_type]).removeprefix('sha256:')[:24]}"
        return EventEnvelopeV1(
            event_id=event_id,
            event_type=event_type,
            # A run can contain more than one signal.  Keep the event-stream
            # version unique per durable job instead of colliding at version 1
            # when two jobs share the same run identifier.
            aggregate_id=job.job_id,
            aggregate_version=1,
            occurred_at=now,
            received_at=now,
            producer="decision-worker",
            run_id=job.run_id,
            correlation_id=job.job_id,
            payload=payload,
        )

    @staticmethod
    def _audit_record(
        job: Any,
        *,
        now: datetime,
        evaluation_hash: str,
        reason_code: str,
        decision_status: SignalDecisionStatusV1,
        placement_state: SignalPlacementStateV1,
        agent_thesis_hash: str | None,
        trade_intent_hash: str | None,
        economic_context_hash: str | None,
        economic_assessment_hash: str | None,
        plan_hash: str | None,
        client_order_id: str | None,
        signal_payload: dict[str, Any],
        supplemental: dict[str, Any],
    ) -> SignalDecisionAuditV1:
        record_id = f"signal-{canonical_hash([job.run_id, evaluation_hash]).removeprefix('sha256:')[:24]}"
        return SignalDecisionAuditV1(
            record_id=record_id,
            run_id=job.run_id,
            trading_date=nyse_trading_date(job.as_of),
            recorded_at=now,
            strategy_evaluation_hash=evaluation_hash,
            agent_thesis_hash=agent_thesis_hash,
            trade_intent_hash=trade_intent_hash,
            economic_context_hash=economic_context_hash,
            economic_assessment_hash=economic_assessment_hash,
            decision_status=decision_status,
            placement_state=placement_state,
            order_placed=False,
            reason_code=reason_code,
            plan_hash=plan_hash,
            client_order_id=client_order_id,
            signal_payload=signal_payload,
            supplemental=supplemental,
        )
