"""Data-steward collector for immutable Alpaca research inputs.

This module is intentionally outside strategy plug-ins.  It is read-only and
records provider evidence; it never creates a broker, account, or order client.
"""

from __future__ import annotations

import json
import platform
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import yaml

from packages.contracts.canonical import canonical_hash

from .artifacts import atomic_json, ensure_empty_output, file_hash, write_parquet, write_raw_pages
from .client import FetchedPage, ReadOnlyAlpacaClient, ResearchHttpError

DATA_BASE_URL = "https://data.alpaca.markets"
TRADING_BASE_URL = "https://paper-api.alpaca.markets"
STOCK_COLUMNS = (
    "symbol", "event_time", "available_time", "ingested_at", "open", "high", "low", "close",
    "volume", "trade_count", "vwap", "endpoint", "feed", "source_page_token", "raw_response_hash",
)
OPTION_BAR_COLUMNS = STOCK_COLUMNS
OPTION_TRADE_COLUMNS = (
    "symbol", "event_time", "available_time", "ingested_at", "price", "size", "exchange", "conditions",
    "endpoint", "feed", "source_page_token", "raw_response_hash",
)
OPTION_QUOTE_COLUMNS = (
    "symbol", "event_time", "available_time", "ingested_at", "bid", "ask", "bid_size", "ask_size",
    "endpoint", "feed", "source_page_token", "raw_response_hash",
)
CONTRACT_COLUMNS = (
    "symbol", "underlying", "right", "strike", "expiration", "style", "multiplier", "deliverable",
    "status", "tradable", "ingested_at", "endpoint", "source_page_token", "raw_response_hash", "record_json",
)


class ResearchDataError(ValueError):
    """Input lineage or provider response is not safe to freeze."""


@dataclass(frozen=True)
class CollectionSpec:
    collection_id: str
    symbols: tuple[str, ...]
    start: str
    end: str
    option_start: str
    option_end: str
    stock_timeframe: str = "1Min"
    feed: str = "iex"
    page_limit: int = 10_000
    availability_delay_seconds: int = 1

    @classmethod
    def from_yaml(cls, path: Path) -> "CollectionSpec":
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ResearchDataError("RESEARCH_COLLECTION_SPEC_INVALID") from exc
        if not isinstance(raw, dict):
            raise ResearchDataError("RESEARCH_COLLECTION_SPEC_INVALID")
        try:
            symbols = tuple(str(item).upper() for item in raw["symbols"])
            spec = cls(
                collection_id=str(raw["collection_id"]),
                symbols=symbols,
                start=str(raw["stock_history"]["start"]),
                end=str(raw["stock_history"]["end"]),
                option_start=str(raw["option_history"]["start"]),
                option_end=str(raw["option_history"]["end"]),
                stock_timeframe=str(raw.get("stock_timeframe", "1Min")),
                feed=str(raw.get("stock_feed", "iex")),
                page_limit=int(raw.get("page_limit", 10_000)),
                availability_delay_seconds=int(raw.get("availability_delay_seconds", 1)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ResearchDataError("RESEARCH_COLLECTION_SPEC_INVALID") from exc
        if not spec.collection_id or not spec.symbols or spec.feed != "iex" or not 1 <= spec.page_limit <= 10_000:
            raise ResearchDataError("RESEARCH_COLLECTION_SPEC_OUT_OF_POLICY")
        return spec


@dataclass(frozen=True)
class OptionObservationRequest:
    request_id: str
    symbols: tuple[str, ...]
    start: str
    end: str

    @classmethod
    def load(cls, path: Path | None) -> tuple["OptionObservationRequest", ...]:
        if path is None:
            return ()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            entries = raw["requests"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ResearchDataError("OPTION_OBSERVATION_REQUEST_INVALID") from exc
        if not isinstance(entries, list):
            raise ResearchDataError("OPTION_OBSERVATION_REQUEST_INVALID")
        requests: list[OptionObservationRequest] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise ResearchDataError("OPTION_OBSERVATION_REQUEST_INVALID")
            symbols = tuple(str(value).upper() for value in entry.get("symbols", ()))
            request = cls(str(entry.get("request_id", "")), symbols, str(entry.get("start", "")), str(entry.get("end", "")))
            if not request.request_id or not request.symbols or len(request.symbols) > 100 or not request.start or not request.end:
                raise ResearchDataError("OPTION_OBSERVATION_REQUEST_INVALID")
            requests.append(request)
        if tuple(item.request_id for item in requests) != tuple(sorted(item.request_id for item in requests)):
            raise ResearchDataError("OPTION_OBSERVATION_REQUEST_ORDER_INVALID")
        return tuple(requests)


def load_quote_symbols(path: Path | None) -> tuple[str, ...]:
    if path is None:
        return ()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        symbols = tuple(str(symbol).upper() for symbol in raw["symbols"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ResearchDataError("OPTION_QUOTE_REQUEST_INVALID") from exc
    if not symbols or len(symbols) > 100 or tuple(sorted(set(symbols))) != symbols:
        raise ResearchDataError("OPTION_QUOTE_REQUEST_INVALID")
    return symbols


class ResearchDataCollector:
    def __init__(self, client: ReadOnlyAlpacaClient, *, now: Callable[[], datetime] | None = None) -> None:
        self._client = client
        self._now = now or (lambda: datetime.now(UTC))

    def collect(
        self,
        *,
        spec: CollectionSpec,
        spec_path: Path,
        output: Path,
        option_request_path: Path | None = None,
        quote_symbols_path: Path | None = None,
    ) -> Path:
        root = ensure_empty_output(output)
        requests = OptionObservationRequest.load(option_request_path)
        quote_symbols = load_quote_symbols(quote_symbols_path)
        datasets: list[dict[str, Any]] = []
        try:
            datasets.extend(self._collect_stocks(root, spec))
            datasets.append(self._collect_calendar(root, spec))
            datasets.append(self._collect_contracts(root, spec))
            for request in requests:
                datasets.extend(self._collect_option_observations(root, request, spec))
            if quote_symbols:
                datasets.append(self._collect_indicative_quotes(root, quote_symbols, spec))
        except ResearchHttpError as exc:
            atomic_json(root / "collection_failure.json", {"status": "FAILED", "reason": str(exc)})
            raise ResearchDataError(str(exc)) from exc
        probe = self._write_entitlement_probe(root, datasets)
        manifest: dict[str, Any] = {
            "schema_version": "research-data-manifest/v1",
            "collection_id": spec.collection_id,
            "status": "COLLECTED_UNATTESTED",
            "collector": "research-data-collect/v1",
            "git_revision": self._git_revision(),
            "platform": {"system": platform.system(), "machine": platform.machine()},
            "spec_hash": file_hash(spec_path),
            "option_observation_request_hash": file_hash(option_request_path) if option_request_path else None,
            "quote_symbols_request_hash": file_hash(quote_symbols_path) if quote_symbols_path else None,
            "datasets": sorted(datasets, key=lambda item: item["dataset_id"]),
            "entitlement_probe": probe,
            "manifest_hash": None,
        }
        manifest["manifest_hash"] = canonical_hash({key: value for key, value in manifest.items() if key != "manifest_hash"})
        target = root / "data_manifest.json"
        atomic_json(target, manifest)
        return target

    @staticmethod
    def _write_entitlement_probe(root: Path, datasets: list[dict[str, Any]]) -> dict[str, Any]:
        endpoints = sorted(
            {
                raw_page["endpoint"]
                for dataset in datasets
                for raw_page in dataset["raw_pages"]
            }
        )
        probe: dict[str, Any] = {
            "schema_version": "alpaca-entitlement-probe/v1",
            "status": "OBSERVED_READ_ONLY_ACCESS",
            "endpoints": endpoints,
            "feeds": sorted({str(dataset["feed"]) for dataset in datasets}),
            "datasets": [dataset["dataset_id"] for dataset in sorted(datasets, key=lambda item: item["dataset_id"])],
            "attestation_required": True,
            "probe_hash": None,
        }
        probe["probe_hash"] = canonical_hash({key: value for key, value in probe.items() if key != "probe_hash"})
        target = root / "entitlement_probe.json"
        atomic_json(target, probe)
        return {"path": target.name, "sha256": file_hash(target), "probe_hash": probe["probe_hash"]}

    def _collect_stocks(self, root: Path, spec: CollectionSpec) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for adjustment in ("raw", "split"):
            pages = self._client.get_paginated(
                base_url=DATA_BASE_URL,
                endpoint="/v2/stocks/bars",
                params={
                    "symbols": ",".join(spec.symbols), "timeframe": spec.stock_timeframe, "start": spec.start,
                    "end": spec.end, "adjustment": adjustment, "feed": spec.feed, "limit": str(spec.page_limit), "sort": "asc",
                },
            )
            dataset_id = f"stock_bars_{adjustment}"
            rows = self._bar_rows(pages, feed=spec.feed, availability_delay_seconds=spec.availability_delay_seconds)
            records.append(self._write_dataset(root, dataset_id, pages, rows, STOCK_COLUMNS, adjustment=adjustment, feed=spec.feed))
        return records

    def _collect_calendar(self, root: Path, spec: CollectionSpec) -> dict[str, Any]:
        page = self._client.get_one(
            base_url=TRADING_BASE_URL,
            endpoint="/v2/calendar",
            params={"start": spec.start[:10], "end": spec.end[:10], "date_type": "TRADING"},
        )
        rows = page.payload
        raw = write_raw_pages(root, "calendar", (page,))
        relative = Path("normalized") / "calendar.json"
        atomic_json(root / relative, {"calendar": rows})
        return {
            "dataset_id": "calendar",
            "kind": "calendar",
            "feed": "N/A_CALENDAR_ENDPOINT",
            "raw_pages": raw,
            "artifact": {"path": relative.as_posix(), "sha256": file_hash(root / relative)},
        }

    def _collect_contracts(self, root: Path, spec: CollectionSpec) -> dict[str, Any]:
        all_pages: list[FetchedPage] = []
        for status in ("active", "inactive"):
            all_pages.extend(self._client.get_paginated(
                base_url=TRADING_BASE_URL,
                endpoint="/v2/options/contracts",
                params={
                    "underlying_symbols": ",".join(spec.symbols), "status": status, "show_deliverables": "true",
                    "expiration_date_gte": spec.option_start[:10], "expiration_date_lte": spec.option_end[:10],
                    "limit": str(spec.page_limit),
                },
            ))
        rows: list[dict[str, Any]] = []
        ingested_at = self._timestamp(self._now())
        for page in all_pages:
            contracts = page.payload.get("option_contracts", [])
            if not isinstance(contracts, list):
                raise ResearchDataError("ALPACA_OPTION_CONTRACTS_SCHEMA_INVALID")
            for contract in contracts:
                if not isinstance(contract, dict):
                    raise ResearchDataError("ALPACA_OPTION_CONTRACT_RECORD_INVALID")
                rows.append({
                    "symbol": str(contract.get("symbol", "")), "underlying": str(contract.get("underlying_symbol", "")),
                    "right": str(contract.get("type", "")).upper(), "strike": str(contract.get("strike_price", "")),
                    "expiration": str(contract.get("expiration_date", "")), "style": str(contract.get("style", "")),
                    "multiplier": str(contract.get("size", "")), "deliverable": json.dumps(contract.get("deliverables", []), sort_keys=True),
                    "status": str(contract.get("status", "")), "tradable": str(contract.get("tradable", "")),
                    "ingested_at": ingested_at, "endpoint": page.endpoint, "source_page_token": page.page_token or "",
                    "raw_response_hash": page.raw_hash, "record_json": json.dumps(contract, sort_keys=True, separators=(",", ":")),
                })
        return self._write_dataset(root, "option_contracts", tuple(all_pages), rows, CONTRACT_COLUMNS, adjustment=None, feed="N/A_CONTRACT_METADATA")

    def _collect_option_observations(self, root: Path, request: OptionObservationRequest, spec: CollectionSpec) -> list[dict[str, Any]]:
        common = {"symbols": ",".join(request.symbols), "start": request.start, "end": request.end, "limit": str(spec.page_limit), "sort": "asc"}
        bars = self._client.get_paginated(base_url=DATA_BASE_URL, endpoint="/v1beta1/options/bars", params={**common, "timeframe": "1Min"})
        trades = self._client.get_paginated(base_url=DATA_BASE_URL, endpoint="/v1beta1/options/trades", params=common)
        return [
            self._write_dataset(root, f"option_bars_{request.request_id}", bars, self._bar_rows(bars, feed="N/A_ENDPOINT_HAS_NO_FEED_PARAM", availability_delay_seconds=spec.availability_delay_seconds), OPTION_BAR_COLUMNS, adjustment=None, feed="N/A_ENDPOINT_HAS_NO_FEED_PARAM"),
            self._write_dataset(root, f"option_trades_{request.request_id}", trades, self._trade_rows(trades, availability_delay_seconds=spec.availability_delay_seconds), OPTION_TRADE_COLUMNS, adjustment=None, feed="N/A_ENDPOINT_HAS_NO_FEED_PARAM"),
        ]

    def _collect_indicative_quotes(self, root: Path, symbols: tuple[str, ...], spec: CollectionSpec) -> dict[str, Any]:
        pages = self._client.get_paginated(
            base_url=DATA_BASE_URL,
            endpoint="/v1beta1/options/quotes/latest",
            params={"symbols": ",".join(symbols), "feed": "indicative"},
        )
        rows: list[dict[str, Any]] = []
        ingested_at = self._timestamp(self._now())
        for page in pages:
            quotes = page.payload.get("quotes", {})
            if not isinstance(quotes, dict):
                raise ResearchDataError("ALPACA_QUOTES_SCHEMA_INVALID")
            for symbol, quote in quotes.items():
                if not isinstance(quote, dict):
                    raise ResearchDataError("ALPACA_QUOTE_RECORD_INVALID")
                event_time = self._bar_time(quote)
                rows.append({
                    "symbol": str(symbol).upper(), "event_time": event_time,
                    "available_time": self._available(event_time, spec.availability_delay_seconds), "ingested_at": ingested_at,
                    "bid": self._number(quote, "bp"), "ask": self._number(quote, "ap"),
                    "bid_size": str(quote.get("bs", "")), "ask_size": str(quote.get("as", "")),
                    "endpoint": page.endpoint, "feed": "indicative", "source_page_token": page.page_token or "",
                    "raw_response_hash": page.raw_hash,
                })
        for row in rows:
            if float(row["ask"]) < float(row["bid"]):
                raise ResearchDataError("ALPACA_QUOTE_CROSSED")
        return self._write_dataset(root, "option_quotes_indicative", pages, rows, OPTION_QUOTE_COLUMNS, adjustment=None, feed="indicative")

    def _write_dataset(self, root: Path, dataset_id: str, pages: Iterable[FetchedPage], rows: list[dict[str, Any]], columns: tuple[str, ...], *, adjustment: str | None, feed: str) -> dict[str, Any]:
        copied_pages = tuple(pages)
        raw_pages = write_raw_pages(root, dataset_id, copied_pages)
        artifact = write_parquet(root, dataset_id, rows, columns)
        return {"dataset_id": dataset_id, "feed": feed, "adjustment": adjustment, "page_complete": True, "raw_pages": raw_pages, "artifact": artifact}

    def _bar_rows(self, pages: Iterable[FetchedPage], *, feed: str, availability_delay_seconds: int) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        ingested_at = self._timestamp(self._now())
        for page in pages:
            bars = page.payload.get("bars", {})
            if not isinstance(bars, dict):
                raise ResearchDataError("ALPACA_BARS_SCHEMA_INVALID")
            for symbol, values in bars.items():
                if not isinstance(values, list):
                    raise ResearchDataError("ALPACA_BAR_LIST_INVALID")
                for value in values:
                    if not isinstance(value, dict):
                        raise ResearchDataError("ALPACA_BAR_RECORD_INVALID")
                    event_time = self._bar_time(value)
                    rows.append({
                        "symbol": str(symbol).upper(), "event_time": event_time,
                        "available_time": self._available(event_time, availability_delay_seconds), "ingested_at": ingested_at,
                        "open": self._number(value, "o"), "high": self._number(value, "h"), "low": self._number(value, "l"),
                        "close": self._number(value, "c"), "volume": self._number(value, "v"), "trade_count": str(value.get("n", "")),
                        "vwap": str(value.get("vw", "")), "endpoint": page.endpoint, "feed": feed,
                        "source_page_token": page.page_token or "", "raw_response_hash": page.raw_hash,
                    })
        self._validate_bars(rows)
        return rows

    def _trade_rows(self, pages: Iterable[FetchedPage], *, availability_delay_seconds: int) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        ingested_at = self._timestamp(self._now())
        for page in pages:
            trades = page.payload.get("trades", {})
            if not isinstance(trades, dict):
                raise ResearchDataError("ALPACA_TRADES_SCHEMA_INVALID")
            for symbol, values in trades.items():
                if not isinstance(values, list):
                    raise ResearchDataError("ALPACA_TRADE_LIST_INVALID")
                for value in values:
                    if not isinstance(value, dict):
                        raise ResearchDataError("ALPACA_TRADE_RECORD_INVALID")
                    event_time = self._bar_time(value)
                    rows.append({
                        "symbol": str(symbol).upper(), "event_time": event_time,
                        "available_time": self._available(event_time, availability_delay_seconds), "ingested_at": ingested_at,
                        "price": self._number(value, "p"), "size": self._number(value, "s"), "exchange": str(value.get("x", "")),
                        "conditions": json.dumps(value.get("c", []), separators=(",", ":")), "endpoint": page.endpoint,
                        "feed": "N/A_ENDPOINT_HAS_NO_FEED_PARAM", "source_page_token": page.page_token or "", "raw_response_hash": page.raw_hash,
                    })
        return rows

    @staticmethod
    def _timestamp(value: datetime) -> str:
        if value.tzinfo is None:
            raise ResearchDataError("TIMESTAMP_MUST_BE_TIMEZONE_AWARE")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    def _bar_time(self, row: Mapping[str, Any]) -> str:
        raw = row.get("t")
        if not isinstance(raw, str):
            raise ResearchDataError("ALPACA_EVENT_TIME_INVALID")
        try:
            return self._timestamp(datetime.fromisoformat(raw.replace("Z", "+00:00")))
        except ValueError as exc:
            raise ResearchDataError("ALPACA_EVENT_TIME_INVALID") from exc

    def _available(self, event_time: str, seconds: int) -> str:
        parsed = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
        return self._timestamp(parsed + timedelta(seconds=seconds))

    @staticmethod
    def _number(row: Mapping[str, Any], key: str) -> str:
        value = row.get(key)
        if value is None:
            raise ResearchDataError(f"ALPACA_REQUIRED_FIELD_MISSING_{key}")
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise ResearchDataError(f"ALPACA_REQUIRED_FIELD_INVALID_{key}") from exc
        if not parsed == parsed or parsed in (float("inf"), float("-inf")):
            raise ResearchDataError(f"ALPACA_REQUIRED_FIELD_INVALID_{key}")
        return str(value)

    @staticmethod
    def _validate_bars(rows: list[dict[str, Any]]) -> None:
        seen: set[tuple[str, str]] = set()
        for row in rows:
            key = (row["symbol"], row["event_time"])
            if key in seen:
                raise ResearchDataError("ALPACA_DUPLICATE_BAR")
            seen.add(key)
            low, high, open_, close = (float(row[key]) for key in ("low", "high", "open", "close"))
            if low > min(open_, close) or high < max(open_, close) or float(row["volume"]) < 0:
                raise ResearchDataError("ALPACA_OHLC_INVALID")

    @staticmethod
    def _git_revision() -> str:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return "UNAVAILABLE"
        revision = result.stdout.strip()
        return revision if revision else "UNAVAILABLE"
