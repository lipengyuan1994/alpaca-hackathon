"""Freeze V10 same-strike calendar-spread observations for the V7 signal."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from packages.contracts.canonical import canonical_hash

from .artifacts import atomic_json
from .group_a_option_requests import GroupARequestError, _artifact, _load_manifest
from .group_a_relative_strength_v7_option_requests import _HOLDING_SESSIONS, _signals


def _atm_contract(contracts: pd.DataFrame, spot: float) -> Any:
    return min(
        contracts.itertuples(index=False),
        key=lambda row: (abs(float(row.strike_value) - spot), float(row.strike_value), row.symbol),
    )


def generate_requests(*, data_manifest_path: Path, output_path: Path) -> Path:
    """Create an immutable V10 calendar request manifest before option I/O."""
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
        trade_date = signal.decision_time.date()
        near_expiry = next((value for value in expirations[signal.side] if 7 <= (value - trade_date).days <= 14), None)
        far_expiry = next((value for value in expirations[signal.side] if 15 <= (value - trade_date).days <= 21), None)
        if near_expiry is None or far_expiry is None:
            continue
        far = _atm_contract(groups[(signal.side, far_expiry)], signal.raw_spot)
        near = groups[(signal.side, near_expiry)]
        near = near[near["strike_value"] == float(far.strike_value)].sort_values("symbol").head(1)
        if near.empty:
            continue
        start = signal.decision_time.tz_convert("UTC")
        records.append(
            {
                "strategy": "qqq_relative_strength_residual_3s_calendar_v10",
                "strategy_family": "SESSION_TIME_EXIT_V10",
                "underlying": "QQQ",
                "side": signal.side,
                "holding_sessions": _HOLDING_SESSIONS,
                "session_exit_clock": "14:00",
                "decision_time": start.isoformat().replace("+00:00", "Z"),
                "long_symbol": str(far.symbol),
                "short_symbol": str(near.iloc[0]["symbol"]),
                "symbols": sorted([str(far.symbol), str(near.iloc[0]["symbol"])]),
                "start": start.isoformat().replace("+00:00", "Z"),
                "end": signal.exit_window_end.tz_convert("UTC").isoformat().replace("+00:00", "Z"),
            }
        )
    records.sort(key=lambda item: (item["decision_time"], item["symbols"]))
    payload: dict[str, Any] = {
        "schema_version": "group-a-relative-strength-calendar-v10-option-requests/v1",
        "research_status": "EXPANDED_SCOPE_EXPLORATORY_V10_NOT_PROMOTION_ELIGIBLE",
        "base_data_manifest_hash": manifest["manifest_hash"],
        "selection_rule": "GROUP_A_V10_V7_SIGNAL_SAME_STRIKE_7_14DTE_SHORT_15_21DTE_LONG_V1",
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
