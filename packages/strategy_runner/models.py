"""Private canonical-JSON messages for one authorized plug-in invocation."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.contracts.models import (
    DataRequirementsV1,
    IntentTupleV1,
    StrategyConfigV1,
    StrategyContextV1,
    StrategyEvaluationV1,
    StrategyMetadataV1,
)


class RunnerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PluginAuthorization(RunnerModel):
    registry_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    entrypoint: str
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    expected_metadata: StrategyMetadataV1
    expected_data_requirements: DataRequirementsV1
    config_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    allowed_underlyings: tuple[str, ...] = Field(min_length=1)
    allowed_intent_tuples: tuple[IntentTupleV1, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _coherent_authority(self) -> "PluginAuthorization":
        if self.expected_data_requirements.underlyings != self.allowed_underlyings:
            raise ValueError("authorized data requirements and underlyings differ")
        keys = [
            (item.template_id, item.horizon_bucket, item.risk_tier, item.max_intent_ttl_seconds)
            for item in self.allowed_intent_tuples
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("authorized intent tuples are duplicated")
        return self


class PluginRequest(RunnerModel):
    authorization: PluginAuthorization
    context: StrategyContextV1
    config: StrategyConfigV1


class PluginResponse(RunnerModel):
    metadata: StrategyMetadataV1
    data_requirements: DataRequirementsV1
    evaluation: StrategyEvaluationV1
