"""Narrow interface that prevents generic MCP tool access from entering the system."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from packages.contracts.models import (
    AccountSnapshotV1,
    BrokerEventV1,
    MarketSnapshotV1,
    OrderPlanV1,
    OrderRiskSnapshotV1,
    PositionSnapshotV1,
    ReduceOnlyOrderPlanV1,
)


class PaperEndpointError(ValueError):
    pass


class AlpacaExecutionPort(Protocol):
    def submit(
        self,
        plan: OrderPlanV1 | ReduceOnlyOrderPlanV1,
        *,
        now: datetime,
    ) -> BrokerEventV1: ...

    def reconcile(self, client_order_id: str, *, now: datetime) -> BrokerEventV1 | None: ...

    def cancel(self, client_order_id: str, *, now: datetime) -> BrokerEventV1: ...

    def runtime_state_violations(
        self,
        *,
        account: AccountSnapshotV1,
        positions: PositionSnapshotV1,
        order_risk: OrderRiskSnapshotV1,
        market: MarketSnapshotV1,
        now: datetime,
        quote_ttl_seconds: int,
    ) -> tuple[str, ...]: ...
