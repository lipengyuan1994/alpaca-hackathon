"""Canonical binding for the latest credential-zone broker reconciliation."""

from packages.contracts.canonical import canonical_hash
from packages.contracts.models import AccountSnapshotV1, OrderRiskSnapshotV1, PositionSnapshotV1


def reconciliation_hash(
    account: AccountSnapshotV1,
    positions: PositionSnapshotV1,
    order_risk: OrderRiskSnapshotV1,
) -> str:
    return canonical_hash(
        {
            "account_snapshot": account.content_hash,
            "position_snapshot": positions.content_hash,
            "order_risk_snapshot": order_risk.content_hash,
        }
    )
