"""GET-only Alpaca daily-bar collector for the ETF cash study."""

from __future__ import annotations

import json
import platform
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

import yaml

from packages.contracts.canonical import canonical_hash
from packages.research_data.artifacts import (
    atomic_json,
    ensure_empty_output,
    file_hash,
    write_parquet,
    write_raw_pages,
)
from packages.research_data.client import FetchedPage, ReadOnlyAlpacaClient, ResearchHttpError


class ETFCollectionError(ValueError):
    """Collection output cannot be used as immutable backtest input."""


@dataclass(frozen=True)
class ETFCollectionSpec:
    collection_id: str
    symbols: tuple[str, ...]
    start: str
    end: str
    feeds: tuple[str, ...] = ("sip", "iex")
    timeframe: str = "1Day"
    page_limit: int = 10_000
    availability_delay_seconds: int = 1

    @classmethod
    def from_yaml(cls, path: Path) -> "ETFCollectionSpec":
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            spec = cls(
                collection_id=str(raw["collection_id"]),
                symbols=tuple(str(item).upper() for item in raw["symbols"]),
                start=str(raw["stock_history"]["start"]),
                end=str(raw["stock_history"]["end"]),
                feeds=tuple(str(item).lower() for item in raw.get("feeds", ("sip", "iex"))),
                timeframe=str(raw.get("timeframe", "1Day")),
                page_limit=int(raw.get("page_limit", 10_000)),
                availability_delay_seconds=int(raw.get("availability_delay_seconds", 1)),
            )
        except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
            raise ETFCollectionError("ETF_COLLECTION_SPEC_INVALID") from exc
        if not spec.collection_id or len(set(spec.symbols)) != len(spec.symbols) or not set(spec.symbols).issuperset({"QQQM", "SOXX", "SMH", "QQQ", "SPY"}):
            raise ETFCollectionError("ETF_COLLECTION_SYMBOLS_INVALID")
        if spec.timeframe != "1Day" or not set(spec.feeds).issubset({"sip", "iex"}) or not spec.feeds:
            raise ETFCollectionError("ETF_COLLECTION_SPEC_OUT_OF_POLICY")
        return spec

    @classmethod
    def from_yaml_v2(cls, path: Path) -> "ETFCollectionSpec":
        """Parse the extended universe without changing the frozen v1 parser."""
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            spec = cls(
                collection_id=str(raw["collection_id"]),
                symbols=tuple(str(item).upper() for item in raw["symbols"]),
                start=str(raw["stock_history"]["start"]),
                end=str(raw["stock_history"]["end"]),
                feeds=tuple(str(item).lower() for item in raw.get("feeds", ("sip", "iex"))),
                timeframe=str(raw.get("timeframe", "1Day")),
                page_limit=int(raw.get("page_limit", 10_000)),
                availability_delay_seconds=int(raw.get("availability_delay_seconds", 1)),
            )
        except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
            raise ETFCollectionError("ETF_COLLECTION_V2_SPEC_INVALID") from exc
        allowed = {"QQQM", "SOXX", "SMH", "QQQ", "SPY", "TQQQ", "SOXL", "SPXL"}
        if not spec.collection_id or not spec.symbols or len(set(spec.symbols)) != len(spec.symbols) or not set(spec.symbols).issubset(allowed):
            raise ETFCollectionError("ETF_COLLECTION_V2_SYMBOLS_INVALID")
        if spec.timeframe != "1Day" or not set(spec.feeds).issubset({"sip", "iex"}) or not spec.feeds:
            raise ETFCollectionError("ETF_COLLECTION_V2_SPEC_OUT_OF_POLICY")
        return spec


def _bar_rows(pages: tuple[FetchedPage, ...], *, symbols: tuple[str, ...], available_delay: int, default_symbol: str | None = None) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for page in pages:
        payload = page.payload
        if not isinstance(payload, dict):
            raise ETFCollectionError("ETF_COLLECTION_BARS_RESPONSE_INVALID")
        bars = payload.get("bars")
        if not isinstance(bars, list):
            raise ETFCollectionError("ETF_COLLECTION_BARS_MISSING")
        symbol = str(payload.get("symbol", page.request_params.get("symbol", default_symbol or ""))).upper()
        if symbol not in symbols:
            continue
        for item in bars:
            if not isinstance(item, Mapping):
                raise ETFCollectionError("ETF_COLLECTION_BAR_INVALID")
            timestamp = pd_timestamp(item.get("t"))
            output.append({
                "symbol": symbol,
                "event_time": timestamp.isoformat(),
                "available_time": (timestamp + timedelta(seconds=available_delay)).isoformat(),
                "ingested_at": datetime.now(UTC).isoformat(),
                "open": float(item["o"]), "high": float(item["h"]), "low": float(item["l"]), "close": float(item["c"]),
                "volume": float(item.get("v", 0.0)), "trade_count": int(item.get("n", 0)), "vwap": float(item["vw"]) if item.get("vw") is not None else None,
                "endpoint": page.endpoint, "feed": page.request_params.get("feed"), "source_page_token": page.page_token, "raw_response_hash": page.raw_hash,
            })
    return output


def pd_timestamp(value: Any):
    import pandas as pd

    try:
        timestamp = pd.Timestamp(value)
    except Exception as exc:  # pragma: no cover - provider malformed response
        raise ETFCollectionError("ETF_COLLECTION_TIMESTAMP_INVALID") from exc
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(UTC)
    return timestamp.tz_convert(UTC)


class ETFDataCollector:
    def __init__(self, client: ReadOnlyAlpacaClient) -> None:
        self.client = client

    def collect(self, *, spec: ETFCollectionSpec, spec_path: Path, output: Path) -> Path:
        root = ensure_empty_output(output)
        datasets: list[dict[str, Any]] = []
        probe: list[dict[str, Any]] = []
        all_raw: list[dict[str, Any]] = []
        all_split: list[dict[str, Any]] = []
        raw_pages: list[FetchedPage] = []
        split_pages: list[FetchedPage] = []
        try:
            selected_feed: dict[str, str] = {}
            for symbol in spec.symbols:
                for feed in spec.feeds:
                    params = {"timeframe": spec.timeframe, "start": spec.start, "end": spec.end, "feed": feed, "adjustment": "raw", "limit": str(spec.page_limit)}
                    try:
                        raw_result = self.client.get_paginated(base_url="https://data.alpaca.markets", endpoint=f"/v2/stocks/{symbol}/bars", params=params)
                    except ResearchHttpError as exc:
                        probe.append({"symbol": symbol, "feed": feed, "adjustment": "raw", "status": "FAILED", "reason": str(exc)})
                        continue
                    try:
                        split_params = {**params, "adjustment": "split"}
                        split_result = self.client.get_paginated(base_url="https://data.alpaca.markets", endpoint=f"/v2/stocks/{symbol}/bars", params=split_params)
                    except ResearchHttpError as exc:
                        probe.append({"symbol": symbol, "feed": feed, "adjustment": "split", "status": "FAILED", "reason": str(exc)})
                        continue
                    selected_feed[symbol] = feed
                    raw_pages.extend(raw_result)
                    split_pages.extend(split_result)
                    all_raw.extend(_bar_rows(raw_result, symbols=(symbol,), default_symbol=symbol, available_delay=spec.availability_delay_seconds))
                    all_split.extend(_bar_rows(split_result, symbols=(symbol,), default_symbol=symbol, available_delay=spec.availability_delay_seconds))
                    probe.extend([
                        {"symbol": symbol, "feed": feed, "adjustment": "raw", "status": "OK", "pages": len(raw_result)},
                        {"symbol": symbol, "feed": feed, "adjustment": "split", "status": "OK", "pages": len(split_result)},
                    ])
                    break
                if symbol not in selected_feed:
                    raise ETFCollectionError(f"ETF_COLLECTION_NO_FEED:{symbol}")
            if len(set(selected_feed.values())) != 1:
                raise ETFCollectionError("ETF_COLLECTION_MIXED_FEEDS")
            if not all_raw or not all_split:
                raise ETFCollectionError("ETF_COLLECTION_NO_BARS")
            raw_meta = write_raw_pages(root, "stock_bars_raw", raw_pages)
            split_meta = write_raw_pages(root, "stock_bars_split", split_pages)
            raw_artifact = write_parquet(root, "stock_bars_raw", all_raw, tuple(all_raw[0].keys()))
            split_artifact = write_parquet(root, "stock_bars_split", all_split, tuple(all_split[0].keys()))
            datasets.extend([
                {"dataset_id": "stock_bars_raw", "feed": sorted(set(selected_feed.values())), "adjustment": "raw", "raw_pages": raw_meta, "artifact": raw_artifact},
                {"dataset_id": "stock_bars_split", "feed": sorted(set(selected_feed.values())), "adjustment": "split", "raw_pages": split_meta, "artifact": split_artifact},
            ])
            calendar_page = self.client.get_one(base_url="https://paper-api.alpaca.markets", endpoint="/v2/calendar", params={"start": spec.start[:10], "end": spec.end[:10], "date_type": "TRADING"})
            calendar_payload = calendar_page.payload
            calendar_rows = calendar_payload if isinstance(calendar_payload, list) else calendar_payload.get("calendar", []) if isinstance(calendar_payload, dict) else []
            if not isinstance(calendar_rows, list):
                raise ETFCollectionError("ETF_COLLECTION_CALENDAR_INVALID")
            calendar_artifact = write_parquet(root, "calendar", calendar_rows, tuple(calendar_rows[0].keys()) if calendar_rows else ("date", "open", "close"))
            datasets.append({"dataset_id": "calendar", "artifact": calendar_artifact, "raw_pages": write_raw_pages(root, "calendar", (calendar_page,))})
            # Corporate actions are a required lineage input.  If the account
            # lacks this endpoint, the manifest is explicitly FAILED.
            actions = self.client.get_paginated(base_url="https://data.alpaca.markets", endpoint="/v1/corporate-actions", params={"symbols": ",".join(spec.symbols), "start": spec.start[:10], "end": spec.end[:10], "types": "cash_dividend,stock_dividend,forward_split,reverse_split,unit_split", "limit": str(min(spec.page_limit, 1000)), "sort": "asc"})
            action_rows = []
            for page in actions:
                if isinstance(page.payload, dict):
                    def collect_lists(value: Any, action_type: str | None = None) -> None:
                        if isinstance(value, list):
                            for item in value:
                                if isinstance(item, Mapping):
                                    action_rows.append({**item, "action_type": action_type or item.get("type", "unknown")})
                        elif isinstance(value, Mapping):
                            for key, nested in value.items():
                                collect_lists(nested, key if key not in {"corporate_actions", "actions"} else action_type)

                    collect_lists(page.payload)
            action_columns = tuple(dict.fromkeys(key for row in action_rows for key in row)) if action_rows else ("symbol", "type", "ex_date", "payable_date", "value")
            action_artifact = write_parquet(root, "corporate_actions", action_rows, action_columns)
            datasets.append({"dataset_id": "corporate_actions", "artifact": action_artifact, "raw_pages": write_raw_pages(root, "corporate_actions", actions)})
        except (ResearchHttpError, ETFCollectionError, KeyError, TypeError, ValueError) as exc:
            atomic_json(root / "collection_failure.json", {"schema_version": "etf-cash-collection-failure/v1", "status": "FAILED", "reason": str(exc), "probe": probe})
            raise ETFCollectionError(str(exc)) from exc
        manifest: dict[str, Any] = {
            "schema_version": "etf-cash-data-manifest/v1",
            "status": "COLLECTED",
            "collection_id": spec.collection_id,
            "spec_hash": file_hash(spec_path),
            "symbols": list(spec.symbols),
            "timeframe": spec.timeframe,
            "feeds_requested": list(spec.feeds),
            "entitlement_probe": probe,
            "runtime": {"python_architecture": platform.machine(), "collector": "etf-cash-collect/v1"},
            "datasets": datasets,
            "manifest_hash": None,
        }
        manifest["manifest_hash"] = canonical_hash({key: value for key, value in manifest.items() if key != "manifest_hash"})
        atomic_json(root / "data_manifest.json", manifest)
        return root / "data_manifest.json"


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ETFCollectionError("ETF_DATA_MANIFEST_INVALID") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "etf-cash-data-manifest/v1" or manifest.get("status") != "COLLECTED" or manifest.get("manifest_hash") != canonical_hash({key: value for key, value in manifest.items() if key != "manifest_hash"}):
        raise ETFCollectionError("ETF_DATA_MANIFEST_HASH_MISMATCH")
    return manifest
