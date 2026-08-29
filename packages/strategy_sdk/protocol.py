"""The only API a V1 strategy implementation receives."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from packages.contracts.models import (
    DataRequirementsV1,
    StrategyConfigV1,
    StrategyContextV1,
    StrategyEvaluationV1,
    StrategyMetadataV1,
)


@runtime_checkable
class StrategyPluginV1(Protocol):
    @property
    def metadata(self) -> StrategyMetadataV1: ...

    def data_requirements(self, config: StrategyConfigV1) -> DataRequirementsV1: ...

    def evaluate(self, context: StrategyContextV1, config: StrategyConfigV1) -> StrategyEvaluationV1: ...
