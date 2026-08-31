"""Fail-closed economic support gate after semantic intent and before planning."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from packages.contracts.canonical import canonical_hash, hash_without
from packages.contracts.economic_input import (
    economic_assessment_request_from_intent,
    sanitized_economic_model_input,
)
from packages.contracts.models import (
    DailyEconomicContextV1,
    EconomicAssessmentV1,
    StrategyEvaluationV1,
    TradeIntentV1,
)

from .resolver import NoTradeRecordedV1

_NEW_YORK = ZoneInfo("America/New_York")


def _refusal(
    evaluation: StrategyEvaluationV1,
    intent: TradeIntentV1,
    reason_code: str,
    *,
    economic_context_hash: str | None,
    economic_assessment_hash: str | None,
) -> NoTradeRecordedV1:
    return NoTradeRecordedV1(
        decision_id=evaluation.evaluation_id,
        reason_code=reason_code,
        strategy_evaluation_hash=intent.strategy_evaluation_hash,
        thesis_hash=intent.thesis_hash,
        economic_context_hash=economic_context_hash,
        economic_assessment_hash=economic_assessment_hash,
    )


def apply_economic_gate(
    evaluation: StrategyEvaluationV1,
    intent: TradeIntentV1,
    context: DailyEconomicContextV1,
    assessment: EconomicAssessmentV1,
    *,
    now: datetime,
) -> TradeIntentV1 | NoTradeRecordedV1:
    """Preserve a semantic intent unchanged only when the frozen gate allows it.

    This function deliberately executes before exact option selection, quantity,
    price, risk reservation, and outbox publication.  The LLM can never amend
    any executable field: the only successful return is the original ``intent``
    object itself.
    """

    now = now.astimezone(UTC)
    context_hash = context.content_hash
    assessment_hash = assessment.content_hash
    if context_hash != hash_without(context, "content_hash"):
        return _refusal(
            evaluation,
            intent,
            "ECONOMIC_CONTEXT_HASH_MISMATCH",
            economic_context_hash=context_hash,
            economic_assessment_hash=assessment_hash,
        )
    if assessment_hash != hash_without(assessment, "content_hash"):
        return _refusal(
            evaluation,
            intent,
            "ECONOMIC_ASSESSMENT_HASH_MISMATCH",
            economic_context_hash=context_hash,
            economic_assessment_hash=assessment_hash,
        )
    if intent.strategy_evaluation_hash != evaluation.evaluation_hash:
        return _refusal(
            evaluation,
            intent,
            "ECONOMIC_INTENT_EVALUATION_BINDING_MISMATCH",
            economic_context_hash=context_hash,
            economic_assessment_hash=assessment_hash,
        )
    if (
        intent.as_of.astimezone(_NEW_YORK).date() != context.trading_date
        or now.astimezone(_NEW_YORK).date() != context.trading_date
    ):
        return _refusal(
            evaluation,
            intent,
            "ECONOMIC_CONTEXT_WRONG_TRADING_DATE",
            economic_context_hash=context_hash,
            economic_assessment_hash=assessment_hash,
        )
    if now >= context.expires_at:
        return _refusal(
            evaluation,
            intent,
            "ECONOMIC_CONTEXT_EXPIRED",
            economic_context_hash=context_hash,
            economic_assessment_hash=assessment_hash,
        )
    if (
        assessment.economic_context_hash != context_hash
        or assessment.strategy_evaluation_hash != evaluation.evaluation_hash
        or assessment.trade_intent_hash != intent.content_hash
    ):
        return _refusal(
            evaluation,
            intent,
            "ECONOMIC_ASSESSMENT_BINDING_MISMATCH",
            economic_context_hash=context_hash,
            economic_assessment_hash=assessment_hash,
        )
    try:
        expected_input_hash = canonical_hash(
            sanitized_economic_model_input(
                economic_assessment_request_from_intent(context, evaluation, intent)
            )
        )
    except ValueError:
        return _refusal(
            evaluation,
            intent,
            "ECONOMIC_MODEL_INPUT_BINDING_MISMATCH",
            economic_context_hash=context_hash,
            economic_assessment_hash=assessment_hash,
        )
    if assessment.model_input_hash != expected_input_hash:
        return _refusal(
            evaluation,
            intent,
            "ECONOMIC_MODEL_INPUT_BINDING_MISMATCH",
            economic_context_hash=context_hash,
            economic_assessment_hash=assessment_hash,
        )
    if now >= assessment.expires_at:
        return _refusal(
            evaluation,
            intent,
            "ECONOMIC_ASSESSMENT_EXPIRED",
            economic_context_hash=context_hash,
            economic_assessment_hash=assessment_hash,
        )
    if assessment.recommendation != "ALLOW_UNCHANGED":
        return _refusal(
            evaluation,
            intent,
            "ECONOMIC_VETO",
            economic_context_hash=context_hash,
            economic_assessment_hash=assessment_hash,
        )
    return intent
