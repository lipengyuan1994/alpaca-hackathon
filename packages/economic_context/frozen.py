"""Dependency-free frozen artifacts for economic-gate replay fixtures."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from packages.contracts.canonical import canonical_hash
from packages.contracts.economic_input import (
    economic_assessment_request_from_intent,
    sanitized_economic_model_input,
)
from packages.contracts.models import (
    AgentNarrativeV1,
    DailyEconomicContextV1,
    EconomicAssessmentV1,
    EconomicMarketObservationV1,
    StrategyEvaluationV1,
    TradeIntentV1,
)


def fixture_daily_economic_context(*, collected_at: datetime) -> DailyEconomicContextV1:
    """Return a causally valid morning context for credential-free replay."""

    collected_at = collected_at.astimezone(UTC)
    request_hash = canonical_hash(
        {
            "schema_version": "alpaca-economic-context-request/v1",
            "fixture": "economic-context-v1",
            "trading_date": collected_at.date().isoformat(),
        }
    )
    config_hash = canonical_hash({"fixture": "economic-context-config-v1"})
    observed_at = datetime(2026, 8, 28, 20, 0, tzinfo=UTC)

    def observation(symbol: str, category: str, close: str, previous_close: str) -> EconomicMarketObservationV1:
        return EconomicMarketObservationV1(
            category=category,  # type: ignore[arg-type]
            symbol=symbol,
            session_date=observed_at.date(),
            close=close,
            previous_close=previous_close,
            return_bps=((Decimal(close) / Decimal(previous_close)) - Decimal("1")) * Decimal("10000"),
            observed_at=observed_at,
            available_at=collected_at,
        )

    return DailyEconomicContextV1(
        context_id=f"economic-{request_hash.removeprefix('sha256:')[:24]}",
        trading_date=collected_at.date(),
        collected_at=collected_at,
        expires_at=datetime(2026, 9, 1, 4, 0, tzinfo=UTC),
        collection_config_hash=config_hash,
        source_request_hash=request_hash,
        macro_observations=(
            observation("SPY", "MACRO", "600", "598"),
            observation("QQQ", "MACRO", "530", "528"),
            observation("IWM", "MACRO", "220", "219"),
            observation("TLT", "MACRO", "90", "91"),
        ),
        micro_observations=(
            observation("SPY", "MICRO", "600", "598"),
            observation("QQQ", "MICRO", "530", "528"),
        ),
        quality_flags=("FIXTURE_ALPACA_MARKET_PROXY_CONTEXT",),
    )


def fixture_economic_assessment(
    context: DailyEconomicContextV1,
    evaluation: StrategyEvaluationV1,
    intent: TradeIntentV1,
    *,
    veto: bool = False,
) -> EconomicAssessmentV1:
    """Freeze a deterministic support/veto artifact; no model call occurs in replay."""

    request = economic_assessment_request_from_intent(context, evaluation, intent)
    input_hash = canonical_hash(sanitized_economic_model_input(request))
    recommendation = "VETO" if veto else "ALLOW_UNCHANGED"
    reason_code = "ECONOMIC_CONTEXT_CONTRADICTS_SIGNAL" if veto else "ECONOMIC_CONTEXT_SUPPORTS_SIGNAL"
    raw_hash = canonical_hash(
        {
            "fixture": "economic-assessment-v1",
            "input_hash": input_hash,
            "recommendation": recommendation,
            "reason_code": reason_code,
        }
    )
    return EconomicAssessmentV1(
        assessment_id=f"economic-{raw_hash.removeprefix('sha256:')[:24]}",
        economic_context_hash=context.content_hash,
        strategy_evaluation_hash=evaluation.evaluation_hash,
        trade_intent_hash=intent.content_hash,
        model_input_hash=input_hash,
        model_version="fixture/economic-advisory-v1",
        prompt_version="economic_fixture_v1",
        raw_output_hash=raw_hash,
        recommendation=recommendation,
        diagnostic_confidence="0.75",
        expires_at=intent.expires_at,
        reason_code=reason_code,
        narrative=AgentNarrativeV1(
            market_thesis="The frozen market proxies are directionally consistent with the semantic signal.",
            counter_thesis="Market proxies are not official macroeconomic releases and can change after collection.",
            explanation="The fixture preserves the original intent unchanged or records a deterministic veto.",
        ),
    )
