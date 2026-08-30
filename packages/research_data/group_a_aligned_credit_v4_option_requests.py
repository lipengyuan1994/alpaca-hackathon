"""Freeze correctly direction-aligned Group A V4 credit-spread observations."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from packages.contracts.canonical import canonical_hash

from .artifacts import atomic_json
from .group_a_option_requests import GroupARequestError, _artifact, _load_manifest
from .group_a_parallel_v2_option_requests import _OBSERVATION_MINUTES, _signals


def _credit_side(debit_side: str) -> str:
    if debit_side == "CALL":
        return "PUT"
    if debit_side == "PUT":
        return "CALL"
    raise GroupARequestError("CREDIT_SIGNAL_DIRECTION_INVALID")


def generate_requests(*, data_manifest_path: Path, output_path: Path) -> Path:
    """Create a predeclared V4 request manifest without option market-data I/O."""
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
        _artifact(root, manifest, "stock_bars_split"),
        _artifact(root, manifest, "stock_bars_raw"),
    ):
        side = _credit_side(signal.side)
        expiry = next(
            (value for value in expirations[(signal.symbol, side)] if 7 <= (value - signal.decision_time.date()).days <= 14),
            None,
        )
        if expiry is None:
            continue
        choices = groups[(signal.symbol, side, expiry)]
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
                "strategy": signal.strategy.replace("_v2", "_aligned_credit_v4"),
                "strategy_family": "TIME_EXIT_ONLY_V4",
                "underlying": signal.symbol,
                "side": side,
                "time_exit_minutes": signal.time_exit_minutes,
                "decision_time": start.isoformat().replace("+00:00", "Z"),
                "symbols": sorted([str(short.symbol), str(hedge.iloc[0]["symbol"])]),
                "start": start.isoformat().replace("+00:00", "Z"),
                "end": (start + timedelta(minutes=_OBSERVATION_MINUTES)).isoformat().replace("+00:00", "Z"),
            }
        )
    records.sort(key=lambda item: (item["strategy"], item["decision_time"], item["underlying"], item["symbols"]))
    payload: dict[str, Any] = {
        "schema_version": "group-a-aligned-credit-v4-option-requests/v1",
        "research_status": "EXPANDED_SCOPE_EXPLORATORY_V4_NOT_PROMOTION_ELIGIBLE",
        "base_data_manifest_hash": manifest["manifest_hash"],
        "selection_rule": "GROUP_A_V4_DIRECTION_ALIGNED_DEFINED_RISK_CREDIT_V1",
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
