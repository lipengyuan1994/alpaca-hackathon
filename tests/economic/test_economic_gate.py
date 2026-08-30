from __future__ import annotations

from apps.decision_worker.main import run_economic_veto_fixture


def test_economic_veto_records_no_trade_before_order_planning() -> None:
    result = run_economic_veto_fixture()

    assert result.status == "NO_TRADE"
    assert result.details["reason_code"] == "ECONOMIC_VETO"
    event_types = [event["event_type"] for event in result.tape]
    assert event_types == [
        "MarketSnapshotRecordedV1",
        "FeatureVectorComputedV1",
        "StrategyDecisionProducedV1",
        "AgentThesisFrozenV1",
        "DailyEconomicContextBoundV1",
        "EconomicAssessmentFrozenV1",
        "NoTradeRecordedV1",
    ]
    assert "OrderPlanCreatedV1" not in event_types
    assert "RiskApprovedAndCapacityReservedV1" not in event_types
