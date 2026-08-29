from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from packages.contracts.canonical import canonical_hash
from packages.contracts.models import ControlCommandV1, ControlStateV1, OperatingModeV1
from packages.domain import ControlError, apply_control_command


def test_control_command_is_single_use_and_compare_and_swap() -> None:
    now = datetime(2026, 8, 31, 14, 15, tzinfo=UTC)
    hashes = {name: canonical_hash({name: "fixture"}) for name in ("release", "config", "allowlist", "reconciliation")}
    state = ControlStateV1(
        account_id="paper-fixture-account",
        version=3,
        release_hash=hashes["release"],
        config_hash=hashes["config"],
        account_allowlist_hash=hashes["allowlist"],
        reconciliation_hash=hashes["reconciliation"],
        reconciled_at=now,
    )
    command = ControlCommandV1(
        nonce=uuid4(),
        issued_at=now,
        expires_at=now + timedelta(seconds=30),
        operator_id="operator",
        expected_mode=OperatingModeV1.DISARMED,
        expected_version=3,
        target_mode=OperatingModeV1.PAPER_DEMO_ARMED,
        account_id=state.account_id,
        account_allowlist_hash=state.account_allowlist_hash,
        release_hash=state.release_hash,
        config_hash=state.config_hash,
        reconciliation_hash=state.reconciliation_hash,
        reason_code="OPERATOR_ARM_DEMO",
    )
    used: set[str] = set()
    armed = apply_control_command(
        state, command, now=now, used_nonces=used, account_is_flat=True, no_working_or_unknown_orders=True
    )
    assert armed.mode == OperatingModeV1.PAPER_DEMO_ARMED
    with pytest.raises(ControlError, match="CONTROL_NONCE_REPLAYED"):
        apply_control_command(
            state, command, now=now, used_nonces=used, account_is_flat=True, no_working_or_unknown_orders=True
        )
