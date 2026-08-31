"""Narrow, sanitized input for the economic advisory gate.

The economic model sees an already-created semantic trade intent and the
frozen morning context.  It never receives account, option-chain, quantity,
price, risk, or execution state and can only preserve that intent or veto it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from .models import (
    DailyEconomicContextV1,
    StrategyEvaluationV1,
    StrictModel,
    TradeIntentV1,
)


class EconomicSignalV1(StrictModel):
    """Allowlisted semantic signal fields visible to the economic model."""

    evaluation_id: str = Field(min_length=1, max_length=128)
    strategy_evaluation_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    intent_id: str = Field(min_length=1, max_length=128)
    trade_intent_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    underlying: str = Field(pattern=r"^[A-Z]{1,8}$")
    direction: Literal["BULLISH", "BEARISH"]
    template_id: Literal["CALL_DEBIT_SPREAD_V1", "PUT_DEBIT_SPREAD_V1", "LONG_CALL_V1", "LONG_PUT_V1"]
    horizon_bucket: Literal["INTRADAY_15_60M"]
    risk_tier: Literal["TINY", "STANDARD"]
    expires_at: datetime


class EconomicAssessmentRequestV1(StrictModel):
    """Complete, bounded economic advisory request."""

    schema_version: Literal["economic-assessment-request/v1"] = "economic-assessment-request/v1"
    economic_context: DailyEconomicContextV1
    signal: EconomicSignalV1

    @model_validator(mode="after")
    def _bindings_match(self) -> "EconomicAssessmentRequestV1":
        if self.signal.strategy_evaluation_hash == "":  # defensive; pattern already enforces this
            raise ValueError("economic signal evaluation hash missing")
        return self


def economic_assessment_request_from_intent(
    economic_context: DailyEconomicContextV1,
    evaluation: StrategyEvaluationV1,
    intent: TradeIntentV1,
) -> EconomicAssessmentRequestV1:
    """Project immutable decision artifacts into the economic advisory surface."""

    if intent.strategy_evaluation_hash != evaluation.evaluation_hash:
        raise ValueError("economic request intent evaluation binding mismatch")
    return EconomicAssessmentRequestV1(
        economic_context=economic_context,
        signal=EconomicSignalV1(
            evaluation_id=evaluation.evaluation_id,
            strategy_evaluation_hash=evaluation.evaluation_hash,
            intent_id=intent.intent_id,
            trade_intent_hash=intent.content_hash,
            underlying=intent.underlying,
            direction=intent.direction,
            template_id=intent.template_id,
            horizon_bucket=intent.horizon_bucket,
            risk_tier=intent.risk_tier,
            expires_at=intent.expires_at,
        ),
    )


def sanitized_economic_model_input(request: EconomicAssessmentRequestV1) -> dict[str, Any]:
    """Render the frozen context and semantic signal, with no execution fields."""

    context = request.economic_context
    return {
        "schema_version": "economic-model-input/v1",
        "economic_context": {
            "context_id": context.context_id,
            "trading_date": context.trading_date.isoformat(),
            "collected_at": context.collected_at,
            "expires_at": context.expires_at,
            "content_hash": context.content_hash,
            "collection_config_hash": context.collection_config_hash,
            "source_request_hash": context.source_request_hash,
            "macro_market_proxies": [
                item.model_dump(mode="json") for item in context.macro_observations
            ],
            "micro_market_context": [
                item.model_dump(mode="json") for item in context.micro_observations
            ],
            "news_headlines_untrusted": [
                item.model_dump(mode="json") for item in context.news_headlines
            ],
            "quality_flags": list(context.quality_flags),
        },
        "semantic_signal": request.signal.model_dump(mode="json"),
        "resolver_constraint": "ALLOW_UNCHANGED_OR_VETO_ONLY",
    }
