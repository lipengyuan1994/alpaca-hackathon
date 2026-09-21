"""Operator CLI for the isolated L11 runtime."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence

from packages.contracts.canonical import canonical_json

from .broker import AlpacaLiveBroker, BrokerError
from .config import load_config
from .runtime import LiveRuntime
from .state import LiveState


def _now(text: str | None) -> datetime:
    if text is None:
        return datetime.now(UTC)
    value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("--at_MUST_BE_TIMEZONE_AWARE")
    return value.astimezone(UTC)


def _runtime(path: Path) -> LiveRuntime:
    config = load_config(path)
    broker = AlpacaLiveBroker.from_environment(account_id=config.account_id, secrets_root=config.secrets_root)
    return LiveRuntime(config=config, broker=broker, state=LiveState(config.state_path))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="L11 TQQQ/SOXL live runtime")
    parser.add_argument("action", choices=("status", "preflight", "reconcile", "run-once", "arm-live", "pause-buys", "resume-buys"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--reason", default="operator requested")
    parser.add_argument("--at")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.action == "status":
            state = LiveState(config.state_path)
            print(canonical_json({"config_hash": config.config_hash, "activation": state.activation(), "buys_paused": state.get_meta("buys_paused") == "1", "open_orders": state.open_orders()}))
            return 0
        runtime = _runtime(args.config)
        now = _now(args.at)
        if args.action == "preflight":
            result = runtime.preflight(now=now)
        elif args.action == "reconcile":
            result = runtime.reconcile(now=now)
        elif args.action == "run-once":
            result = runtime.run_once(now=now)
        elif args.action == "arm-live":
            if not runtime.config.enabled_file.is_file() or runtime.config.enabled_file.read_text(encoding="utf-8").strip() != "1":
                raise RuntimeError("ETF_LIVE_HOST_ENABLE_FILE_MISSING")
            result = {"status": "LIVE_ARMED", "token_hash": runtime.arm_live(now=now, operator_reason=args.reason)}
        elif args.action == "pause-buys":
            runtime.pause_buys(now=now, reason=args.reason)
            result = {"status": "BUYS_PAUSED"}
        else:
            runtime.resume_buys(now=now, reason=args.reason)
            result = {"status": "BUYS_RESUMED"}
        print(canonical_json(result))
        return 0
    except (BrokerError, RuntimeError, ValueError, OSError) as exc:
        print(canonical_json({"status": "BLOCKED", "reason_codes": [str(exc)]}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
