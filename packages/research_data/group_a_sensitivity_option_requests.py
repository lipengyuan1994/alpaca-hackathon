"""Freeze predeclared Group A sensitivity option-observation requests.

The four non-central variants are a diagnostic grid declared in the Group A
packet.  This module creates requests before any additional option bars or
trades are read; it does not assess outcomes or choose a winning variant.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from packages.contracts.canonical import canonical_hash

from .artifacts import atomic_json
from .group_a_option_requests import GroupARequestError, _artifact, _bars, _load_manifest

_SYMBOLS = ("SPY", "QQQ")
_DECISION_MINUTES = {"10:30", "11:00", "11:30", "12:00", "12:30", "13:00", "13:30", "14:00", "14:30"}
_OBSERVATION_MINUTES = 65
_VARIANTS = (
    ("intraday_continuation_v1_momentum_0_75", "intraday_continuation_v1", "momentum", 0.75),
    ("intraday_continuation_v1_momentum_1_25", "intraday_continuation_v1", "momentum", 1.25),
    ("vwap_reversion_v1_deviation_1_25", "vwap_reversion_v1", "deviation", 1.25),
    ("vwap_reversion_v1_deviation_1_75", "vwap_reversion_v1", "deviation", 1.75),
)


@dataclass(frozen=True)
class _Signal:
    strategy: str
    strategy_family: str
    variant_id: str
    symbol: str
    timestamp: pd.Timestamp
    side: str
    raw_spot: float


def _intervals(split_path: Path, raw_path: Path) -> pd.DataFrame:
    bars = _bars(split_path)
    raw = _bars(raw_path)[["symbol", "timestamp", "close"]].rename(columns={"close": "raw_spot"})
    bars["bucket"] = bars["timestamp"].dt.floor("15min") + pd.Timedelta(minutes=15)
    grouped = bars.groupby(["symbol", "session", "bucket"], sort=True)
    intervals = grouped.agg(close=("close", "last")).reset_index()
    bars = bars.sort_values(["symbol", "timestamp"])
    bars["pv"] = bars["vwap"] * bars["volume"]
    bars["cum_pv"] = bars.groupby(["symbol", "session"])["pv"].cumsum()
    bars["cum_volume"] = bars.groupby(["symbol", "session"])["volume"].cumsum()
    vwap = bars.groupby(["symbol", "session", "bucket"], sort=True).tail(1)[
        ["symbol", "session", "bucket", "cum_pv", "cum_volume"]
    ]
    intervals = intervals.merge(vwap, on=["symbol", "session", "bucket"], how="inner")
    intervals["session_vwap"] = intervals["cum_pv"] / intervals["cum_volume"]
    intervals["time"] = intervals["bucket"].dt.strftime("%H:%M")
    intervals = intervals[intervals["time"].isin(_DECISION_MINUTES)].copy()
    opens = bars.groupby(["symbol", "session"], sort=True)["open"].first().rename("session_open")
    intervals = intervals.join(opens, on=["symbol", "session"])
    by_time = intervals.set_index(["symbol", "session", "time"])["close"]
    reference = []
    for row in intervals.itertuples(index=False):
        reference.append(
            row.session_open
            if row.time == "10:30"
            else by_time.get((row.symbol, row.session, (row.bucket - pd.Timedelta(minutes=60)).strftime("%H:%M")), float("nan"))
        )
    intervals["r60"] = (intervals["close"] / pd.Series(reference, index=intervals.index)).map(math.log)
    intervals["deviation"] = (intervals["close"] / intervals["session_vwap"]).map(math.log)
    intervals = intervals.sort_values(["symbol", "time", "session"])
    for source, target in (("r60", "momentum_z"), ("deviation", "deviation_z")):
        prior = intervals.groupby(["symbol", "time"], sort=False)[source].shift(1)
        mean = prior.groupby([intervals["symbol"], intervals["time"]], sort=False).transform(
            lambda values: values.rolling(20, min_periods=20).mean()
        )
        std = prior.groupby([intervals["symbol"], intervals["time"]], sort=False).transform(
            lambda values: values.rolling(20, min_periods=20).std(ddof=1)
        )
        intervals[target] = (intervals[source] - mean) / std.clip(lower=1e-6)
    intervals["raw_spot_time"] = intervals["bucket"] - pd.Timedelta(minutes=1)
    return intervals.merge(raw, left_on=["symbol", "raw_spot_time"], right_on=["symbol", "timestamp"], how="left")


def _signals(split_path: Path, raw_path: Path) -> list[_Signal]:
    results: list[_Signal] = []
    intervals = _intervals(split_path, raw_path).sort_values("bucket")
    for strategy, family, kind, threshold in _VARIANTS:
        used: set[tuple[str, object]] = set()
        for row in intervals.itertuples(index=False):
            if pd.isna(row.raw_spot) or pd.isna(row.momentum_z) or pd.isna(row.deviation_z):
                continue
            side: str | None = None
            if kind == "momentum":
                if row.momentum_z >= threshold and row.close > row.session_vwap:
                    side = "CALL"
                elif row.momentum_z <= -threshold and row.close < row.session_vwap:
                    side = "PUT"
            elif abs(row.momentum_z) < 0.5:
                if row.deviation_z <= -threshold:
                    side = "CALL"
                elif row.deviation_z >= threshold:
                    side = "PUT"
            key = (row.symbol, row.session)
            if side and key not in used and row.bucket.date() >= pd.Timestamp("2024-02-01").date():
                used.add(key)
                results.append(_Signal(strategy, family, strategy, row.symbol, row.bucket, side, float(row.raw_spot)))
    return results


def generate_requests(*, data_manifest_path: Path, output_path: Path) -> Path:
    """Create immutable request records for every non-central predeclared variant."""
    if output_path.exists():
        raise GroupARequestError("OPTION_REQUEST_OUTPUT_EXISTS")
    manifest = _load_manifest(data_manifest_path)
    root = data_manifest_path.resolve().parent
    contracts = pd.read_parquet(_artifact(root, manifest, "option_contracts"))
    contracts = contracts[
        (contracts["underlying"].isin(_SYMBOLS))
        & (contracts["right"].isin(("CALL", "PUT")))
        & (contracts["style"] == "american")
        & (contracts["multiplier"].astype(str) == "100")
    ].copy()
    contracts["strike_value"] = pd.to_numeric(contracts["strike"], errors="coerce")
    contracts["expiration_date"] = pd.to_datetime(contracts["expiration"]).dt.date
    records: list[dict[str, Any]] = []
    for signal in _signals(_artifact(root, manifest, "stock_bars_split"), _artifact(root, manifest, "stock_bars_raw")):
        date = signal.timestamp.date()
        choices = contracts[(contracts["underlying"] == signal.symbol) & (contracts["right"] == signal.side)].copy()
        choices["dte"] = choices["expiration_date"].map(lambda expiry, trade_date=date: (expiry - trade_date).days)
        choices = choices[(choices["dte"] >= 7) & (choices["dte"] <= 14)]
        if choices.empty:
            continue
        choices = choices[choices["expiration_date"] == min(choices["expiration_date"])]
        if signal.side == "CALL":
            long = min(choices.itertuples(index=False), key=lambda row: (abs(row.strike_value - signal.raw_spot), row.strike_value < signal.raw_spot, row.strike_value, row.symbol))
            short = choices[choices["strike_value"] >= long.strike_value * 1.01].sort_values(["strike_value", "symbol"]).head(1)
        else:
            long = min(choices.itertuples(index=False), key=lambda row: (abs(row.strike_value - signal.raw_spot), row.strike_value > signal.raw_spot, -row.strike_value, row.symbol))
            short = choices[choices["strike_value"] <= long.strike_value * 0.99].sort_values(["strike_value", "symbol"], ascending=[False, True]).head(1)
        if short.empty:
            continue
        start = signal.timestamp.tz_convert("UTC")
        records.append(
            {
                "strategy": signal.strategy,
                "strategy_family": signal.strategy_family,
                "variant_id": signal.variant_id,
                "underlying": signal.symbol,
                "side": signal.side,
                "decision_time": start.isoformat().replace("+00:00", "Z"),
                "symbols": sorted([long.symbol, str(short.iloc[0]["symbol"])]),
                "start": start.isoformat().replace("+00:00", "Z"),
                "end": (start + timedelta(minutes=_OBSERVATION_MINUTES)).isoformat().replace("+00:00", "Z"),
            }
        )
    records.sort(key=lambda item: (item["strategy"], item["decision_time"], item["underlying"], item["symbols"]))
    payload: dict[str, Any] = {
        "schema_version": "group-a-sensitivity-option-requests/v1",
        "research_status": "PREDECLARED_SENSITIVITY_DIAGNOSTIC_ONLY",
        "base_data_manifest_hash": manifest["manifest_hash"],
        "baseline_request_manifest_hash": "sha256:22c0fb0bf044c7f9a40dee73c9bc1a596e99ef2dd1be6aa425a2d7e0206398ec",
        "selection_rule": "GROUP_A_PACKET_PREDECLARED_SENSITIVITY_GRID_O2_V1",
        "observation_minutes": _OBSERVATION_MINUTES,
        "requests": [
            {"request_id": f"{index:06d}", "symbols": item["symbols"], "start": item["start"], "end": item["end"]}
            for index, item in enumerate(records, start=1)
        ],
        "selection_records": records,
        "manifest_hash": None,
    }
    payload["manifest_hash"] = canonical_hash({key: value for key, value in payload.items() if key != "manifest_hash"})
    atomic_json(output_path, payload)
    return output_path
