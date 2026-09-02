from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from packages.contracts.canonical import canonical_hash
from packages.paper_wheel.public_snapshot import PublicSnapshotError, build_snapshot


def _account() -> dict[str, str]:
    return {
        "id": "paper-account-id",
        "status": "ACTIVE",
        "equity": "100079.73",
        "last_equity": "99929.73",
        "cash": "100375.73",
        "buying_power": "119902.92",
    }


def _order(index: int, *, prefix: str = "rs-v135", filled: bool = True) -> dict[str, str]:
    filled_at = datetime(2026, 9, 1, 15, tzinfo=UTC) + timedelta(minutes=index)
    return {
        "client_order_id": f"{prefix}-{'a' * 24}{index:08d}",
        "symbol": "QQQ260908P00704000",
        "side": "sell",
        "position_intent": "sell_to_open",
        "filled_qty": "1" if filled else "0",
        "filled_avg_price": "2.78" if filled else None,
        "filled_at": filled_at.isoformat(),
    }


def test_public_snapshot_filters_sorts_limits_and_hashes_orders() -> None:
    orders = [_order(index) for index in range(12)]
    orders.extend([_order(99, prefix="manual"), _order(98, filled=False)])

    snapshot = build_snapshot(
        account=_account(),
        orders=orders,
        expected_account_id="paper-account-id",
        generated_at=datetime(2026, 9, 2, 21, 31, tzinfo=UTC),
        baseline=Decimal("100000"),
    )

    assert snapshot["schema_version"] == "stable-income-generator-live-paper/v2"
    assert snapshot["refresh_contract"]["publishing_window"] == {
        "timezone": "America/New_York",
        "weekdays": ["MON", "TUE", "WED", "THU", "FRI"],
        "start": "09:00",
        "final_run": "17:00",
    }
    assert snapshot["account"]["total_pnl"] == 79.73
    assert snapshot["account"]["day_pnl"] == 150.0
    assert len(snapshot["recent_filled_system_orders"]) == 10
    assert snapshot["recent_filled_system_orders"][0]["system_ref"] == "00000011"
    assert snapshot["recent_filled_system_orders"][-1]["system_ref"] == "00000002"
    contract = snapshot["recent_filled_system_orders"][0]["contract"]
    assert contract == {
        "symbol": "QQQ260908P00704000",
        "underlying": "QQQ",
        "expiry": "2026-09-08",
        "option_type": "PUT",
        "strike": 704.0,
    }
    artifact_hash = snapshot.pop("artifact_hash")
    assert artifact_hash == canonical_hash(snapshot)


def test_public_snapshot_rejects_an_unexpected_account() -> None:
    with pytest.raises(PublicSnapshotError, match="PUBLIC_SNAPSHOT_ACCOUNT_MISMATCH"):
        build_snapshot(
            account=_account(),
            orders=[],
            expected_account_id="different-account",
            generated_at=datetime(2026, 9, 2, tzinfo=UTC),
        )
