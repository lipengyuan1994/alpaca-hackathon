"""Narrow interface that prevents generic MCP tool access from entering the system."""

from __future__ import annotations

from typing import Protocol

from packages.contracts.models import BrokerEventV1, OrderPlanV1


class PaperEndpointError(ValueError):
    pass


class AlpacaExecutionPort(Protocol):
    def submit_approved_plan(self, plan: OrderPlanV1) -> BrokerEventV1: ...

    def reconcile_client_order(self, client_order_id: str) -> BrokerEventV1 | None: ...

    def cancel_by_client_order_id(self, client_order_id: str) -> BrokerEventV1: ...
