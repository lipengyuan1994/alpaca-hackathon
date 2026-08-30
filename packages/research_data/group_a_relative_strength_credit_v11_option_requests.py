"""Freeze a five-session V11 credit-spread horizon from V7 residual signals."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pandas as pd

from packages.contracts.canonical import canonical_hash

from .artifacts import atomic_json
from .group_a_aligned_credit_v4_option_requests import _credit_side
from .group_a_option_requests import GroupARequestError, _artifact, _load_manifest
from .group_a_parallel_v2_option_requests import _latest_before, _regular_bars
from .group_a_proxy_backtest import _load

_HOLDING_SESSIONS = 5


def generate_requests(
    *, data_manifest_path: Path, v7_request_path: Path, output_path: Path
) -> Path:
    """Create the immutable five-session V11 request manifest before option I/O."""
    if output_path.exists():
        raise GroupARequestError("OPTION_REQUEST_OUTPUT_EXISTS")
    manifest = _load_manifest(data_manifest_path)
    source = _load(v7_request_path)
    if source.get("base_data_manifest_hash") != manifest.get("manifest_hash"):
        raise GroupARequestError("V11_SOURCE_BASE_MANIFEST_MISMATCH")
    root = data_manifest_path.resolve().parent
    raw = _regular_bars(_artifact(root, manifest, "stock_bars_raw"))
    raw_sessions = {
        session: values.reset_index(drop=True)
        for (symbol, session), values in raw.groupby(["symbol", "session"], sort=False)
        if symbol == "QQQ"
    }
    sessions = sorted(raw_sessions)
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
    for source_record in deepcopy(source["selection_records"]):
        decision = pd.Timestamp(source_record["decision_time"])
        session = decision.tz_convert("America/New_York").date()
        if session not in raw_sessions:
            continue
        target_index = sessions.index(session) + _HOLDING_SESSIONS
        if target_index >= len(sessions):
            continue
        raw_latest = _latest_before(raw_sessions[session], decision.tz_convert("America/New_York"))
        if raw_latest is None:
            continue
        side = _credit_side(str(source_record["side"]))
        expiry = next(
            (value for value in expirations[side] if 7 <= (value - session).days <= 14), None
        )
        if expiry is None:
            continue
        choices = groups[(side, expiry)]
        spot = float(raw_latest["close"])
        if side == "CALL":
            short = min(
                choices.itertuples(index=False),
                key=lambda row: (abs(row.strike_value - spot), row.strike_value < spot, row.strike_value, row.symbol),
            )
            hedge = choices[choices["strike_value"] >= short.strike_value * 1.01].sort_values(["strike_value", "symbol"]).head(1)
        else:
            short = min(
                choices.itertuples(index=False),
                key=lambda row: (abs(row.strike_value - spot), row.strike_value > spot, -row.strike_value, row.symbol),
            )
            hedge = choices[choices["strike_value"] <= short.strike_value * 0.99].sort_values(["strike_value", "symbol"], ascending=[False, True]).head(1)
        if hedge.empty:
            continue
        end = pd.Timestamp(f"{sessions[target_index]} 14:05:00", tz="America/New_York").tz_convert("UTC")
        records.append(
            {
                "strategy": "qqq_relative_strength_residual_5s_aligned_credit_v11",
                "strategy_family": "SESSION_TIME_EXIT_V11",
                "underlying": "QQQ",
                "side": side,
                "holding_sessions": _HOLDING_SESSIONS,
                "session_exit_clock": "14:00",
                "decision_time": decision.isoformat().replace("+00:00", "Z"),
                "symbols": sorted([str(short.symbol), str(hedge.iloc[0]["symbol"])]),
                "start": decision.isoformat().replace("+00:00", "Z"),
                "end": end.isoformat().replace("+00:00", "Z"),
            }
        )
    records.sort(key=lambda item: (item["decision_time"], item["symbols"]))
    payload: dict[str, Any] = {
        "schema_version": "group-a-relative-strength-credit-v11-option-requests/v1",
        "research_status": "EXPANDED_SCOPE_EXPLORATORY_V11_NOT_PROMOTION_ELIGIBLE",
        "base_data_manifest_hash": manifest["manifest_hash"],
        "source_request_manifest_hash": source["manifest_hash"],
        "selection_rule": "GROUP_A_V11_V7_RESIDUAL_SIGNAL_DIRECTION_ALIGNED_CREDIT_FIVE_SESSION_EXIT_V1",
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
