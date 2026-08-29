"""Fail-closed state transitions for the private operator procedure."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from packages.contracts.models import ControlCommandV1, ControlStateV1, OperatingModeV1


class ControlError(ValueError):
    """A stable control procedure rejection; callers must not bypass it."""


_ARMABLE = {OperatingModeV1.PAPER_ARMED, OperatingModeV1.PAPER_DEMO_ARMED}


def apply_control_command(
    state: ControlStateV1,
    command: ControlCommandV1,
    *,
    now: datetime,
    used_nonces: set[str],
    account_is_flat: bool,
    no_working_or_unknown_orders: bool,
) -> ControlStateV1:
    """Apply one compare-and-swap control command without side effects on refusal."""
    now = now.astimezone(UTC)
    nonce = str(command.nonce)
    if nonce in used_nonces:
        raise ControlError("CONTROL_NONCE_REPLAYED")
    if now > command.expires_at:
        raise ControlError("CONTROL_COMMAND_EXPIRED")
    if command.account_id != state.account_id:
        raise ControlError("CONTROL_ACCOUNT_MISMATCH")
    if command.expected_mode != state.mode or command.expected_version != state.version:
        raise ControlError("CONTROL_CAS_CONFLICT")
    for name in ("account_allowlist_hash", "release_hash", "config_hash", "reconciliation_hash"):
        if getattr(command, name) != getattr(state, name):
            raise ControlError(f"CONTROL_{name.removesuffix('_hash').upper()}_MISMATCH")
    if now - state.reconciled_at > timedelta(seconds=15):
        raise ControlError("CONTROL_RECONCILIATION_STALE")

    target = command.target_mode
    if target in _ARMABLE:
        if state.mode not in {OperatingModeV1.DISARMED, OperatingModeV1.REPLAY, OperatingModeV1.SHADOW}:
            raise ControlError("CONTROL_ILLEGAL_ARM_TRANSITION")
        if not account_is_flat or not no_working_or_unknown_orders:
            raise ControlError("CONTROL_ACCOUNT_NOT_FLAT")
    elif target == OperatingModeV1.FLATTENING:
        if state.mode not in set(OperatingModeV1):
            raise ControlError("CONTROL_ILLEGAL_TRANSITION")
    elif target == OperatingModeV1.HALTED:
        if state.mode not in {OperatingModeV1.FLATTENING, OperatingModeV1.HALTED, OperatingModeV1.DISARMED}:
            raise ControlError("CONTROL_ILLEGAL_HALT_TRANSITION")
        if not account_is_flat or not no_working_or_unknown_orders:
            raise ControlError("CONTROL_EXPOSURE_REQUIRES_FLATTENING")
    else:
        raise ControlError("CONTROL_TARGET_NOT_PERMITTED")

    used_nonces.add(nonce)
    material = state.model_dump(exclude={"content_hash"})
    material.update({"mode": target, "version": state.version + 1})
    return ControlStateV1.model_validate(material)
