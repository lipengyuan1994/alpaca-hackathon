"""The monotonic semantic resolver: AI can leave a request unchanged or veto it."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from packages.contracts.models import (
    AgentThesisV1,
    EntryTemplateRequestV1,
    NoTradeV1,
    PositionPolicyIdV1,
    StrategyContextV1,
    StrategyEvaluationV1,
    TradeIntentV1,
)


@dataclass(frozen=True)
class NoTradeRecordedV1:
    decision_id: str
    reason_code: str
    strategy_evaluation_hash: str
    thesis_hash: str


def _refusal(evaluation: StrategyEvaluationV1, thesis: AgentThesisV1, reason: str) -> NoTradeRecordedV1:
    return NoTradeRecordedV1(
        decision_id=evaluation.evaluation_id,
        reason_code=reason,
        strategy_evaluation_hash=evaluation.evaluation_hash,
        thesis_hash=thesis.content_hash,
    )


def resolve(
    evaluation: StrategyEvaluationV1,
    thesis: AgentThesisV1,
    context: StrategyContextV1,
    *,
    now: datetime,
    position_policy_id: PositionPolicyIdV1,
) -> TradeIntentV1 | NoTradeRecordedV1:
    now = now.astimezone(UTC)
    if thesis.context_hash != context.context_hash or thesis.strategy_evaluation_hash != evaluation.evaluation_hash:
        return _refusal(evaluation, thesis, "THESIS_BINDING_MISMATCH")
    if now > thesis.expires_at:
        return _refusal(evaluation, thesis, "THESIS_EXPIRED")
    if thesis.recommendation == "VETO":
        return _refusal(evaluation, thesis, "AGENT_VETO")
    if isinstance(evaluation.decision, NoTradeV1):
        return _refusal(evaluation, thesis, evaluation.decision.primary_reason_code)
    if not isinstance(evaluation.decision, EntryTemplateRequestV1):
        return _refusal(evaluation, thesis, "POSITION_DIRECTIVE_NOT_IMPLEMENTED")
    decision = evaluation.decision
    if now >= decision.intent_expires_at:
        return _refusal(evaluation, thesis, "INTENT_EXPIRED")
    allowed = any(
        item.template_id == decision.template_id
        and item.horizon_bucket == decision.horizon_bucket
        and item.risk_tier == decision.risk_tier
        and 0 < (decision.intent_expires_at - context.as_of).total_seconds()
        <= item.max_intent_ttl_seconds
        for item in context.allowed_intent_tuples
    )
    if not allowed:
        # Underlying authority is checked by the registry/planner too; this keeps the resolver pure.
        return _refusal(evaluation, thesis, "INTENT_TUPLE_NOT_ALLOWED")
    direction = "BULLISH" if decision.template_id in {"CALL_DEBIT_SPREAD_V1", "LONG_CALL_V1"} else "BEARISH"
    return TradeIntentV1(
        intent_id=f"intent-{evaluation.evaluation_hash.removeprefix('sha256:')[:24]}",
        as_of=context.as_of,
        underlying=decision.underlying,
        template_id=decision.template_id,
        direction=direction,
        horizon_bucket=decision.horizon_bucket,
        risk_tier=decision.risk_tier,
        position_policy_id=position_policy_id,
        expires_at=decision.intent_expires_at,
        strategy_evaluation_hash=evaluation.evaluation_hash,
        thesis_hash=thesis.content_hash,
    )
