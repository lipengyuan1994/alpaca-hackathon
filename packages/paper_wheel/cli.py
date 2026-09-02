"""Operator and scheduler CLI for the deterministic V13.5 paper-wheel runtime."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence

from apps.common import assert_native_developer_runtime
from packages.contracts.canonical import canonical_json

from .broker import AlpacaPaperWheelBroker, PaperBrokerError
from .config import load_config
from .runtime import PaperWheelRuntime


def _project_root(path: Path) -> Path:
    current = path.resolve().parent
    while current != current.parent:
        if (current / "pyproject.toml").is_file():
            return current
        current = current.parent
    raise SystemExit("WHEEL_PROJECT_ROOT_NOT_FOUND")


def _at(value: str | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise SystemExit("--at must include a UTC offset")
    return parsed.astimezone(UTC)


def _runtime(config_path: Path) -> PaperWheelRuntime:
    loaded = load_config(config_path)
    broker = AlpacaPaperWheelBroker.from_environment()
    return PaperWheelRuntime(loaded=loaded, broker=broker, project_root=_project_root(config_path))


def main(argv: Sequence[str] | None = None) -> int:
    assert_native_developer_runtime()
    parser = argparse.ArgumentParser(description="Paper-only V13.5 wheel runtime")
    actions = parser.add_subparsers(dest="action", required=True)
    for name in ("preflight", "verify-arm", "run-once", "status"):
        command = actions.add_parser(name)
        command.add_argument("--config", required=True, type=Path)
        if name != "status":
            command.add_argument("--at", help="timezone-aware test/diagnostic timestamp")
    arm = actions.add_parser("arm")
    arm.add_argument("--config", required=True, type=Path)
    arm.add_argument("--reason", required=True)
    arm.add_argument("--at", help="timezone-aware operator timestamp")
    halt = actions.add_parser("halt")
    halt.add_argument("--config", required=True, type=Path)
    halt.add_argument("--reason", required=True)
    halt.add_argument("--at", help="timezone-aware operator timestamp")
    migrate = actions.add_parser("migrate-config")
    migrate.add_argument("--config", required=True, type=Path)
    migrate.add_argument("--expected-current-config-hash", required=True)
    migrate.add_argument("--reason", required=True)
    migrate.add_argument("--at", help="timezone-aware operator timestamp")
    args = parser.parse_args(argv)
    try:
        if args.action == "status":
            loaded = load_config(args.config)
            root = _project_root(args.config) / loaded.config.runtime.runtime_root
            from .state import WheelStateStore

            store = WheelStateStore(root)
            state = store.load_state(config_hash=loaded.config_hash, now=datetime.now(UTC))
            arm_token = store.load_arm()
            print(
                canonical_json(
                    {
                        "status": state.status,
                        "config_hash": loaded.config_hash,
                        "state_hash": state.state_hash,
                        "sequence": state.sequence,
                        "last_run_at": state.last_run_at,
                        "arm_token_hash": None if arm_token is None else arm_token.token_hash,
                        "arm_expires_at": None if arm_token is None else arm_token.expires_at,
                    }
                )
            )
            return 0
        runtime = _runtime(args.config)
        now = _at(args.at)
        if args.action == "preflight":
            outcome = runtime.preflight(now=now)
        elif args.action == "verify-arm":
            outcome = runtime.scheduled_arm_preflight(now=now)
        elif args.action == "run-once":
            outcome = runtime.run_once(now=now)
        elif args.action == "arm":
            token = runtime.create_arm(now=now, operator_reason=args.reason)
            print(
                canonical_json(
                    {
                        "status": "PAPER_ARM_CREATED",
                        "config_hash": token.config_hash,
                        "account_id_hash": token.account_id_hash,
                        "valid_from": token.valid_from,
                        "expires_at": token.expires_at,
                        "token_hash": token.token_hash,
                    }
                )
            )
            return 0
        elif args.action == "migrate-config":
            outcome = runtime.migrate_config(
                now=now,
                expected_current_config_hash=args.expected_current_config_hash,
                operator_reason=args.reason,
            )
        else:
            outcome = runtime.operator_halt(now=now, reason=args.reason)
        print(canonical_json(outcome.public_dict()))
        return (
            0
            if outcome.status
            not in {
                "HALTED",
                "BLOCKED",
                "BROKER_UNAVAILABLE",
                "PREFLIGHT_BLOCKED",
                "PAPER_ARM_SCHEDULE_BLOCKED",
            }
            else 2
        )
    except (PaperBrokerError, RuntimeError, ValueError) as exc:
        print(canonical_json({"status": "BLOCKED", "reason_codes": [str(exc)]}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
