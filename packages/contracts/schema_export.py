"""Generate committed JSON Schema snapshots for every public V1 model."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from .models import (
    AccountSnapshotV1,
    AgentNarrativeV1,
    AgentThesisV1,
    ArmCommandV1,
    ArtifactRefV1,
    BrokerEventV1,
    ControlCommandV1,
    ControlStateV1,
    DailyEconomicContextV1,
    DataRequirementsV1,
    DecisionJobV1,
    EconomicAssessmentV1,
    EconomicMarketObservationV1,
    EconomicNewsHeadlineV1,
    EntryTemplateRequestV1,
    EventEnvelopeV1,
    ExecuteApprovedPlanV1,
    ExecuteReduceOnlyPlanV1,
    ExecutionBundleV1,
    ExecutionDeploymentV1,
    ExecutionPreflightDecisionV1,
    FeatureVectorV1,
    FeedIdentityV1,
    HaltCommandV1,
    IntentTupleV1,
    ManagedPositionV1,
    MarketClockV1,
    MarketSnapshotV1,
    NoTradeV1,
    OptionContractV1,
    OrderLegV1,
    OrderPlanV1,
    OrderRiskSnapshotV1,
    PositionDirectiveV1,
    PositionLegV1,
    PositionMarketStateV1,
    PositionSnapshotV1,
    QuoteV1,
    ReduceOnlyDecisionV1,
    ReduceOnlyOrderPlanV1,
    RiskDecisionV1,
    RiskInputV1,
    RiskPolicyV1,
    RiskReservationV1,
    RunManifestV1,
    SignalDecisionAuditV1,
    StrategyConfigV1,
    StrategyContextV1,
    StrategyEvaluationV1,
    StrategyMetadataV1,
    StrategyStateV1,
    TradeIntentV1,
)

MODELS: tuple[type[BaseModel], ...] = (
    AccountSnapshotV1,
    AgentNarrativeV1,
    AgentThesisV1,
    ArmCommandV1,
    ArtifactRefV1,
    BrokerEventV1,
    ControlCommandV1,
    ControlStateV1,
    DataRequirementsV1,
    DecisionJobV1,
    DailyEconomicContextV1,
    EconomicAssessmentV1,
    EconomicMarketObservationV1,
    EconomicNewsHeadlineV1,
    EntryTemplateRequestV1,
    EventEnvelopeV1,
    ExecuteApprovedPlanV1,
    ExecuteReduceOnlyPlanV1,
    ExecutionBundleV1,
    ExecutionDeploymentV1,
    ExecutionPreflightDecisionV1,
    FeatureVectorV1,
    FeedIdentityV1,
    HaltCommandV1,
    IntentTupleV1,
    ManagedPositionV1,
    MarketClockV1,
    MarketSnapshotV1,
    NoTradeV1,
    OptionContractV1,
    OrderLegV1,
    OrderPlanV1,
    OrderRiskSnapshotV1,
    PositionDirectiveV1,
    PositionLegV1,
    PositionMarketStateV1,
    PositionSnapshotV1,
    QuoteV1,
    ReduceOnlyDecisionV1,
    ReduceOnlyOrderPlanV1,
    RiskDecisionV1,
    RiskInputV1,
    RiskPolicyV1,
    RiskReservationV1,
    RunManifestV1,
    SignalDecisionAuditV1,
    StrategyConfigV1,
    StrategyContextV1,
    StrategyEvaluationV1,
    StrategyMetadataV1,
    StrategyStateV1,
    TradeIntentV1,
)


def schema_filename(model: type[BaseModel]) -> str:
    return f"{model.__name__}.json"


def export(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for model in MODELS:
        (destination / schema_filename(model)).write_text(
            json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def main() -> None:
    export(Path("schemas/v1"))


if __name__ == "__main__":
    main()
