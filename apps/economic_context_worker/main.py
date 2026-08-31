"""Collect or reuse the one immutable daily Alpaca economic context."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from apps.common import assert_native_developer_runtime
from packages.economic_context.alpaca import AlpacaEconomicDataAdapter
from packages.economic_context.collector import EconomicContextCollector, EconomicContextError
from packages.economic_context.config import load_economic_context_config
from packages.economic_context.store import PostgresEconomicContextStore
from packages.runtime_secrets import require_file_secret


def collect_once(*, now: datetime, store: object, data_port: object, config_path: Path) -> dict[str, str]:
    config = load_economic_context_config(config_path)
    collector = EconomicContextCollector(
        store=store,  # type: ignore[arg-type]
        data_port=data_port,  # type: ignore[arg-type]
        config=config,
    )
    result = collector.get_or_collect(now=now)
    return {
        "status": result.source,
        "context_id": result.context.context_id,
        "trading_date": result.context.trading_date.isoformat(),
        "context_hash": result.context.content_hash,
    }


def main() -> None:
    assert_native_developer_runtime()
    parser = argparse.ArgumentParser(
        description="Collect one pre-market Alpaca market/news economic context or return the cached context."
    )
    parser.add_argument("--as-of", help="UTC RFC3339 instant for controlled replay/testing")
    args = parser.parse_args()
    now = datetime.now(UTC)
    if args.as_of:
        now = datetime.fromisoformat(args.as_of.replace("Z", "+00:00")).astimezone(UTC)
    config_path = Path(
        os.environ.get("ECONOMIC_CONTEXT_CONFIG_PATH", "/app/configs/economic_context.yaml")
    )
    dsn = require_file_secret("DATABASE_URL")
    store = PostgresEconomicContextStore.from_dsn(dsn)
    adapter = AlpacaEconomicDataAdapter.from_environment()
    try:
        print(json.dumps(collect_once(now=now, store=store, data_port=adapter, config_path=config_path), sort_keys=True))
    except EconomicContextError as exc:
        print(json.dumps({"status": "NO_CONTEXT", "reason_code": str(exc)}, sort_keys=True))
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
