#!/usr/bin/env python3
"""Validate the SignalQuarry feed bundle and stage O's legacy renderer data.

This adapter intentionally uses only the Python standard library. It accepts
the exact versioned public bundle, verifies both hashes and their shared
identity, and writes only the allowlisted O-compatible projection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_BUNDLE_BYTES = 5 * 1024 * 1024
SNAPSHOT_KEYS = {
    "schema_version", "deployment_alias", "strategy_version", "evidence_mode",
    "account_history_epoch", "broker_observed_at", "captured_at", "account",
    "pnl", "equity_history", "drawdown_sampling", "positions", "recent_fills",
    "external_cash_flows", "availability", "limitations", "snapshot_hash",
}
ACCOUNT_KEYS = {"equity", "cash", "buying_power", "status"}
METRIC_KEYS = {"status", "amount", "return_value", "attribution", "label"}
POINT_KEYS = {"timestamp", "equity", "drawdown"}
POSITION_KEYS = {"symbol", "quantity", "market_value", "unrealized_pnl"}
FILL_KEYS = {"filled_at", "action", "instrument", "quantity", "average_fill_price"}
FLOW_KEYS = {"occurred_at", "direction", "amount"}
AVAILABILITY_KEYS = {
    "equity_history", "recent_fills", "positions", "external_cash_flows",
    "realized_pnl", "source_feed",
}
COMPAT_KEYS = {
    "schema_version", "source", "generated_at", "signalquarry_snapshot_hash",
    "refresh_contract", "account", "strategy", "recent_filled_system_orders",
    "portfolio_history", "publication_scope", "artifact_hash",
}
COMPAT_ACCOUNT_KEYS = {
    "deployment_alias", "status", "equity", "cash", "buying_power",
    "starting_baseline", "total_pnl", "total_return", "day_start_equity",
    "day_pnl", "day_return",
}


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _exact_fields(value: Any, expected: set[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(code)
    return value


def _hash_valid(value: dict[str, Any], key: str, code: str) -> None:
    expected = value.get(key)
    body = {name: item for name, item in value.items() if name != key}
    if not isinstance(expected, str) or expected != canonical_hash(body):
        raise ValueError(code)


def _timestamp(value: Any, code: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(code) from exc
    if parsed.tzinfo is None:
        raise ValueError(code)
    return parsed.astimezone(UTC)


def validate_bundle(
    bundle: Any,
    *,
    mode: str,
    max_age_seconds: int = 5400,
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _exact_fields(bundle, {"snapshot", "compatibility"}, "SHARED_FEED_BUNDLE_INVALID")
    snapshot = _exact_fields(bundle["snapshot"], SNAPSHOT_KEYS, "SHARED_FEED_SNAPSHOT_FIELDS_INVALID")
    if snapshot["schema_version"] != "signalquarry-public-performance/v1" or snapshot["evidence_mode"] != "paper":
        raise ValueError("SHARED_FEED_SNAPSHOT_VERSION_OR_MODE_INVALID")
    _hash_valid(snapshot, "snapshot_hash", "SHARED_FEED_SNAPSHOT_HASH_INVALID")
    _exact_fields(snapshot["account"], ACCOUNT_KEYS, "SHARED_FEED_ACCOUNT_FIELDS_INVALID")
    pnl = snapshot["pnl"]
    if not isinstance(pnl, dict) or set(pnl) != {
        "broker_reference", "day", "net_dollar_pnl", "time_weighted_return"
    }:
        raise ValueError("SHARED_FEED_METRICS_INVALID")
    for metric in pnl.values():
        _exact_fields(metric, METRIC_KEYS, "SHARED_FEED_METRIC_FIELDS_INVALID")
    for collection, expected, code in (
        (snapshot["equity_history"], POINT_KEYS, "SHARED_FEED_HISTORY_INVALID"),
        (snapshot["positions"], POSITION_KEYS, "SHARED_FEED_POSITIONS_INVALID"),
        (snapshot["recent_fills"], FILL_KEYS, "SHARED_FEED_FILLS_INVALID"),
        (snapshot["external_cash_flows"], FLOW_KEYS, "SHARED_FEED_CASHFLOWS_INVALID"),
    ):
        if not isinstance(collection, list) or any(not isinstance(item, dict) or set(item) != expected for item in collection):
            raise ValueError(code)
    _exact_fields(snapshot["availability"], AVAILABILITY_KEYS, "SHARED_FEED_AVAILABILITY_INVALID")
    if not isinstance(snapshot["limitations"], list) or any(not isinstance(item, str) for item in snapshot["limitations"]):
        raise ValueError("SHARED_FEED_LIMITATIONS_INVALID")

    compat = _exact_fields(bundle["compatibility"], COMPAT_KEYS, "SHARED_FEED_COMPAT_FIELDS_INVALID")
    if compat["schema_version"] != "stable-income-generator-live-paper/v3" or compat["source"] != "broker_reported_paper":
        raise ValueError("SHARED_FEED_COMPAT_VERSION_OR_SOURCE_INVALID")
    _hash_valid(compat, "artifact_hash", "SHARED_FEED_COMPAT_HASH_INVALID")
    if compat["signalquarry_snapshot_hash"] != snapshot["snapshot_hash"]:
        raise ValueError("SHARED_FEED_HASH_IDENTITY_MISMATCH")
    captured = _timestamp(snapshot["captured_at"], "SHARED_FEED_CAPTURE_TIME_INVALID")
    if _timestamp(compat["generated_at"], "SHARED_FEED_COMPAT_CAPTURE_TIME_INVALID") != captured:
        raise ValueError("SHARED_FEED_CAPTURE_TIME_MISMATCH")
    if mode == "refresh":
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("SHARED_FEED_NOW_TIMEZONE_REQUIRED")
        age = (current.astimezone(UTC) - captured).total_seconds()
        if age < -300 or age > max_age_seconds:
            raise ValueError("SHARED_FEED_CAPTURE_STALE_OR_FUTURE")
    elif mode != "site-only":
        raise ValueError("SHARED_FEED_MODE_INVALID")

    account = _exact_fields(compat["account"], COMPAT_ACCOUNT_KEYS, "SHARED_FEED_COMPAT_ACCOUNT_FIELDS_INVALID")
    if account["deployment_alias"] != snapshot["deployment_alias"]:
        raise ValueError("SHARED_FEED_DEPLOYMENT_IDENTITY_MISMATCH")
    scope = _exact_fields(
        compat["publication_scope"],
        {"paper_only", "account_id_publication_approved", "excluded", "order_filter"},
        "SHARED_FEED_PUBLICATION_SCOPE_INVALID",
    )
    if scope["paper_only"] is not True or scope["account_id_publication_approved"] is not False:
        raise ValueError("SHARED_FEED_PUBLICATION_SCOPE_NOT_SANITIZED")
    strategy = _exact_fields(compat["strategy"], {"strategy_id", "underlying"}, "SHARED_FEED_COMPAT_STRATEGY_INVALID")
    history = _exact_fields(compat["portfolio_history"], {"status", "period", "timeframe", "points"}, "SHARED_FEED_COMPAT_HISTORY_INVALID")
    if not isinstance(history["points"], list):
        raise ValueError("SHARED_FEED_COMPAT_HISTORY_POINTS_INVALID")
    refresh = compat["refresh_contract"]
    _exact_fields(refresh, {"scheduled_interval_seconds", "browser_poll_seconds", "stale_after_seconds", "publishing_window", "delivery"}, "SHARED_FEED_REFRESH_CONTRACT_INVALID")
    _exact_fields(refresh["publishing_window"], {"timezone", "weekdays", "start", "final_run"}, "SHARED_FEED_PUBLISHING_WINDOW_INVALID")
    fills = compat["recent_filled_system_orders"]
    if not isinstance(fills, list) or len(fills) > 10:
        raise ValueError("SHARED_FEED_COMPAT_FILLS_INVALID")
    for fill in fills:
        _exact_fields(fill, {"filled_at", "action", "side", "quantity", "average_fill_price", "contract"}, "SHARED_FEED_COMPAT_FILL_FIELDS_INVALID")
        _exact_fields(fill["contract"], {"symbol", "underlying", "expiry", "option_type", "strike"}, "SHARED_FEED_COMPAT_CONTRACT_FIELDS_INVALID")
    if any(not isinstance(item, str) for item in scope["excluded"]):
        raise ValueError("SHARED_FEED_COMPAT_EXCLUSIONS_INVALID")
    if strategy["strategy_id"] != snapshot["strategy_version"]:
        raise ValueError("SHARED_FEED_STRATEGY_IDENTITY_MISMATCH")
    return snapshot, compat


def _atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def publish(
    bundle_path: Path,
    *,
    public_root: Path,
    mode: str,
    max_age_seconds: int,
    now: datetime | None = None,
) -> str:
    size = bundle_path.stat().st_size
    if size <= 0 or size > MAX_BUNDLE_BYTES:
        raise ValueError("SHARED_FEED_BUNDLE_SIZE_INVALID")
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    snapshot, compat = validate_bundle(bundle, mode=mode, max_age_seconds=max_age_seconds, now=now)
    root = public_root / "assets" / "data"
    json_body = (json.dumps(compat, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    script_value = json.dumps(compat, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    script_body = f"globalThis.__LIVE_PAPER_SNAPSHOT__ = Object.freeze({script_value});\n".encode("utf-8")
    _atomic_write(root / "live-paper-snapshot.json", json_body)
    _atomic_write(root / "live-paper-snapshot.js", script_body)
    return snapshot["snapshot_hash"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--public-root", type=Path, default=Path("docs"))
    parser.add_argument("--mode", choices=("refresh", "site-only"), required=True)
    parser.add_argument("--max-age-seconds", type=int, default=5400)
    args = parser.parse_args()
    digest = publish(args.input, public_root=args.public_root, mode=args.mode, max_age_seconds=args.max_age_seconds)
    print(f"PUBLIC_SHARED_FEED_STAGED mode={args.mode} snapshot_hash={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
