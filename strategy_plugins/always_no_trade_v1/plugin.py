"""Repository-owned reference plug-in with no dependency beyond the public SDK."""

from __future__ import annotations

from packages.contracts.models import (
    DataRequirementsV1,
    NoTradeV1,
    StrategyConfigV1,
    StrategyContextV1,
    StrategyEvaluationV1,
    StrategyMetadataV1,
)
from packages.strategy_sdk import UNBOUND_PLUGIN_CONTENT_HASH

FEATURE_CONTRACT_HASH = "sha256:3e3cfa8a1047dda69b0c829d0b1153f9258f4356c23ffb1def6a8601ff3445bc"


class Plugin:
    @property
    def metadata(self) -> StrategyMetadataV1:
        return StrategyMetadataV1(
            plugin_id="always_no_trade",
            plugin_version="1.0.0",
            owner="platform",
            economic_hypothesis_id="safe-refusal",
        )

    def data_requirements(self, config: StrategyConfigV1) -> DataRequirementsV1:
        return DataRequirementsV1(
            underlyings=("SPY",),
            feature_contract_hash=FEATURE_CONTRACT_HASH,
            required_feature_keys=(),
            maximum_observation_age_seconds=60,
        )

    def evaluate(self, context: StrategyContextV1, config: StrategyConfigV1) -> StrategyEvaluationV1:
        next_state = context.prior_state.__class__(
            plugin_id=context.prior_state.plugin_id,
            plugin_version=context.prior_state.plugin_version,
            as_of=context.as_of,
            sequence=context.prior_state.sequence + 1,
            payload=context.prior_state.payload,
        )
        return StrategyEvaluationV1(
            evaluation_id=context.evaluation_id,
            plugin_id=self.metadata.plugin_id,
            plugin_version=self.metadata.plugin_version,
            plugin_content_hash=UNBOUND_PLUGIN_CONTENT_HASH,
            context_hash=context.context_hash,
            config_hash=context.config_hash,
            decision=NoTradeV1(primary_reason_code="REFERENCE_NO_TRADE"),
            next_state=next_state,
        )
