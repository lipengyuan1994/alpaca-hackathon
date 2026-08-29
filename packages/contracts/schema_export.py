"""Generate committed JSON Schema snapshots for the V1 public contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Type

from pydantic import BaseModel

from .models import (
    AccountSnapshotV1,
    AgentThesisV1,
    ArmCommandV1,
    EventEnvelopeV1,
    ExecuteApprovedPlanV1,
    HaltCommandV1,
    MarketSnapshotV1,
    OrderPlanV1,
    RiskDecisionV1,
    RiskInputV1,
    RunManifestV1,
    StrategyContextV1,
    StrategyEvaluationV1,
)

MODELS: tuple[Type[BaseModel], ...] = (
    MarketSnapshotV1,
    AccountSnapshotV1,
    StrategyContextV1,
    StrategyEvaluationV1,
    AgentThesisV1,
    OrderPlanV1,
    RiskInputV1,
    RiskDecisionV1,
    ExecuteApprovedPlanV1,
    EventEnvelopeV1,
    ArmCommandV1,
    HaltCommandV1,
    RunManifestV1,
)


def export(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for model in MODELS:
        name = model.__name__.removesuffix("V1") + "V1.json"
        (destination / name).write_text(
            json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


def main() -> None:
    export(Path("schemas/v1"))


if __name__ == "__main__":
    main()
