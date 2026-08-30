"""Freeze parallel, option-only Group A V2 research requests before option data is read."""

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

_SYMBOLS = ("SPY", "QQQ")
_OBSERVATION_MINUTES = 95
_SPECS = (
    ("late_momentum_v2", "14:30", 60, 0.004, False),
    ("morning_breakout_momentum_v2", "11:00", 90, 0.0025, False),
    ("opening_drive_reversal_v2", "10:00", 60, 0.008, True),
)


@dataclass(frozen=True)
class _Signal:
    strategy: str
    symbol: str
    side: str
    decision_time: pd.Timestamp
    raw_spot: float
    time_exit_minutes: int


def _regular_bars(path: Path) -> pd.DataFrame:
    bars = pd.read_parquet(path, columns=["symbol", "event_time", "close", "high", "low"])
    bars = bars[bars["symbol"].isin(_SYMBOLS)].copy()
    bars["timestamp"] = pd.to_datetime(bars["event_time"], utc=True).dt.tz_convert("America/New_York")
    for field in ("close", "high", "low"):
        bars[field] = pd.to_numeric(bars[field], errors="coerce")
    bars = bars.dropna(subset=["close", "high", "low"])
    bars = bars[
        (bars["timestamp"].dt.weekday < 5)
        & (bars["timestamp"].dt.time >= pd.Timestamp("09:30").time())
        & (bars["timestamp"].dt.time < pd.Timestamp("16:00").time())
    ].copy()
    bars["session"] = bars["timestamp"].dt.date
    return bars.sort_values(["symbol", "timestamp"], kind="stable")


def _latest_before(bars: pd.DataFrame, decision: pd.Timestamp) -> pd.Series | None:
    eligible = bars[bars["timestamp"] <= decision]
    if eligible.empty:
        return None
    result = eligible.iloc[-1]
    return result if decision - result["timestamp"] <= pd.Timedelta(minutes=3) else None


def _signals(split_path: Path, raw_path: Path) -> list[_Signal]:
    split = _regular_bars(split_path)
    raw = _regular_bars(raw_path)
    basic: list[_Signal] = []
    compression_rows: list[dict[str, Any]] = []
    raw_sessions = {
        key: values.reset_index(drop=True)
        for key, values in raw.groupby(["symbol", "session"], sort=False)
    }
    for (symbol, session), session_bars in split.groupby(["symbol", "session"], sort=True):
        session_bars = session_bars.reset_index(drop=True)
        raw_session = raw_sessions.get((symbol, session))
        if raw_session is None:
            continue
        first = session_bars.iloc[0]
        for strategy, clock, hold, threshold, reverse in _SPECS:
            decision = pd.Timestamp(f"{session} {clock}:00", tz="America/New_York")
            latest = _latest_before(session_bars, decision)
            raw_latest = _latest_before(raw_session, decision)
            if latest is None or raw_latest is None:
                continue
            move = math.log(float(latest["close"]) / float(first["close"]))
            if abs(move) < threshold:
                continue
            positive = move > 0
            side = "PUT" if (positive and reverse) or (not positive and not reverse) else "CALL"
            basic.append(
                _Signal(
                    strategy=strategy,
                    symbol=symbol,
                    side=side,
                    decision_time=decision + pd.Timedelta(seconds=1),
                    raw_spot=float(raw_latest["close"]),
                    time_exit_minutes=hold,
                )
            )
        decision = pd.Timestamp(f"{session} 10:30:00", tz="America/New_York")
        latest = _latest_before(session_bars, decision)
        raw_latest = _latest_before(raw_session, decision)
        if latest is not None and raw_latest is not None:
            observed = session_bars[session_bars["timestamp"] <= decision]
            compression_rows.append(
                {
                    "symbol": symbol,
                    "session": session,
                    "range": float(observed["high"].max() - observed["low"].min()),
                    "move": math.log(float(latest["close"]) / float(first["close"])),
                    "raw_spot": float(raw_latest["close"]),
                    "decision_time": decision + pd.Timedelta(seconds=1),
                }
            )
    compression = pd.DataFrame(compression_rows)
    results = basic
    if not compression.empty:
        compression = compression.sort_values(["symbol", "session"], kind="stable")
        prior = compression.groupby("symbol", sort=False)["range"].shift(1)
        compression["prior_median_range"] = prior.groupby(compression["symbol"], sort=False).transform(
            lambda values: values.rolling(20, min_periods=20).median()
        )
        for row in compression.itertuples(index=False):
            if pd.isna(row.prior_median_range) or row.range > 0.65 * row.prior_median_range or abs(row.move) < 0.0015:
                continue
            results.append(
                _Signal(
                    strategy="range_compression_trend_v2",
                    symbol=row.symbol,
                    side="CALL" if row.move > 0 else "PUT",
                    decision_time=row.decision_time,
                    raw_spot=float(row.raw_spot),
                    time_exit_minutes=60,
                )
            )
    return sorted(results, key=lambda item: (item.strategy, item.decision_time, item.symbol))


def generate_requests(*, data_manifest_path: Path, output_path: Path) -> Path:
    """Generate all expanded-scope Group A V2 requests without option I/O."""
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
    contract_groups = {
        key: values.reset_index(drop=True)
        for key, values in contracts.groupby(["underlying", "right", "expiration_date"], sort=True)
    }
    expirations = {
        (underlying, right): sorted(
            expiry
            for grouped_underlying, grouped_right, expiry in contract_groups
            if grouped_underlying == underlying and grouped_right == right
        )
        for underlying in _SYMBOLS
        for right in ("CALL", "PUT")
    }
    records: list[dict[str, Any]] = []
    for signal in _signals(_artifact(root, manifest, "stock_bars_split"), _artifact(root, manifest, "stock_bars_raw")):
        trade_date = signal.decision_time.date()
        expiry = next(
            (value for value in expirations[(signal.symbol, signal.side)] if 7 <= (value - trade_date).days <= 14),
            None,
        )
        if expiry is None:
            continue
        choices = contract_groups[(signal.symbol, signal.side, expiry)]
        if signal.side == "CALL":
            long = min(choices.itertuples(index=False), key=lambda row: (abs(row.strike_value - signal.raw_spot), row.strike_value < signal.raw_spot, row.strike_value, row.symbol))
            short = choices[choices["strike_value"] >= long.strike_value * 1.01].sort_values(["strike_value", "symbol"]).head(1)
        else:
            long = min(choices.itertuples(index=False), key=lambda row: (abs(row.strike_value - signal.raw_spot), row.strike_value > signal.raw_spot, -row.strike_value, row.symbol))
            short = choices[choices["strike_value"] <= long.strike_value * 0.99].sort_values(["strike_value", "symbol"], ascending=[False, True]).head(1)
        if short.empty:
            continue
        start = signal.decision_time.tz_convert("UTC")
        records.append(
            {
                "strategy": signal.strategy,
                "strategy_family": "TIME_EXIT_ONLY_V2",
                "underlying": signal.symbol,
                "side": signal.side,
                "time_exit_minutes": signal.time_exit_minutes,
                "decision_time": start.isoformat().replace("+00:00", "Z"),
                "symbols": sorted([long.symbol, str(short.iloc[0]["symbol"])]),
                "start": start.isoformat().replace("+00:00", "Z"),
                "end": (start + timedelta(minutes=_OBSERVATION_MINUTES)).isoformat().replace("+00:00", "Z"),
            }
        )
    records.sort(key=lambda item: (item["strategy"], item["decision_time"], item["underlying"], item["symbols"]))
    payload: dict[str, Any] = {
        "schema_version": "group-a-parallel-v2-option-requests/v1",
        "research_status": "EXPANDED_SCOPE_EXPLORATORY_V2_NOT_PROMOTION_ELIGIBLE",
        "base_data_manifest_hash": manifest["manifest_hash"],
        "selection_rule": "GROUP_A_PARALLEL_V2_NO_LOOKAHEAD_TIME_EXIT_ONLY_V1",
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
