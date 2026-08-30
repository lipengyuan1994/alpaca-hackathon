"""Freeze 240-minute Group A V5 continuation-spread observations."""

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

_OBSERVATION_MINUTES = 245
_SPECS = (
    ("qqq_opening_drive_continuation_240m_v5", "QQQ", 0.0075),
    ("spy_opening_drive_continuation_240m_v5", "SPY", 0.0050),
)


@dataclass(frozen=True)
class _Signal:
    strategy: str
    underlying: str
    side: str
    decision_time: pd.Timestamp
    raw_spot: float


def _signals(*, split_path: Path, raw_path: Path) -> list[_Signal]:
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
        first = bars[bars["timestamp"] <= decision].iloc[0]
        move = math.log(float(latest["close"]) / float(first["close"]))
        for strategy, expected_symbol, threshold in _SPECS:
            if symbol == expected_symbol and abs(move) >= threshold:
                results.append(
                    _Signal(
                        strategy=strategy,
                        underlying=symbol,
                        side="CALL" if move > 0 else "PUT",
                        decision_time=decision + pd.Timedelta(seconds=1),
                        raw_spot=float(raw_latest["close"]),
                    )
                )
    return sorted(results, key=lambda item: (item.strategy, item.decision_time, item.underlying))


def generate_requests(*, data_manifest_path: Path, output_path: Path) -> Path:
    """Create an immutable V5 debit-spread request manifest before option I/O."""
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
    ].copy()
    contracts["strike_value"] = pd.to_numeric(contracts["strike"], errors="coerce")
    contracts["expiration_date"] = pd.to_datetime(contracts["expiration"]).dt.date
    contracts = contracts.dropna(subset=["strike_value", "expiration_date"])
    groups = {
        key: values.reset_index(drop=True)
        for key, values in contracts.groupby(["underlying", "right", "expiration_date"], sort=True)
    }
    expirations = {
        (underlying, right): sorted(
            expiry
            for grouped_underlying, grouped_right, expiry in groups
            if grouped_underlying == underlying and grouped_right == right
        )
        for underlying in ("SPY", "QQQ")
        for right in ("CALL", "PUT")
    }
    records: list[dict[str, Any]] = []
    for signal in _signals(
        split_path=_artifact(root, manifest, "stock_bars_split"),
        raw_path=_artifact(root, manifest, "stock_bars_raw"),
    ):
        expiry = next(
            (value for value in expirations[(signal.underlying, signal.side)] if 7 <= (value - signal.decision_time.date()).days <= 14),
            None,
        )
        if expiry is None:
            continue
        choices = groups[(signal.underlying, signal.side, expiry)]
        if signal.side == "CALL":
            long = min(
                choices.itertuples(index=False),
                key=lambda row: (abs(row.strike_value - signal.raw_spot), row.strike_value < signal.raw_spot, row.strike_value, row.symbol),
            )
            short = choices[choices["strike_value"] >= long.strike_value * 1.01].sort_values(["strike_value", "symbol"]).head(1)
        else:
            long = min(
                choices.itertuples(index=False),
                key=lambda row: (abs(row.strike_value - signal.raw_spot), row.strike_value > signal.raw_spot, -row.strike_value, row.symbol),
            )
            short = choices[choices["strike_value"] <= long.strike_value * 0.99].sort_values(["strike_value", "symbol"], ascending=[False, True]).head(1)
        if short.empty:
            continue
        start = signal.decision_time.tz_convert("UTC")
        records.append(
            {
                "strategy": signal.strategy,
                "strategy_family": "TIME_EXIT_ONLY_V5",
                "underlying": signal.underlying,
                "side": signal.side,
                "time_exit_minutes": 240,
                "decision_time": start.isoformat().replace("+00:00", "Z"),
                "symbols": sorted([str(long.symbol), str(short.iloc[0]["symbol"])]),
                "start": start.isoformat().replace("+00:00", "Z"),
                "end": (start + timedelta(minutes=_OBSERVATION_MINUTES)).isoformat().replace("+00:00", "Z"),
            }
        )
    records.sort(key=lambda item: (item["strategy"], item["decision_time"], item["underlying"], item["symbols"]))
    payload: dict[str, Any] = {
        "schema_version": "group-a-long-horizon-v5-option-requests/v1",
        "research_status": "EXPANDED_SCOPE_EXPLORATORY_V5_NOT_PROMOTION_ELIGIBLE",
        "base_data_manifest_hash": manifest["manifest_hash"],
        "selection_rule": "GROUP_A_V5_240_MINUTE_OPENING_DRIVE_CONTINUATION_V1",
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
