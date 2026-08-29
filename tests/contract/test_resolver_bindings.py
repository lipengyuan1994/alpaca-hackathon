from __future__ import annotations

from datetime import timedelta

from apps.decision_worker.main import FIXTURE_TIME, _evaluate
from packages.contracts.models import (
    AgentNarrativeV1,
    AgentThesisV1,
    PositionPolicyIdV1,
    StrategyEvaluationV1,
    TradeIntentV1,
)
from packages.decision_core.resolver import NoTradeRecordedV1, resolve


def _resolve(evaluation, thesis, context):
    return resolve(
        evaluation,
        thesis,
        context,
        now=FIXTURE_TIME,
        position_policy_id=PositionPolicyIdV1.TREND_VWAP_OR_60M_V1,
    )


def _entry_fixture():
    *_, context, _config, pair = _evaluate("regime_momentum", momentum=True)
    evaluation, thesis = pair
    return context, evaluation, thesis


def test_resolver_copies_the_original_semantic_request_unchanged() -> None:
    context, evaluation, thesis = _entry_fixture()

    outcome = _resolve(evaluation, thesis, context)

    assert isinstance(outcome, TradeIntentV1)
    decision = evaluation.decision
    assert outcome.underlying == decision.underlying
    assert outcome.template_id == decision.template_id
    assert outcome.horizon_bucket == decision.horizon_bucket
    assert outcome.risk_tier == decision.risk_tier
    assert outcome.expires_at == decision.intent_expires_at
    assert outcome.strategy_evaluation_hash == evaluation.evaluation_hash
    assert outcome.thesis_hash == thesis.content_hash


def test_resolver_rejects_a_valid_evaluation_rebound_to_another_context() -> None:
    context, evaluation, thesis = _entry_fixture()
    rebound_evaluation = StrategyEvaluationV1.model_validate(
        evaluation.model_dump(mode="json", exclude={"evaluation_hash"})
        | {"context_hash": "sha256:" + "f" * 64}
    )
    rebound_thesis = AgentThesisV1.model_validate(
        thesis.model_dump(mode="json", exclude={"content_hash"})
        | {"strategy_evaluation_hash": rebound_evaluation.evaluation_hash}
    )

    outcome = _resolve(rebound_evaluation, rebound_thesis, context)

    assert isinstance(outcome, NoTradeRecordedV1)
    assert outcome.reason_code == "STRATEGY_EVALUATION_BINDING_MISMATCH"


def test_resolver_treats_the_expiry_boundary_as_expired() -> None:
    context, evaluation, thesis = _entry_fixture()
    expired = AgentThesisV1.model_validate(
        thesis.model_dump(mode="json", exclude={"content_hash"})
        | {"expires_at": FIXTURE_TIME}
    )

    outcome = _resolve(evaluation, expired, context)

    assert isinstance(outcome, NoTradeRecordedV1)
    assert outcome.reason_code == "THESIS_EXPIRED"


def test_resolver_rejects_a_tampered_allow_verdict_before_it_can_authorize() -> None:
    context, evaluation, thesis = _entry_fixture()
    veto = AgentThesisV1.model_validate(
        thesis.model_dump(mode="json", exclude={"content_hash"})
        | {
            "recommendation": "VETO",
            "reason_code": "FIXTURE_VETO",
            "expires_at": FIXTURE_TIME + timedelta(seconds=300),
        }
    )
    tampered = veto.model_copy(update={"recommendation": "ALLOW_UNCHANGED"})

    outcome = _resolve(evaluation, tampered, context)

    assert isinstance(outcome, NoTradeRecordedV1)
    assert outcome.reason_code == "THESIS_CONTENT_HASH_MISMATCH"


def test_advisory_narrative_is_hash_bound_but_cannot_change_the_semantic_intent() -> None:
    context, evaluation, thesis = _entry_fixture()
    rewritten = AgentThesisV1.model_validate(
        thesis.model_dump(mode="json", exclude={"content_hash"})
        | {
            "narrative": AgentNarrativeV1(
                market_thesis="Different display-only thesis.",
                counter_thesis="Different display-only counter-thesis.",
                explanation="Different display-only explanation.",
            ).model_dump(mode="json")
        }
    )

    outcome = _resolve(evaluation, rewritten, context)

    assert isinstance(outcome, TradeIntentV1)
    decision = evaluation.decision
    assert (outcome.underlying, outcome.template_id, outcome.direction) == (
        decision.underlying,
        decision.template_id,
        "BULLISH",
    )
    assert (outcome.horizon_bucket, outcome.risk_tier, outcome.expires_at) == (
        decision.horizon_bucket,
        decision.risk_tier,
        decision.intent_expires_at,
    )
