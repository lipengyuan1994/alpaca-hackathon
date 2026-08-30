"""Pure, sanitized input contract shared by the agent service and resolver."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, model_validator

from .models import (
    FeedIdentityV1,
    StrategyContextV1,
    StrategyDecisionV1,
    StrategyEvaluationV1,
    StrictModel,
)


class AgentContextV1(StrictModel):
    """Allowlisted market/feature context sent to the advisory service."""

    evaluation_id: str
    as_of: datetime
    context_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    market_snapshot_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    feature_vector_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    feature_contract_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    feature_available_time: datetime
    feed_identity: FeedIdentityV1
    quality_flags: tuple[str, ...] = Field(default=(), max_length=32)
    universe_features: dict[str, Decimal] = Field(max_length=128)
    config_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _bounded_finite_features(self) -> "AgentContextV1":
        if self.feature_available_time > self.as_of:
            raise ValueError("agent context features are not available")
        if any(not value.is_finite() for value in self.universe_features.values()):
            raise ValueError("agent context features must be finite")
        if any(not key or len(key) > 128 for key in self.universe_features):
            raise ValueError("agent context feature key is invalid")
        return self


class AgentEvaluationV1(StrictModel):
    """Allowlisted semantic evaluation sent to the advisory service."""

    evaluation_id: str
    plugin_id: str
    plugin_version: str
    plugin_content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    context_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    config_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    decision: StrategyDecisionV1
    evaluation_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class AgentRequestV1(StrictModel):
    """Complete, bounded input surface for an advisory request."""

    schema_version: Literal["agent-request/v1"] = "agent-request/v1"
    context: AgentContextV1
    evaluation: AgentEvaluationV1

    @model_validator(mode="after")
    def _semantic_bindings_match(self) -> "AgentRequestV1":
        if self.context.evaluation_id != self.evaluation.evaluation_id:
            raise ValueError("agent request evaluation id mismatch")
        if self.context.context_hash != self.evaluation.context_hash:
            raise ValueError("agent request context hash mismatch")
        if self.context.config_hash != self.evaluation.config_hash:
            raise ValueError("agent request config hash mismatch")
        return self


def agent_request_from_strategy(
    context: StrategyContextV1, evaluation: StrategyEvaluationV1
) -> AgentRequestV1:
    """Project deterministic artifacts into the narrow advisory contract."""

    return AgentRequestV1(
        context=AgentContextV1(
            evaluation_id=context.evaluation_id,
            as_of=context.as_of,
            context_hash=context.context_hash,
            market_snapshot_hash=context.market_snapshot_hash,
            feature_vector_hash=context.feature_vector_hash,
            feature_contract_hash=context.feature_contract_hash,
            feature_available_time=context.feature_available_time,
            feed_identity=context.feed_identity,
            quality_flags=context.quality_flags,
            universe_features=context.universe_features,
            config_hash=context.config_hash,
        ),
        evaluation=AgentEvaluationV1(
            evaluation_id=evaluation.evaluation_id,
            plugin_id=evaluation.plugin_id,
            plugin_version=evaluation.plugin_version,
            plugin_content_hash=evaluation.plugin_content_hash,
            context_hash=evaluation.context_hash,
            config_hash=evaluation.config_hash,
            decision=evaluation.decision,
            evaluation_hash=evaluation.evaluation_hash,
        ),
    )


def sanitized_model_input(request: AgentRequestV1) -> dict[str, Any]:
    """Render the exact allowlisted model input and nothing else."""

    context = request.context
    evaluation = request.evaluation
    return {
        "schema_version": "agent-model-input/v1",
        "context": {
            "evaluation_id": context.evaluation_id,
            "as_of": context.as_of,
            "context_hash": context.context_hash,
            "market_snapshot_hash": context.market_snapshot_hash,
            "feature_vector_hash": context.feature_vector_hash,
            "feature_contract_hash": context.feature_contract_hash,
            "feature_available_time": context.feature_available_time,
            "feed_identity": context.feed_identity.model_dump(mode="json"),
            "quality_flags": list(context.quality_flags),
            "universe_features": context.universe_features,
            "config_hash": context.config_hash,
        },
        "strategy_evaluation": {
            "evaluation_id": evaluation.evaluation_id,
            "plugin_id": evaluation.plugin_id,
            "plugin_version": evaluation.plugin_version,
            "plugin_content_hash": evaluation.plugin_content_hash,
            "context_hash": evaluation.context_hash,
            "config_hash": evaluation.config_hash,
            "decision": evaluation.decision.model_dump(mode="json"),
            "evaluation_hash": evaluation.evaluation_hash,
        },
        "resolver_constraint": "ALLOW_UNCHANGED_OR_VETO_ONLY",
    }
