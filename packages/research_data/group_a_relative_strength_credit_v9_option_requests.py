"""Freeze V9 three-session direction-aligned credit-spread observations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from packages.contracts.canonical import canonical_hash

from .artifacts import atomic_json
from .group_a_aligned_credit_v4_option_requests import _credit_side
from .group_a_option_requests import GroupARequestError, _artifact, _load_manifest
from .group_a_relative_strength_v7_option_requests import _HOLDING_SESSIONS, _signals


def generate_requests(*, data_manifest_path: Path, output_path: Path) -> Path:
    """Create an immutable V9 request manifest before option market-data I/O."""
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
        side = _credit_side(signal.side)
        expiry = next(
            (value for value in expirations[side] if 7 <= (value - signal.decision_time.date()).days <= 14),
            None,
        )
        if expiry is None:
            continue
        choices = groups[(side, expiry)]
        if side == "CALL":
            short = min(
                choices.itertuples(index=False),
                key=lambda row: (abs(row.strike_value - signal.raw_spot), row.strike_value < signal.raw_spot, row.strike_value, row.symbol),
            )
            hedge = choices[choices["strike_value"] >= short.strike_value * 1.01].sort_values(["strike_value", "symbol"]).head(1)
        else:
            short = min(
                choices.itertuples(index=False),
                key=lambda row: (abs(row.strike_value - signal.raw_spot), row.strike_value > signal.raw_spot, -row.strike_value, row.symbol),
            )
            hedge = choices[choices["strike_value"] <= short.strike_value * 0.99].sort_values(["strike_value", "symbol"], ascending=[False, True]).head(1)
        if hedge.empty:
            continue
        start = signal.decision_time.tz_convert("UTC")
        records.append(
            {
                "strategy": "qqq_relative_strength_residual_3s_aligned_credit_v9",
                "strategy_family": "SESSION_TIME_EXIT_V9",
                "underlying": "QQQ",
                "side": side,
                "holding_sessions": _HOLDING_SESSIONS,
                "session_exit_clock": "14:00",
                "decision_time": start.isoformat().replace("+00:00", "Z"),
                "symbols": sorted([str(short.symbol), str(hedge.iloc[0]["symbol"])]),
                "start": start.isoformat().replace("+00:00", "Z"),
                "end": signal.exit_window_end.tz_convert("UTC").isoformat().replace("+00:00", "Z"),
            }
        )
    records.sort(key=lambda item: (item["decision_time"], item["symbols"]))
    payload: dict[str, Any] = {
        "schema_version": "group-a-relative-strength-credit-v9-option-requests/v1",
        "research_status": "EXPANDED_SCOPE_EXPLORATORY_V9_NOT_PROMOTION_ELIGIBLE",
        "base_data_manifest_hash": manifest["manifest_hash"],
        "selection_rule": "GROUP_A_V9_V7_SIGNAL_DIRECTION_ALIGNED_CREDIT_3_SESSION_EXIT_V1",
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
