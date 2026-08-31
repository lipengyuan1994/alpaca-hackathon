"""Freeze QQQ-only V13 wheel variants before reading new option outcomes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from packages.contracts.canonical import canonical_hash

from .artifacts import atomic_json
from .group_a_option_requests import GroupARequestError, _artifact, _load_manifest
from .group_a_parallel_v2_option_requests import _regular_bars
from .group_a_wheel_v12_option_requests import _MAX_DTE, _MIN_DTE, _expiry_end, _weekly_slots

_UNDERLYING = "QQQ"
_MONEYNESS_LEVELS = (1, 2, 3)
_VARIANTS: tuple[dict[str, Any], ...] = (
    {"variant_id": "v13.1", "put_otm_pct": 2, "call_otm_pct": 2, "take_profit_fraction": 0.15, "regime": "fixed", "hypothesis": "V12_QQQ_REPLICATION"},
    {"variant_id": "v13.2", "put_otm_pct": 1, "call_otm_pct": 1, "take_profit_fraction": 0.15, "regime": "fixed", "hypothesis": "CLOSER_STRIKES_HIGHER_PREMIUM"},
    {"variant_id": "v13.3", "put_otm_pct": 3, "call_otm_pct": 3, "take_profit_fraction": 0.15, "regime": "fixed", "hypothesis": "FARTHER_STRIKES_LOWER_ASSIGNMENT_RISK"},
    {"variant_id": "v13.4", "put_otm_pct": 2, "call_otm_pct": 2, "take_profit_fraction": 0.25, "regime": "fixed", "hypothesis": "LESS_AGGRESSIVE_PROFIT_TAKING"},
    {"variant_id": "v13.5", "put_otm_pct": None, "call_otm_pct": None, "take_profit_fraction": 0.15, "regime": "prior_50_session_trend", "hypothesis": "TREND_ADAPTIVE_STRIKES"},
)

_RESEARCH_SOURCES = (
    "https://cdn.cboe.com/api/global/us_indices/governance/Cboe_NASDAQ_BuyWrite_Indices_Methodology.pdf",
    "https://cdn.cboe.com/api/global/us_indices/governance/Cboe_One-Week_PutWrite_Indices_Methodology.pdf",
    "https://cdn.cboe.com/api/global/us_indices/governance/BXMD_Methodology.pdf",
)


def _choose_put(choices: pd.DataFrame, spot: float, otm_pct: int) -> str | None:
    eligible = choices[choices["strike_value"] <= spot * (1.0 - otm_pct / 100.0)]
    if eligible.empty:
        return None
    return str(eligible.sort_values(["strike_value", "symbol"], ascending=[False, True]).iloc[0]["symbol"])


def _choose_call(choices: pd.DataFrame, spot: float, otm_pct: int) -> str | None:
    eligible = choices[choices["strike_value"] >= spot * (1.0 + otm_pct / 100.0)]
    if eligible.empty:
        return None
    return str(eligible.sort_values(["strike_value", "symbol"], ascending=[True, True]).iloc[0]["symbol"])


def _prior_trend_by_session(split_path: Path) -> dict[object, bool | None]:
    bars = _regular_bars(split_path)
    bars = bars[bars["symbol"] == _UNDERLYING]
    daily = bars.groupby("session", sort=True).tail(1)[["session", "close"]].copy()
    daily["prior_close"] = daily["close"].shift(1)
    daily["prior_sma_50"] = daily["close"].shift(1).rolling(50, min_periods=50).mean()
    return {
        row.session: None if pd.isna(row.prior_sma_50) else bool(row.prior_close > row.prior_sma_50)
        for row in daily.itertuples(index=False)
    }


def generate_requests(*, data_manifest_path: Path, output_path: Path) -> Path:
    """Create one shared immutable request set for all five QQQ V13 variants."""
    if output_path.exists():
        raise GroupARequestError("OPTION_REQUEST_OUTPUT_EXISTS")
    manifest = _load_manifest(data_manifest_path)
    root = data_manifest_path.resolve().parent
    contracts = pd.read_parquet(_artifact(root, manifest, "option_contracts"))
    contracts = contracts[
        (contracts["underlying"] == _UNDERLYING)
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
        for key, values in contracts.groupby(["right", "expiration_date"], sort=True)
    }
    expiries = sorted(
        expiry for right, expiry in groups if right == "PUT" and ("CALL", expiry) in groups
    )
    trend_by_session = _prior_trend_by_session(_artifact(root, manifest, "stock_bars_split"))
    records: list[dict[str, Any]] = []
    for slot in _weekly_slots(raw_path=_artifact(root, manifest, "stock_bars_raw")):
        if slot.underlying != _UNDERLYING:
            continue
        expiry = next(
            (value for value in expiries if _MIN_DTE <= (value - slot.decision_time.date()).days <= _MAX_DTE),
            None,
        )
        if expiry is None:
            continue
        contract_map: dict[str, str] = {}
        for otm_pct in _MONEYNESS_LEVELS:
            put_symbol = _choose_put(groups[("PUT", expiry)], slot.raw_spot, otm_pct)
            call_symbol = _choose_call(groups[("CALL", expiry)], slot.raw_spot, otm_pct)
            if put_symbol is None or call_symbol is None:
                contract_map = {}
                break
            contract_map[f"put_{otm_pct}pct"] = put_symbol
            contract_map[f"call_{otm_pct}pct"] = call_symbol
        if len(contract_map) != 6:
            continue
        start = slot.decision_time.tz_convert("UTC")
        records.append(
            {
                "underlying": _UNDERLYING,
                "decision_time": start.isoformat().replace("+00:00", "Z"),
                "expiry": str(expiry),
                "expiry_time": _expiry_end(expiry).isoformat().replace("+00:00", "Z"),
                "prior_50_session_trend_up": trend_by_session.get(slot.decision_time.date()),
                "contract_map": contract_map,
                "symbols": sorted(set(contract_map.values())),
                "start": start.isoformat().replace("+00:00", "Z"),
                "end": _expiry_end(expiry).isoformat().replace("+00:00", "Z"),
            }
        )
    records.sort(key=lambda item: item["decision_time"])
    payload: dict[str, Any] = {
        "schema_version": "group-a-wheel-v13-option-requests/v1",
        "research_status": "RESEARCH_ONLY_QQQ_WHEEL_V13_NOT_INTEGRATION_ELIGIBLE",
        "base_data_manifest_hash": manifest["manifest_hash"],
        "selection_rule": "GROUP_A_V13_QQQ_WEEKLY_CSP_CC_FIVE_PREDECLARED_VARIANTS_V1",
        "primary_ranking_metric": "net_return",
        "tie_break_metrics": ["sharpe", "max_drawdown"],
        "position_unit": "ONE_OPTION_CONTRACT_OR_100_ASSIGNED_SHARES",
        "variants": list(_VARIANTS),
        "research_sources": list(_RESEARCH_SOURCES),
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

