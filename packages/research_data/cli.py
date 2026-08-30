"""Command-line entry point used only by the approved data-steward runtime."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .client import ReadOnlyAlpacaClient
from .collector import CollectionSpec, ResearchDataCollector, ResearchDataError


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect immutable, read-only Alpaca research data")
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--option-observation-requests", type=Path)
    parser.add_argument("--quote-symbols", type=Path)
    args = parser.parse_args()
    key = os.environ.get("ALPACA_MARKET_DATA_KEY_ID")
    secret = os.environ.get("ALPACA_MARKET_DATA_SECRET_KEY")
    if not key or not secret:
        raise SystemExit("ALPACA_READ_ONLY_CREDENTIALS_UNAVAILABLE")
    try:
        manifest = ResearchDataCollector(
            ReadOnlyAlpacaClient(headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
        ).collect(
            spec=CollectionSpec.from_yaml(args.spec),
            spec_path=args.spec,
            output=args.output,
            option_request_path=args.option_observation_requests,
            quote_symbols_path=args.quote_symbols,
        )
    except ResearchDataError as exc:
        raise SystemExit(str(exc)) from exc
    print(manifest)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
