"""Freeze a three-session QQQ relative-strength debit-spread research family."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from packages.contracts.canonical import canonical_hash

from .artifacts import atomic_json
from .group_a_option_requests import GroupARequestError, _artifact, _load_manifest
from .group_a_parallel_v2_option_requests import _latest_before, _regular_bars

_HOLDING_SESSIONS = 3
_RESIDUAL_THRESHOLD = 0.02
_STRATEGY = "qqq_relative_strength_residual_3s_v7"


@dataclass(frozen=True)
class _Signal:
    decision_time: pd.Timestamp
    exit_window_end: pd.Timestamp
    side: str
    raw_spot: float


def _signals(*, split_path: Path, raw_path: Path) -> list[_Signal]:
    """Use only prior completed QQQ/SPY sessions to form a residual signal."""
    split = _regular_bars(split_path)
    raw = _regular_bars(raw_path)
    split_sessions = {
        key: values.reset_index(drop=True)
        for key, values in split.groupby(["symbol", "session"], sort=False)
    }
    raw_sessions = {
        key: values.reset_index(drop=True)
        for key, values in raw.groupby(["symbol", "session"], sort=False)
    }
    sessions = sorted(
        set(session for symbol, session in split_sessions if symbol == "QQQ")
        & set(session for symbol, session in split_sessions if symbol == "SPY")
        & set(session for symbol, session in raw_sessions if symbol == "QQQ")
    )
    closes = {
        symbol: {
            session: float(split_sessions[(symbol, session)].iloc[-1]["close"])
            for session in sessions
        }
        for symbol in ("SPY", "QQQ")
    }
    results: list[_Signal] = []
    for index in range(11, len(sessions) - _HOLDING_SESSIONS):
        session = sessions[index]
        prior = sessions[index - 1]
        lookback = sessions[index - 11]
        residual = math.log(closes["QQQ"][prior] / closes["QQQ"][lookback]) - math.log(
            closes["SPY"][prior] / closes["SPY"][lookback]
        )
        if abs(residual) < _RESIDUAL_THRESHOLD:
            continue
        decision = pd.Timestamp(f"{session} 10:00:00", tz="America/New_York")
        raw_latest = _latest_before(raw_sessions[("QQQ", session)], decision)
        if raw_latest is None:
            continue
        exit_session = sessions[index + _HOLDING_SESSIONS]
        exit_end = pd.Timestamp(f"{exit_session} 14:05:00", tz="America/New_York")
        results.append(
            _Signal(
                decision_time=decision + pd.Timedelta(seconds=1),
                exit_window_end=exit_end,
                side="CALL" if residual > 0 else "PUT",
                raw_spot=float(raw_latest["close"]),
            )
        )
    return results


def generate_requests(*, data_manifest_path: Path, output_path: Path) -> Path:
    """Create the V7 immutable option-observation request manifest without option I/O."""
    if output_path.exists():
        raise GroupARequestError("OPTION_REQUEST_OUTPUT_EXISTS")
    manifest = _load_manifest(data_manifest_path)
    root = data_manifest_path.resolve().parent
    contracts = pd.read_parquet(_artifact(root, manifest, "option_contracts"))
    contracts = contracts[
        (contracts["underlying"] == "QQQ")
        & (contracts["right"].isin(("CALL", "PUT")))
        & (contracts["style"] == "american")
        & (contracts["multiplier"].astype(str) == "100")
    ].copy()
    contracts["strike_value"] = pd.to_numeric(contracts["strike"], errors="coerce")
    contracts["expiration_date"] = pd.to_datetime(contracts["expiration"]).dt.date
    contracts = contracts.dropna(subset=["strike_value", "expiration_date"])
    groups = {
        key: values.reset_index(drop=True)
        for key, values in contracts.groupby(["right", "expiration_date"], sort=True)
    }
    expirations = {
        right: sorted(expiry for grouped_right, expiry in groups if grouped_right == right)
        for right in ("CALL", "PUT")
    }
    records: list[dict[str, Any]] = []
    for signal in _signals(
        split_path=_artifact(root, manifest, "stock_bars_split"),
        raw_path=_artifact(root, manifest, "stock_bars_raw"),
    ):
        expiry = next(
            (value for value in expirations[signal.side] if 7 <= (value - signal.decision_time.date()).days <= 14),
            None,
        )
        if expiry is None:
            continue
        choices = groups[(signal.side, expiry)]
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
                "strategy": _STRATEGY,
                "strategy_family": "SESSION_TIME_EXIT_V7",
                "underlying": "QQQ",
                "side": signal.side,
                "holding_sessions": _HOLDING_SESSIONS,
                "session_exit_clock": "14:00",
                "decision_time": start.isoformat().replace("+00:00", "Z"),
                "symbols": sorted([str(long.symbol), str(short.iloc[0]["symbol"])]),
                "start": start.isoformat().replace("+00:00", "Z"),
                "end": signal.exit_window_end.tz_convert("UTC").isoformat().replace("+00:00", "Z"),
            }
        )
    records.sort(key=lambda item: (item["decision_time"], item["symbols"]))
    payload: dict[str, Any] = {
        "schema_version": "group-a-relative-strength-v7-option-requests/v1",
        "research_status": "EXPANDED_SCOPE_EXPLORATORY_V7_NOT_PROMOTION_ELIGIBLE",
        "base_data_manifest_hash": manifest["manifest_hash"],
        "selection_rule": "GROUP_A_V7_QQQ_MINUS_SPY_10_SESSION_RESIDUAL_3_SESSION_EXIT_V1",
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
