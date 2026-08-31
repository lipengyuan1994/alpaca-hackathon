"""Atomic state, hash-chain journal, and single-process lease for paper trading."""

from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from packages.contracts.canonical import canonical_json

from .models import WheelArmTokenV1, WheelJournalEventV1, WheelRuntimeStateV1


def _atomic_model(path: Path, model: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    payload = canonical_json(model) + "\n"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


class WheelStateStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.state_path = root / "state.json"
        self.arm_path = root / "control" / "arm.json"
        self.journal_path = root / "journal.jsonl"
        self.lock_path = root / "runtime.lock"

    @contextmanager
    def lease(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("WHEEL_RUNTIME_LEASE_BUSY") from exc
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def load_state(self, *, config_hash: str, now: datetime) -> WheelRuntimeStateV1:
        if not self.state_path.exists():
            return WheelRuntimeStateV1(config_hash=config_hash, last_run_at=now.astimezone(UTC))
        try:
            state = WheelRuntimeStateV1.model_validate_json(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError("WHEEL_RUNTIME_STATE_INVALID") from exc
        if state.config_hash != config_hash:
            raise RuntimeError("WHEEL_RUNTIME_CONFIG_HASH_CHANGED_REARM_REQUIRED")
        return state

    def save_state(self, state: WheelRuntimeStateV1) -> None:
        _atomic_model(self.state_path, state)

    def load_arm(self) -> WheelArmTokenV1 | None:
        if not self.arm_path.exists():
            return None
        try:
            return WheelArmTokenV1.model_validate_json(self.arm_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError("WHEEL_ARM_TOKEN_INVALID") from exc

    def save_arm(self, token: WheelArmTokenV1) -> None:
        _atomic_model(self.arm_path, token)

    def events(self) -> tuple[WheelJournalEventV1, ...]:
        if not self.journal_path.exists():
            return ()
        events: list[WheelJournalEventV1] = []
        previous: str | None = None
        try:
            lines = self.journal_path.read_text(encoding="utf-8").splitlines()
            for line in lines:
                if not line.strip():
                    continue
                event = WheelJournalEventV1.model_validate_json(line)
                if event.sequence != len(events) + 1 or event.previous_hash != previous:
                    raise ValueError("journal chain mismatch")
                events.append(event)
                previous = event.event_hash
        except (OSError, ValueError) as exc:
            raise RuntimeError("WHEEL_JOURNAL_INVALID") from exc
        return tuple(events)

    def append(
        self,
        *,
        event_type: str,
        occurred_at: datetime,
        client_order_id: str | None = None,
        plan_hash: str | None = None,
        detail: dict[str, object] | None = None,
    ) -> WheelJournalEventV1:
        events = self.events()
        event = WheelJournalEventV1(
            sequence=len(events) + 1,
            occurred_at=occurred_at.astimezone(UTC),
            event_type=event_type,
            client_order_id=client_order_id,
            plan_hash=plan_hash,
            detail={} if detail is None else detail,
            previous_hash=None if not events else events[-1].event_hash,
        )
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.journal_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
            handle.write(canonical_json(event) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(self.journal_path, 0o600)
        return event
