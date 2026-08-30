"""Freeze stock-collateralized wheel research observations before option I/O.

This is deliberately a research-only input generator.  It does not call a
broker, inspect an account, select a live contract, or authorize a stock or
option order.  The paired weekly candidates allow the later replay to follow
the deterministic CSP -> assigned shares -> covered-call lifecycle without
choosing a contract after observing option prices.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from packages.contracts.canonical import canonical_hash

from .artifacts import atomic_json
from .group_a_option_requests import GroupARequestError, _artifact, _load_manifest
from .group_a_parallel_v2_option_requests import _latest_before, _regular_bars

_UNDERLYINGS = ("SPY", "QQQ")
_ENTRY_CLOCK = "10:00"
_MIN_DTE = 7
_MAX_DTE = 14
_PUT_MONEYNESS = 0.98
_CALL_MONEYNESS = 1.02


@dataclass(frozen=True)
class _WeeklySlot:
    underlying: str
    decision_time: pd.Timestamp
    raw_spot: float


def _weekly_slots(*, raw_path: Path) -> list[_WeeklySlot]:
    """Return the first eligible regular-session slot for each symbol/week."""
    bars = _regular_bars(raw_path)
    results: list[_WeeklySlot] = []
    for (underlying, session), session_bars in bars.groupby(["symbol", "session"], sort=True):
        decision = pd.Timestamp(f"{session} {_ENTRY_CLOCK}:00", tz="America/New_York")
        latest = _latest_before(session_bars, decision)
        if latest is None:
            continue
        week = decision.isocalendar().year, decision.isocalendar().week
        results.append(
            _WeeklySlot(
                underlying=str(underlying),
                decision_time=decision + pd.Timedelta(seconds=1),
                raw_spot=float(latest["close"]),
            )
        )
    by_week: dict[tuple[str, int, int], _WeeklySlot] = {}
    for slot in results:
        year, week, _ = slot.decision_time.isocalendar()
        by_week.setdefault((slot.underlying, int(year), int(week)), slot)
    return sorted(by_week.values(), key=lambda item: (item.decision_time, item.underlying))


def _expiry_end(expiry: object) -> pd.Timestamp:
    return pd.Timestamp(f"{expiry} 16:05:00", tz="America/New_York").tz_convert("UTC")


def _select_put(choices: pd.DataFrame, spot: float) -> str | None:
    eligible = choices[choices["strike_value"] <= spot * _PUT_MONEYNESS]
    if eligible.empty:
        return None
    return str(eligible.sort_values(["strike_value", "symbol"], ascending=[False, True]).iloc[0]["symbol"])


def _select_call(choices: pd.DataFrame, spot: float) -> str | None:
    eligible = choices[choices["strike_value"] >= spot * _CALL_MONEYNESS]
    if eligible.empty:
        return None
    return str(eligible.sort_values(["strike_value", "symbol"], ascending=[True, True]).iloc[0]["symbol"])


def generate_requests(*, data_manifest_path: Path, output_path: Path) -> Path:
    """Create a fixed weekly SPY/QQQ CSP/covered-call research request set."""
    if output_path.exists():
        raise GroupARequestError("OPTION_REQUEST_OUTPUT_EXISTS")
    manifest = _load_manifest(data_manifest_path)
    root = data_manifest_path.resolve().parent
    contracts = pd.read_parquet(_artifact(root, manifest, "option_contracts"))
    contracts = contracts[
        (contracts["underlying"].isin(_UNDERLYINGS))
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
    expiries = {
        underlying: sorted(
            expiry
            for symbol, right, expiry in groups
            if symbol == underlying and right == "PUT" and (underlying, "CALL", expiry) in groups
        )
        for underlying in _UNDERLYINGS
    }
    records: list[dict[str, Any]] = []
    for slot in _weekly_slots(raw_path=_artifact(root, manifest, "stock_bars_raw")):
        expiry = next(
            (value for value in expiries[slot.underlying] if _MIN_DTE <= (value - slot.decision_time.date()).days <= _MAX_DTE),
            None,
        )
        if expiry is None:
            continue
        put_symbol = _select_put(groups[(slot.underlying, "PUT", expiry)], slot.raw_spot)
        call_symbol = _select_call(groups[(slot.underlying, "CALL", expiry)], slot.raw_spot)
        if put_symbol is None or call_symbol is None:
            continue
        start = slot.decision_time.tz_convert("UTC")
        records.append(
            {
                "strategy": f"{slot.underlying.lower()}_wheel_csp_cc_v12",
                "strategy_family": "RESEARCH_ONLY_WHEEL_V12",
                "underlying": slot.underlying,
                "decision_time": start.isoformat().replace("+00:00", "Z"),
                "expiry": str(expiry),
                "expiry_time": _expiry_end(expiry).isoformat().replace("+00:00", "Z"),
                "put_symbol": put_symbol,
                "call_symbol": call_symbol,
                "put_moneyness": _PUT_MONEYNESS,
                "call_moneyness": _CALL_MONEYNESS,
                "take_profit_fraction": 0.15,
                "symbols": sorted((put_symbol, call_symbol)),
                "start": start.isoformat().replace("+00:00", "Z"),
                "end": _expiry_end(expiry).isoformat().replace("+00:00", "Z"),
            }
        )
    records.sort(key=lambda item: (item["decision_time"], item["underlying"]))
    payload: dict[str, Any] = {
        "schema_version": "group-a-wheel-v12-option-requests/v1",
        "research_status": "RESEARCH_ONLY_STOCK_COLLATERALIZED_WHEEL_V12_NOT_INTEGRATION_ELIGIBLE",
        "base_data_manifest_hash": manifest["manifest_hash"],
        "selection_rule": "GROUP_A_V12_WEEKLY_2PCT_OTM_CSP_OR_COVERED_CALL_15PCT_PREMIUM_TAKE_PROFIT_V1",
        "position_unit": "ONE_OPTION_CONTRACT_OR_100_ASSIGNED_SHARES",
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
