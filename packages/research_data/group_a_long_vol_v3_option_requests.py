"""Freeze Group A V3 long-volatility option observations before option data I/O."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from packages.contracts.canonical import canonical_hash

from .artifacts import atomic_json
from .group_a_option_requests import GroupARequestError, _artifact, _load_manifest
from .group_a_parallel_v2_option_requests import _latest_before, _regular_bars

_OBSERVATION_MINUTES = 95
_SPECS = (
    ("qqq_opening_drive_long_straddle_v3", "QQQ", "drive", 0.0075),
    ("qqq_opening_range_long_straddle_v3", "QQQ", "range", 0.0100),
    ("spy_opening_range_long_straddle_v3", "SPY", "range", 0.0075),
)


@dataclass(frozen=True)
class _Signal:
    strategy: str
    underlying: str
    decision_time: pd.Timestamp
    raw_spot: float


def _signals(*, split_path: Path, raw_path: Path) -> list[_Signal]:
    """Return fixed 10:00 ET long-volatility signals from completed ETF bars."""
    split = _regular_bars(split_path)
    raw = _regular_bars(raw_path)
    raw_sessions = {
        key: values.reset_index(drop=True)
        for key, values in raw.groupby(["symbol", "session"], sort=False)
    }
    results: list[_Signal] = []
    for (symbol, session), bars in split.groupby(["symbol", "session"], sort=True):
        raw_bars = raw_sessions.get((symbol, session))
        if raw_bars is None or bars.empty:
            continue
        decision = pd.Timestamp(f"{session} 10:00:00", tz="America/New_York")
        latest = _latest_before(bars, decision)
        raw_latest = _latest_before(raw_bars, decision)
        if latest is None or raw_latest is None:
            continue
        observed = bars[bars["timestamp"] <= decision]
        first = observed.iloc[0]
        drive = abs(math.log(float(latest["close"]) / float(first["close"])))
        opening_range = float(observed["high"].max() / observed["low"].min() - 1.0)
        for strategy, expected_symbol, feature, threshold in _SPECS:
            if symbol != expected_symbol:
                continue
            value = drive if feature == "drive" else opening_range
            if value >= threshold:
                results.append(
                    _Signal(
                        strategy=strategy,
                        underlying=symbol,
                        decision_time=decision + pd.Timedelta(seconds=1),
                        raw_spot=float(raw_latest["close"]),
                    )
                )
    return sorted(results, key=lambda item: (item.strategy, item.decision_time, item.underlying))


def _nearest_atm(contracts: pd.DataFrame, spot: float) -> str:
    chosen = min(
        contracts.itertuples(index=False),
        key=lambda row: (abs(float(row.strike_value) - spot), float(row.strike_value), row.symbol),
    )
    return str(chosen.symbol)


def generate_requests(*, data_manifest_path: Path, output_path: Path) -> Path:
    """Create a deterministic, option-only long-straddle request manifest."""
    if output_path.exists():
        raise GroupARequestError("OPTION_REQUEST_OUTPUT_EXISTS")
    manifest = _load_manifest(data_manifest_path)
    root = data_manifest_path.resolve().parent
    contracts = pd.read_parquet(_artifact(root, manifest, "option_contracts"))
    contracts = contracts[
        (contracts["underlying"].isin(("SPY", "QQQ")))
        & (contracts["right"].isin(("CALL", "PUT")))
        & (contracts["style"] == "american")
        & (contracts["multiplier"].astype(str) == "100")
        & contracts["tradable"].astype(bool)
    ].copy()
    contracts["strike_value"] = pd.to_numeric(contracts["strike"], errors="coerce")
    contracts["expiration_date"] = pd.to_datetime(contracts["expiration"]).dt.date
    contracts = contracts.dropna(subset=["strike_value", "expiration_date"])
    groups = {
        key: values.reset_index(drop=True)
        for key, values in contracts.groupby(["underlying", "right", "expiration_date"], sort=True)
    }
    expirations = {
        underlying: sorted(
            {
                expiry
                for grouped_underlying, right, expiry in groups
                if grouped_underlying == underlying and right == "CALL"
                and (underlying, "PUT", expiry) in groups
            }
        )
        for underlying in ("SPY", "QQQ")
    }
    records: list[dict[str, Any]] = []
    for signal in _signals(
        split_path=_artifact(root, manifest, "stock_bars_split"),
        raw_path=_artifact(root, manifest, "stock_bars_raw"),
    ):
        expiry = next(
            (value for value in expirations[signal.underlying] if 7 <= (value - signal.decision_time.date()).days <= 14),
            None,
        )
        if expiry is None:
            continue
        call_symbol = _nearest_atm(groups[(signal.underlying, "CALL", expiry)], signal.raw_spot)
        put_symbol = _nearest_atm(groups[(signal.underlying, "PUT", expiry)], signal.raw_spot)
        start = signal.decision_time.tz_convert("UTC")
        records.append(
            {
                "strategy": signal.strategy,
                "strategy_family": "TIME_EXIT_ONLY_V3",
                "underlying": signal.underlying,
                "side": "LONG_VOL",
                "time_exit_minutes": 60,
                "decision_time": start.isoformat().replace("+00:00", "Z"),
                "symbols": sorted((call_symbol, put_symbol)),
                "start": start.isoformat().replace("+00:00", "Z"),
                "end": (start + timedelta(minutes=_OBSERVATION_MINUTES)).isoformat().replace("+00:00", "Z"),
            }
        )
    records.sort(key=lambda item: (item["strategy"], item["decision_time"], item["underlying"], item["symbols"]))
    payload: dict[str, Any] = {
        "schema_version": "group-a-long-vol-v3-option-requests/v1",
        "research_status": "EXPANDED_SCOPE_EXPLORATORY_V3_NOT_PROMOTION_ELIGIBLE",
        "base_data_manifest_hash": manifest["manifest_hash"],
        "selection_rule": "GROUP_A_V3_OPENING_LONG_VOL_NO_LOOKAHEAD_V1",
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
