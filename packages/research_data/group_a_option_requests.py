"""Frozen Group A O2 proxy request generation before option prices are read."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from packages.contracts.canonical import canonical_hash

from .artifacts import atomic_json, file_hash

_DECISION_MINUTES = {"10:30", "11:00", "11:30", "12:00", "12:30", "13:00", "13:30", "14:00", "14:30"}
_SYMBOLS = ("SPY", "QQQ")


class GroupARequestError(ValueError):
    """A frozen proxy request cannot be generated from its declared inputs."""


@dataclass(frozen=True)
class _Signal:
    strategy: str
    symbol: str
    timestamp: pd.Timestamp
    side: str
    raw_spot: float


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GroupARequestError("DATA_MANIFEST_INVALID") from exc
    expected = canonical_hash({key: value for key, value in manifest.items() if key != "manifest_hash"})
    if manifest.get("schema_version") != "research-data-manifest/v1" or manifest.get("manifest_hash") != expected:
        raise GroupARequestError("DATA_MANIFEST_HASH_MISMATCH")
    return manifest


def _artifact(root: Path, manifest: dict[str, Any], dataset_id: str) -> Path:
    dataset = next((item for item in manifest.get("datasets", []) if item.get("dataset_id") == dataset_id), None)
    if not isinstance(dataset, dict) or not isinstance(dataset.get("artifact"), dict):
        raise GroupARequestError(f"DATASET_MISSING_{dataset_id}")
    value = root / str(dataset["artifact"].get("path", ""))
    if not value.is_file() or file_hash(value) != dataset["artifact"].get("sha256"):
        raise GroupARequestError(f"DATASET_HASH_MISMATCH_{dataset_id}")
    return value


def _bars(path: Path) -> pd.DataFrame:
    bars = pd.read_parquet(path, columns=["symbol", "event_time", "open", "high", "low", "close", "volume", "vwap"])
    bars = bars[bars["symbol"].isin(_SYMBOLS)].copy()
    bars["timestamp"] = pd.to_datetime(bars["event_time"], utc=True).dt.tz_convert("America/New_York")
    for field in ("open", "high", "low", "close", "volume", "vwap"):
        bars[field] = pd.to_numeric(bars[field], errors="coerce")
    bars = bars.dropna(subset=["open", "high", "low", "close", "volume", "vwap"])
    bars = bars[(bars["timestamp"].dt.weekday < 5) & (bars["timestamp"].dt.time >= pd.Timestamp("09:30").time()) & (bars["timestamp"].dt.time < pd.Timestamp("16:00").time())]
    bars["session"] = bars["timestamp"].dt.date
    complete = bars.groupby(["symbol", "session"])["timestamp"].size().eq(390)
    good = complete[complete].index
    return bars.set_index(["symbol", "session"]).loc[good].reset_index()


def _signals(split_path: Path, raw_path: Path) -> list[_Signal]:
    bars = _bars(split_path)
    raw = _bars(raw_path)[["symbol", "timestamp", "close"]].rename(columns={"close": "raw_spot"})
    bars["bucket"] = bars["timestamp"].dt.floor("15min") + pd.Timedelta(minutes=15)
    grouped = bars.groupby(["symbol", "session", "bucket"], sort=True)
    intervals = grouped.agg(close=("close", "last"), volume=("volume", "sum"), pv=("vwap", lambda values: 0.0)).reset_index()
    # Session VWAP is calculated from one-minute values so it is available only
    # after each completed 15-minute interval.
    bars = bars.sort_values(["symbol", "timestamp"])
    bars["pv"] = bars["vwap"] * bars["volume"]
    bars["cum_pv"] = bars.groupby(["symbol", "session"])["pv"].cumsum()
    bars["cum_volume"] = bars.groupby(["symbol", "session"])["volume"].cumsum()
    vwap = bars.groupby(["symbol", "session", "bucket"], sort=True).tail(1)[["symbol", "session", "bucket", "cum_pv", "cum_volume"]]
    intervals = intervals.merge(vwap, on=["symbol", "session", "bucket"], how="inner")
    intervals["session_vwap"] = intervals["cum_pv"] / intervals["cum_volume"]
    intervals["time"] = intervals["bucket"].dt.strftime("%H:%M")
    intervals = intervals[intervals["time"].isin(_DECISION_MINUTES)].copy()
    opens = bars.groupby(["symbol", "session"], sort=True)["open"].first().rename("session_open")
    intervals = intervals.join(opens, on=["symbol", "session"])
    by_time = intervals.set_index(["symbol", "session", "time"])["close"]
    prior_close = []
    for row in intervals.itertuples(index=False):
        if row.time == "10:30":
            prior_close.append(row.session_open)
        else:
            prior_time = (row.bucket - pd.Timedelta(minutes=60)).strftime("%H:%M")
            prior_close.append(by_time.get((row.symbol, row.session, prior_time), float("nan")))
    intervals["r60"] = (intervals["close"] / pd.Series(prior_close, index=intervals.index)).map(__import__("math").log)
    intervals["deviation"] = (intervals["close"] / intervals["session_vwap"]).map(__import__("math").log)
    intervals = intervals.sort_values(["symbol", "time", "session"])
    for source, target in (("r60", "momentum_z"), ("deviation", "deviation_z")):
        prior = intervals.groupby(["symbol", "time"])[source].shift(1)
        mean = prior.groupby([intervals["symbol"], intervals["time"]]).transform(lambda value: value.rolling(20, min_periods=20).mean())
        std = prior.groupby([intervals["symbol"], intervals["time"]]).transform(lambda value: value.rolling(20, min_periods=20).std(ddof=1))
        intervals[target] = (intervals[source] - mean) / std.clip(lower=1e-6)
    intervals["raw_spot_time"] = intervals["bucket"] - pd.Timedelta(minutes=1)
    intervals = intervals.merge(raw, left_on=["symbol", "raw_spot_time"], right_on=["symbol", "timestamp"], how="left")
    results: list[_Signal] = []
    for strategy in ("intraday_continuation_v1", "vwap_reversion_v1"):
        used: set[tuple[str, object]] = set()
        for row in intervals.sort_values("bucket").itertuples(index=False):
            if pd.isna(row.raw_spot) or pd.isna(row.momentum_z) or pd.isna(row.deviation_z):
                continue
            side = None
            if strategy == "intraday_continuation_v1":
                if row.momentum_z >= 1 and row.close > row.session_vwap:
                    side = "CALL"
                elif row.momentum_z <= -1 and row.close < row.session_vwap:
                    side = "PUT"
            elif abs(row.momentum_z) < 0.5:
                if row.deviation_z <= -1.5:
                    side = "CALL"
                elif row.deviation_z >= 1.5:
                    side = "PUT"
            key = (row.symbol, row.session)
            if side and key not in used and row.bucket.date() >= pd.Timestamp("2024-02-01").date():
                used.add(key)
                results.append(_Signal(strategy, row.symbol, row.bucket, side, float(row.raw_spot)))
    return results


def generate_requests(
    *, data_manifest_path: Path, output_path: Path, observation_minutes: int = 65
) -> Path:
    """Generate O2 leg requests without joining option bars/trades or P&L.

    ``95`` is the separately predeclared 90-minute-exit diagnostic window:
    five minutes for next-minute proxy execution follow the 90-minute policy
    decision.  It does not alter the 60-minute central request manifest.
    """
    if output_path.exists():
        raise GroupARequestError("OPTION_REQUEST_OUTPUT_EXISTS")
    if observation_minutes not in {65, 95}:
        raise GroupARequestError("OPTION_REQUEST_OBSERVATION_WINDOW_INVALID")
    manifest = _load_manifest(data_manifest_path)
    root = data_manifest_path.resolve().parent
    split = _artifact(root, manifest, "stock_bars_split")
    raw = _artifact(root, manifest, "stock_bars_raw")
    contracts = pd.read_parquet(_artifact(root, manifest, "option_contracts"))
    contracts = contracts[(contracts["underlying"].isin(_SYMBOLS)) & (contracts["right"].isin(["CALL", "PUT"])) & (contracts["style"] == "american") & (contracts["multiplier"].astype(str) == "100")].copy()
    contracts["strike_value"] = pd.to_numeric(contracts["strike"], errors="coerce")
    contracts["expiration_date"] = pd.to_datetime(contracts["expiration"]).dt.date
    requests: list[dict[str, Any]] = []
    for signal in _signals(split, raw):
        date = signal.timestamp.date()
        choices = contracts[(contracts["underlying"] == signal.symbol) & (contracts["right"] == signal.side)]
        choices = choices.assign(
            dte=choices["expiration_date"].map(lambda expiration, trade_date=date: (expiration - trade_date).days)
        )
        choices = choices[(choices["dte"] >= 7) & (choices["dte"] <= 14)]
        if choices.empty:
            continue
        expiry = min(choices["expiration_date"])
        choices = choices[choices["expiration_date"] == expiry]
        if signal.side == "CALL":
            long = min(choices.itertuples(index=False), key=lambda row: (abs(row.strike_value - signal.raw_spot), row.strike_value < signal.raw_spot, row.strike_value, row.symbol))
            short = choices[choices["strike_value"] >= long.strike_value * 1.01].sort_values(["strike_value", "symbol"]).head(1)
        else:
            long = min(choices.itertuples(index=False), key=lambda row: (abs(row.strike_value - signal.raw_spot), row.strike_value > signal.raw_spot, -row.strike_value, row.symbol))
            short = choices[choices["strike_value"] <= long.strike_value * 0.99].sort_values(["strike_value", "symbol"], ascending=[False, True]).head(1)
        if short.empty:
            continue
        end = signal.timestamp + timedelta(minutes=observation_minutes)
        requests.append({"strategy": signal.strategy, "underlying": signal.symbol, "side": signal.side, "decision_time": signal.timestamp.tz_convert("UTC").isoformat().replace("+00:00", "Z"), "symbols": sorted([long.symbol, str(short.iloc[0]["symbol"])]), "start": signal.timestamp.tz_convert("UTC").isoformat().replace("+00:00", "Z"), "end": end.tz_convert("UTC").isoformat().replace("+00:00", "Z")})
    requests.sort(key=lambda item: (item["strategy"], item["decision_time"], item["underlying"], item["symbols"]))
    payload = {"schema_version": "group-a-o2-option-requests/v1", "base_data_manifest_hash": manifest["manifest_hash"], "selection_rule": "GROUP_A_FROZEN_O2_7_14_DTE_V1", "observation_minutes": observation_minutes, "requests": [{"request_id": f"{index:06d}", "symbols": item["symbols"], "start": item["start"], "end": item["end"]} for index, item in enumerate(requests, start=1)], "selection_records": requests, "manifest_hash": None}
    payload["manifest_hash"] = canonical_hash({key: value for key, value in payload.items() if key != "manifest_hash"})
    atomic_json(output_path, payload)
    return output_path
