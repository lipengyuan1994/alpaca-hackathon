#!/usr/bin/env python3
"""Refresh and optionally stage the sanitized public Alpaca paper snapshot."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

from packages.contracts.canonical import canonical_hash
from packages.paper_wheel.public_snapshot import publish, write_browser_snapshot

ROOT = Path(__file__).resolve().parents[1]
JSON_OUTPUT = ROOT / "docs" / "assets" / "data" / "live-paper-snapshot.json"
BROWSER_OUTPUT = ROOT / "docs" / "assets" / "data" / "live-paper-snapshot.js"
DEFAULT_MAX_AGE_SECONDS = 5400


def _configure_environment() -> None:
    required = {
        "ALPACA_PAPER_API_KEY": "paper_alpaca_api_key",
        "ALPACA_PAPER_API_SECRET": "paper_alpaca_api_secret",
        "ALPACA_PAPER_ACCOUNT_ID": "paper_account_id",
    }
    if all(os.environ.get(name, "").strip() for name in required):
        os.environ.setdefault("ALPACA_PAPER_BASE_URL", "https://paper-api.alpaca.markets")
        return

    secrets_root = Path(
        os.environ.get(
            "REGIMESWITCH_SECRETS_DIR",
            str(Path.home() / ".config" / "great_secrets"),
        )
    )
    bundle = secrets_root / "alpaca" / "alpaca_api_key.yaml"
    values = yaml.safe_load(bundle.read_text(encoding="utf-8"))
    if not isinstance(values, dict):
        raise RuntimeError(f"Invalid Alpaca secret bundle: {bundle}")
    for environment_name, yaml_name in required.items():
        os.environ.setdefault(environment_name, str(values.get(yaml_name, "")))
    os.environ.setdefault("ALPACA_PAPER_BASE_URL", "https://paper-api.alpaca.markets")


def _assert_native_macos() -> None:
    if sys.platform == "darwin" and platform.machine() != "arm64":
        raise RuntimeError("Snapshot refresh requires a native macOS arm64 Python")


def _load_valid_snapshot() -> dict[str, object]:
    snapshot = json.loads(JSON_OUTPUT.read_text(encoding="utf-8"))
    artifact_hash = snapshot.pop("artifact_hash", None)
    if artifact_hash != canonical_hash(snapshot):
        raise RuntimeError("Public snapshot artifact hash is invalid")
    snapshot["artifact_hash"] = artifact_hash
    return snapshot


def _check(max_age_seconds: int) -> None:
    snapshot = _load_valid_snapshot()
    generated_at = datetime.fromisoformat(str(snapshot["generated_at"]).replace("Z", "+00:00"))
    age = (datetime.now(tz=UTC) - generated_at.astimezone(UTC)).total_seconds()
    if age < 0 or age > max_age_seconds:
        raise RuntimeError(
            f"Public snapshot is {max(0, int(age))} seconds old; "
            "commit a fresh snapshot before pushing"
        )
    expected = (
        "globalThis.__LIVE_PAPER_SNAPSHOT__ = Object.freeze("
        + json.dumps(
            snapshot,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + ");\n"
    )
    if BROWSER_OUTPUT.read_text(encoding="utf-8") != expected:
        raise RuntimeError("Browser snapshot does not match the hash-bound JSON snapshot")
    print(f"PUBLIC_PAPER_SNAPSHOT_CURRENT generated_at={snapshot['generated_at']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--stage", action="store_true")
    parser.add_argument("--max-age-seconds", type=int, default=DEFAULT_MAX_AGE_SECONDS)
    args = parser.parse_args()
    _assert_native_macos()
    if args.check:
        _check(args.max_age_seconds)
        return 0

    _configure_environment()
    snapshot = publish(output=JSON_OUTPUT)
    write_browser_snapshot(snapshot=snapshot, output=BROWSER_OUTPUT)
    _check(args.max_age_seconds)
    if args.stage:
        subprocess.run(
            [
                "git",
                "add",
                str(JSON_OUTPUT.relative_to(ROOT)),
                str(BROWSER_OUTPUT.relative_to(ROOT)),
            ],
            cwd=ROOT,
            check=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
