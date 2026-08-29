"""Tiny deterministic candidate used only by committed fixtures."""

from __future__ import annotations

from datetime import timedelta

from packages.contracts.models import (
    ArtifactRefV1,
    DataRequirementsV1,
    EntryTemplateRequestV1,
    NoTradeV1,
    StrategyConfigV1,
    StrategyContextV1,
    StrategyEvaluationV1,
    StrategyMetadataV1,
)


class Plugin:
    @property
    def metadata(self) -> StrategyMetadataV1:
        return StrategyMetadataV1(
            plugin_id="regime_momentum",
            plugin_version="1.0.0",
            owner="research",
            economic_hypothesis_id="H1_NORMALIZED_INTRADAY_CONTINUATION",
        )

    def data_requirements(self, config: StrategyConfigV1) -> DataRequirementsV1:
        return DataRequirementsV1(underlyings=("SPY", "QQQ"), maximum_observation_age_seconds=60)

    def evaluate(self, context: StrategyContextV1, config: StrategyConfigV1) -> StrategyEvaluationV1:
        next_state = context.prior_state.__class__(
            plugin_id=context.prior_state.plugin_id,
            plugin_version=context.prior_state.plugin_version,
            as_of=context.as_of,
            sequence=context.prior_state.sequence + 1,
            payload=context.prior_state.payload,
        )
        if context.quality_flags or context.universe_features.get("momentum_z", 0) < 1:
            decision = NoTradeV1(primary_reason_code="MOMENTUM_GATE_NOT_MET")
        else:
            tuple_ = next(
                (
                    allowed
                    for allowed in context.allowed_intent_tuples
                    if allowed.template_id == "CALL_DEBIT_SPREAD_V1"
                ),
                None,
            )
            if tuple_ is None:
                decision = NoTradeV1(primary_reason_code="TEMPLATE_NOT_ALLOWED")
            else:
                decision = EntryTemplateRequestV1(
                    underlying="SPY",
                    template_id=tuple_.template_id,
                    horizon_bucket=tuple_.horizon_bucket,
                    risk_tier=tuple_.risk_tier,
                    signal_strength_bucket="MEDIUM",
                    intent_expires_at=context.as_of + timedelta(seconds=tuple_.max_intent_ttl_seconds),
                    entry_reason_codes=("TREND_VWAP_ALIGNED",),
                    evidence_refs=(
                        ArtifactRefV1(
                            artifact_type="FEATURE_VECTOR",
                            content_hash=context.feature_vector_hash,
                            record_id=context.feature_vector_id,
                        ),
                    ),
                )
        return StrategyEvaluationV1(
            evaluation_id=context.evaluation_id,
            plugin_id=self.metadata.plugin_id,
            plugin_version=self.metadata.plugin_version,
            plugin_content_hash="sha256:" + "1" * 64,
            context_hash=context.context_hash,
            config_hash=context.config_hash,
            decision=decision,
            next_state=next_state,
        )
