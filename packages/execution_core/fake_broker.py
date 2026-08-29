"""Deterministic recorded broker simulator used by fixture/reconciliation tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from packages.contracts.models import (
    AccountSnapshotV1,
    BrokerEventV1,
    MarketSnapshotV1,
    OrderPlanV1,
    OrderRiskSnapshotV1,
    PositionSnapshotV1,
    ReduceOnlyOrderPlanV1,
)


@dataclass
class _Order:
    plan: OrderPlanV1 | ReduceOnlyOrderPlanV1
    status: str
    broker_order_id: str


class FakeBroker:
    def __init__(self) -> None:
        self._orders: dict[str, _Order] = {}
        self.submit_count = 0

    def runtime_state_violations(
        self,
        *,
        account: AccountSnapshotV1,
        positions: PositionSnapshotV1,
        order_risk: OrderRiskSnapshotV1,
        market: MarketSnapshotV1,
        now: datetime,
        quote_ttl_seconds: int,
    ) -> tuple[str, ...]:
        return ()

    def submit(
        self,
        plan: OrderPlanV1 | ReduceOnlyOrderPlanV1,
        *,
        now: datetime,
        outcome: str = "ACCEPTED",
    ) -> BrokerEventV1:
        now = now.astimezone(UTC)
        existing = self._orders.get(plan.client_order_id)
        if existing is not None:
            return BrokerEventV1(
                client_order_id=plan.client_order_id,
                status=existing.status,  # type: ignore[arg-type]
                occurred_at=now,
                broker_order_id=existing.broker_order_id,
                reason_code="BROKER_DEDUPLICATED",
            )
        if outcome not in {"ACCEPTED", "REJECTED", "UNKNOWN", "PARTIAL", "FILLED"}:
            raise ValueError("unsupported fake broker outcome")
        self.submit_count += 1
        broker_order_id = f"fake-{self.submit_count:08d}"
        self._orders[plan.client_order_id] = _Order(plan=plan, status=outcome, broker_order_id=broker_order_id)
        return BrokerEventV1(
            client_order_id=plan.client_order_id,
            status=outcome,  # type: ignore[arg-type]
            occurred_at=now,
            broker_order_id=broker_order_id,
            filled_quantity=plan.quantity if outcome == "FILLED" else 0,
            reason_code="FAKE_BROKER_OUTCOME",
        )

    def reconcile(self, client_order_id: str, *, now: datetime) -> BrokerEventV1 | None:
        order = self._orders.get(client_order_id)
        if order is None:
            return None
        return BrokerEventV1(
            client_order_id=client_order_id,
            status=order.status,  # type: ignore[arg-type]
            occurred_at=now.astimezone(UTC),
            broker_order_id=order.broker_order_id,
            filled_quantity=order.plan.quantity if order.status == "FILLED" else 0,
            reason_code="FAKE_RECONCILIATION",
        )

    def set_outcome(self, client_order_id: str, status: str) -> None:
        """Advance a recorded fake order for deterministic lifecycle tests."""
        if status not in {"ACCEPTED", "REJECTED", "PARTIAL", "FILLED", "CANCELLED", "EXPIRED"}:
            raise ValueError("unsupported fake broker outcome")
        order = self._orders.get(client_order_id)
        if order is None:
            raise ValueError("fake broker order not found")
        order.status = status

    def cancel(self, client_order_id: str, *, now: datetime) -> BrokerEventV1:
        order = self._orders.get(client_order_id)
        if order is None:
            return BrokerEventV1(
                client_order_id=client_order_id,
                status="UNKNOWN",
                occurred_at=now.astimezone(UTC),
                reason_code="FAKE_CANCEL_ORDER_NOT_FOUND",
            )
        order.status = "CANCELLED"
        return BrokerEventV1(
            client_order_id=client_order_id,
            status="CANCELLED",
            occurred_at=now.astimezone(UTC),
            broker_order_id=order.broker_order_id,
            reason_code="FAKE_CANCELLED",
        )
