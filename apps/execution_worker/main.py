"""Execution role entrypoint; it only receives immutable approved commands."""

from __future__ import annotations

import platform
from datetime import datetime

from packages.contracts.models import (
    AccountSnapshotV1,
    ExecuteApprovedPlanV1,
    MarketSnapshotV1,
    OrderRiskSnapshotV1,
    PositionSnapshotV1,
    RiskInputV1,
)
from packages.execution_core import ExecutionResult, FakeBroker, preflight_and_submit


def process_approved_command(
    command: ExecuteApprovedPlanV1,
    risk_input: RiskInputV1,
    market: MarketSnapshotV1,
    account: AccountSnapshotV1,
    positions: PositionSnapshotV1,
    order_risk: OrderRiskSnapshotV1,
    *,
    broker: FakeBroker,
    now: datetime,
    paper_hostname: str,
    expected_account_id: str,
) -> ExecutionResult:
    """The sole mutation entry point, preceded by independent preflight."""
    return preflight_and_submit(
        command,
        risk_input,
        market,
        account,
        positions,
        order_risk,
        broker,
        now=now,
        paper_hostname=paper_hostname,
        expected_account_id=expected_account_id,
    )


def main() -> None:
    if platform.machine() != "arm64":
        raise RuntimeError("LOCAL_RUNTIME_MUST_BE_ARM64")
    raise SystemExit(
        "execution-worker needs a private deployment configuration and approved outbox command; "
        "use paper-decision-worker --approved for the credential-free fake-broker fixture"
    )


if __name__ == "__main__":
    main()
