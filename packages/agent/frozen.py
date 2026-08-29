"""Deterministic fixture thesis creation used in offline/replay demos."""

from __future__ import annotations

from datetime import timedelta

from packages.contracts.canonical import canonical_hash
from packages.contracts.models import (
    AgentNarrativeV1,
    AgentThesisV1,
    StrategyContextV1,
    StrategyEvaluationV1,
)


def fixture_thesis(
    context: StrategyContextV1,
    evaluation: StrategyEvaluationV1,
    *,
    veto: bool = False,
) -> AgentThesisV1:
    """Create a persisted fixture artifact, never a fresh model call during replay."""
    narrative = AgentNarrativeV1(
        market_thesis=(
            "Fixture momentum is aligned with the registered continuation signal."
            if not veto
            else "Fixture safety review declines the otherwise aligned continuation signal."
        ),
        counter_thesis=(
            "Indicative option data can be stale or insufficiently representative of executable liquidity."
        ),
        explanation=(
            "This frozen fixture is display-only advisory context. The resolver may preserve the "
            "strategy request unchanged or record a veto; it cannot use this prose for selection, "
            "sizing, pricing, risk, or execution."
        ),
    )
    raw = {
        "recommendation": "VETO" if veto else "ALLOW_UNCHANGED",
        "narrative": narrative,
        "fixture": True,
    }
    raw_hash = canonical_hash(raw)
    return AgentThesisV1(
        thesis_id=f"thesis-{evaluation.evaluation_hash.removeprefix('sha256:')[:24]}",
        context_hash=context.context_hash,
        strategy_evaluation_hash=evaluation.evaluation_hash,
        model_input_hash=canonical_hash({"context_hash": context.context_hash}),
        model_version="fixture-provider/v1",
        prompt_version="frozen/v1",
        raw_output_hash=raw_hash,
        recommendation="VETO" if veto else "ALLOW_UNCHANGED",
        diagnostic_confidence="0.50",
        expires_at=context.as_of + timedelta(seconds=300),
        reason_code="FIXTURE_VETO" if veto else "FIXTURE_ALLOW_UNCHANGED",
        narrative=narrative,
    )
