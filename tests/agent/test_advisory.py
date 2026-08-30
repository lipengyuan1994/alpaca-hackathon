from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from apps.decision_worker.main import _evaluate
from packages.agent.advisory import AdvisoryModelClient
from packages.agent.config import load_advisory_model_catalog
from packages.contracts.agent_input import (
    AgentContextV1,
    AgentRequestV1,
    agent_request_from_strategy,
    sanitized_model_input,
)
from packages.contracts.canonical import canonical_hash, canonical_json


def _request() -> AgentRequestV1:
    _, _, _, _, context, _, pair = _evaluate("regime_momentum", momentum=True)
    evaluation, _ = pair
    return agent_request_from_strategy(context, evaluation)


def _profile(model_id: str):
    return load_advisory_model_catalog(
        Path(__file__).parents[2] / "configs" / "advisory_models.yaml"
    ).select(model_id)


def _valid_response() -> str:
    return json.dumps(
        {
            "recommendation": "ALLOW_UNCHANGED",
            "diagnostic_confidence": "0.62",
            "reason_code": "AGENT_CONTEXT_ALIGNED",
            "narrative": {
                "market_thesis": "The normalized continuation features remain aligned.",
                "counter_thesis": "Feature quality and indicative liquidity can change before submission.",
                "explanation": "The advisory response preserves the deterministic semantic request.",
            },
        }
    )


def test_gemini_3_6_interactions_adapter_sends_only_sanitized_input_and_freezes_a_thesis() -> None:
    request = _request()

    def handler(http_request: httpx.Request) -> httpx.Response:
        assert str(http_request.url) == "https://generativelanguage.googleapis.com/v1beta/interactions"
        assert http_request.headers["x-goog-api-key"] == "test-token"
        body = json.loads(http_request.content)
        assert body["model"] == "gemini-3.6-flash"
        assert body["store"] is False
        assert "tools" not in body
        assert body["generation_config"]["thinking_level"] == "minimal"
        assert body["response_format"]["mime_type"] == "application/json"
        model_text = body["input"]
        assert "account_id" not in model_text
        assert "buying_power" not in model_text
        assert "client_order_id" not in model_text
        return httpx.Response(
            200,
            json={
                "id": "interaction-test",
                "status": "completed",
                "model": "gemini-3.6-flash",
                "steps": [
                    {"type": "thought", "content": [{"type": "text", "text": "hidden"}]},
                    {
                        "type": "model_output",
                        "content": [{"type": "text", "text": _valid_response()}],
                    },
                ],
            },
        )

    client = AdvisoryModelClient(
        profile=_profile("gemini_3_6_flash"),
        api_key="test-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    thesis = client.create_thesis(request)

    assert thesis.recommendation == "ALLOW_UNCHANGED"
    assert thesis.context_hash == request.context.context_hash
    assert thesis.strategy_evaluation_hash == request.evaluation.evaluation_hash
    assert thesis.model_input_hash == canonical_hash(sanitized_model_input(request))
    assert thesis.narrative is not None
    assert "profile=sha256:" in thesis.model_version


def test_legacy_gemini_profile_remains_explicitly_pinned_to_generate_content() -> None:
    request = _request()

    def handler(http_request: httpx.Request) -> httpx.Response:
        assert str(http_request.url) == (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-2.5-flash-lite:generateContent"
        )
        return httpx.Response(
            200,
            json={
                "modelVersion": "gemini-2.5-flash-lite-001",
                "candidates": [{"content": {"parts": [{"text": _valid_response()}]}}],
            },
        )

    client = AdvisoryModelClient(
        profile=_profile("gemini_2_5_flash_lite"),
        api_key="test-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    thesis = client.create_thesis(request)

    assert thesis.recommendation == "ALLOW_UNCHANGED"


def test_deepseek_adapter_disables_thinking_and_fails_closed_on_invalid_json() -> None:
    request = _request()

    def handler(http_request: httpx.Request) -> httpx.Response:
        assert str(http_request.url) == "https://api.deepseek.com/chat/completions"
        assert http_request.headers["authorization"] == "Bearer test-token"
        body = json.loads(http_request.content)
        assert body["thinking"] == {"type": "disabled"}
        return httpx.Response(200, json={"model": "deepseek-v4-flash", "choices": [{"message": {"content": "{}"}}]})

    client = AdvisoryModelClient(
        profile=_profile("deepseek_v4_flash"),
        api_key="test-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    thesis = client.create_thesis(request)

    assert thesis.recommendation == "VETO"
    assert thesis.reason_code == "AGENT_OUTPUT_INVALID"
    assert thesis.diagnostic_confidence == 0


def test_sanitized_input_excludes_execution_and_account_state() -> None:
    rendered = canonical_json(sanitized_model_input(_request()))
    for forbidden in (
        "account_id",
        "buying_power",
        "client_order_id",
        "maximum_loss",
        "option_surface_summaries",
    ):
        assert forbidden not in rendered


def test_advisory_request_contract_has_no_option_surface_or_position_fields() -> None:
    assert {"option_surface_summaries", "logical_positions"}.isdisjoint(AgentContextV1.model_fields)


def test_advisory_request_rejects_a_price_surface_field() -> None:
    payload = _request().model_dump(mode="json")
    payload["context"]["option_surface_summaries"] = {"spread_mid": "1.25"}
    with pytest.raises(ValidationError):
        AgentRequestV1.model_validate(payload)
