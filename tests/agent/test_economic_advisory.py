from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import httpx

from apps.decision_worker.main import FIXTURE_TIME, _evaluate
from packages.agent.advisory import AdvisoryModelClient
from packages.agent.config import load_advisory_model_catalog
from packages.agent.economic import EconomicAdvisoryModelClient
from packages.contracts.economic_input import economic_assessment_request_from_intent
from packages.contracts.models import AgentNarrativeV1, EconomicAssessmentV1
from packages.decision_core.registry import default_registry
from packages.decision_core.resolver import NoTradeRecordedV1, resolve
from packages.economic_context.frozen import fixture_daily_economic_context


def _profile():
    return load_advisory_model_catalog(
        Path(__file__).parents[2] / "configs" / "advisory_models.yaml"
    ).select("gemini_3_6_flash")


def _request():
    _, _, _, _, context, _, pair = _evaluate("regime_momentum", momentum=True)
    evaluation, thesis = pair
    entry = default_registry().entry(evaluation.plugin_id, evaluation.plugin_version)
    intent = resolve(
        evaluation,
        thesis,
        context,
        now=FIXTURE_TIME,
        position_policy_id=entry.position_policy_ref,
    )
    assert not isinstance(intent, NoTradeRecordedV1)
    daily = fixture_daily_economic_context(collected_at=FIXTURE_TIME - timedelta(hours=1, minutes=30))
    return economic_assessment_request_from_intent(daily, evaluation, intent)


def test_economic_advisory_sends_only_the_frozen_context_and_semantic_signal() -> None:
    request = _request()

    def handler(http_request: httpx.Request) -> httpx.Response:
        assert str(http_request.url) == "https://generativelanguage.googleapis.com/v1beta/interactions"
        body = json.loads(http_request.content)
        assert "official macroeconomic statistics" in body["system_instruction"]
        model_text = body["input"]
        assert "semantic_signal" in model_text
        assert "macro_market_proxies" in model_text
        for forbidden in ("account_id", "buying_power", "client_order_id", "maximum_loss", "option_surface"):
            assert forbidden not in model_text
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "model": "gemini-3.6-flash",
                "steps": [
                    {
                        "type": "model_output",
                        "content": [
                            {
                                "text": json.dumps(
                                    {
                                        "recommendation": "ALLOW_UNCHANGED",
                                        "diagnostic_confidence": "0.66",
                                        "reason_code": "ECONOMIC_CONTEXT_SUPPORTS_SIGNAL",
                                        "narrative": {
                                            "market_thesis": "The frozen market proxies support the direction.",
                                            "counter_thesis": "Proxy moves are not official macroeconomic data.",
                                            "explanation": "Preserve the semantic signal unchanged.",
                                        },
                                    }
                                )
                            }
                        ],
                    }
                ],
            },
        )

    base = AdvisoryModelClient(
        profile=_profile(),
        api_key="test-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assessment = EconomicAdvisoryModelClient(base).create_assessment(request)

    assert isinstance(assessment, EconomicAssessmentV1)
    assert assessment.recommendation == "ALLOW_UNCHANGED"
    assert assessment.economic_context_hash == request.economic_context.content_hash
    assert assessment.strategy_evaluation_hash == request.signal.strategy_evaluation_hash
    assert assessment.trade_intent_hash == request.signal.trade_intent_hash
    assert assessment.narrative == AgentNarrativeV1(
        market_thesis="The frozen market proxies support the direction.",
        counter_thesis="Proxy moves are not official macroeconomic data.",
        explanation="Preserve the semantic signal unchanged.",
    )
