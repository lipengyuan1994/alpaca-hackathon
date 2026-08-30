"""Deterministic, research-only CSP/covered-call wheel proxy replay.

The module intentionally stops at historical research.  It neither exposes a
Strategy API plug-in nor creates broker instructions: V1 cannot safely model
an equity-and-option execution saga.  One replay unit is one option contract
or 100 shares after deterministic expiration assignment.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from packages.contracts.canonical import canonical_hash

from .artifacts import atomic_bytes, atomic_json, ensure_empty_output, write_parquet
from .group_a_proxy_backtest import (
    _bar,
    _complete_market_dates,
    _cumulative_pnl_svg,
    _dataset,
    _load,
)


class WheelReplayError(ValueError):
    """Raised when immutable wheel inputs cannot support a replay."""


_INITIAL_CASH = 100_000.0
_CONTRACT_MULTIPLIER = 100
_FEE_PER_CONTRACT_SIDE = 0.10


@dataclass
class _ActiveOption:
    record: dict[str, Any]
    kind: str
    symbol: str
    strike: float
    credit: float
    entry_time: pd.Timestamp
    expiry_time: pd.Timestamp


def _utc_text(value: pd.Timestamp) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _first_entry_bar(bars: pd.DataFrame, decision: pd.Timestamp) -> pd.Series | None:
    eligible = bars[(bars["event_time"] > decision) & (bars["event_time"] <= decision + pd.Timedelta(minutes=5))]
    return None if eligible.empty else eligible.sort_values("event_time", kind="stable").iloc[0]


def _single_buffer(row: pd.Series) -> float:
    return max(0.05, 0.10 * float(row["open"]), 0.25 * (float(row["high"]) - float(row["low"])))


def _entry_credit(row: pd.Series) -> float:
    return max(0.0, float(row["open"]) - _single_buffer(row))


def _close_debit(row: pd.Series) -> float:
    return float(row["open"]) + _single_buffer(row)


def _underlying_close(bars: pd.DataFrame, *, underlying: str, at: pd.Timestamp) -> float | None:
    eligible = bars[(bars["symbol"] == underlying) & (bars["event_time"] <= at)]
    if eligible.empty:
        return None
    return float(eligible.sort_values("event_time", kind="stable").iloc[-1]["close"])


def _option_bars(option_manifest: dict[str, Any], root: Path, request_id: str) -> pd.DataFrame:
    bars = _bar(root, _dataset(option_manifest, f"option_bars_{request_id}"))
    return bars.sort_values("event_time", kind="stable")


def _resolve(
    active: _ActiveOption,
    *,
    option_bars: pd.DataFrame,
    underlying_bars: pd.DataFrame,
    cutoff: pd.Timestamp,
) -> dict[str, Any] | None:
    """Resolve a short option only when take-profit/expiration is observable."""
    end = min(active.expiry_time, cutoff)
    bars = option_bars[
        (option_bars["symbol"] == active.symbol)
        & (option_bars["event_time"] > active.entry_time)
        & (option_bars["event_time"] <= end)
    ]
    for row in bars.itertuples(index=False):
        series = pd.Series(row._asdict())
        debit = _close_debit(series)
        if active.credit - debit > active.credit * float(active.record["take_profit_fraction"]):
            return {
                "time": series["event_time"], "reason": "TAKE_PROFIT_15_PERCENT", "debit": debit,
                "assigned": False, "called_away": False,
            }
    if cutoff < active.expiry_time:
        return None
    spot = _underlying_close(underlying_bars, underlying=active.record["underlying"], at=active.expiry_time)
    if spot is None:
        return {"time": active.expiry_time, "reason": "EXPIRY_PRICE_UNAVAILABLE", "debit": None, "assigned": False, "called_away": False}
    assigned = active.kind == "CSP" and spot < active.strike
    called_away = active.kind == "CC" and spot > active.strike
    return {
        "time": active.expiry_time,
        "reason": "ASSIGNED" if assigned else "CALLED_AWAY" if called_away else "EXPIRED_WORTHLESS",
        "debit": 0.0,
        "assigned": assigned,
        "called_away": called_away,
        "expiry_spot": spot,
    }


def _daily_metrics(equity_events: list[dict[str, Any]], underlying_bars: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, float | None]]:
    if not equity_events:
        return pd.DataFrame(columns=["date", "daily_pnl", "daily_return", "equity"]), {
            "net_pnl": 0.0, "net_return": 0.0, "sharpe": None, "sortino": None, "max_drawdown": 0.0,
        }
    events = pd.DataFrame(equity_events)
    events["date"] = pd.to_datetime(events["time"], utc=True).dt.tz_convert("America/New_York").dt.date.astype(str)
    start, end = events["date"].min(), events["date"].max()
    dates = _complete_market_dates(underlying_bars, start=start, end=end)
    closing = events.sort_values("time", kind="stable").groupby("date", as_index=True)["equity"].last()
    equity = closing.reindex(dates).ffill().fillna(_INITIAL_CASH)
    daily = pd.DataFrame({"date": dates, "equity": equity.to_numpy()})
    daily["daily_pnl"] = daily["equity"].diff().fillna(daily["equity"] - _INITIAL_CASH)
    daily["daily_return"] = daily["equity"].pct_change().fillna(daily["daily_pnl"] / _INITIAL_CASH)
    cumulative = (1.0 + daily["daily_return"]).cumprod()
    drawdown = cumulative / cumulative.cummax() - 1.0
    std = float(daily["daily_return"].std(ddof=1))
    downside = float((daily["daily_return"].clip(upper=0).pow(2).mean()) ** 0.5)
    return daily, {
        "net_pnl": float(daily["equity"].iloc[-1] - _INITIAL_CASH),
        "net_return": float(daily["equity"].iloc[-1] / _INITIAL_CASH - 1.0),
        "sharpe": None if not std else math.sqrt(252) * float(daily["daily_return"].mean()) / std,
        "sortino": None if not downside else math.sqrt(252) * float(daily["daily_return"].mean()) / downside,
        "max_drawdown": float(drawdown.min()),
    }


def run(*, option_manifest_path: Path, request_path: Path, output: Path, base_data_manifest_path: Path | None = None) -> Path:
    """Replay the V12 wheel state machine against frozen option bars."""
    option_manifest = _load(option_manifest_path)
    requests = _load(request_path)
    if option_manifest.get("schema_version") != "option-observation-manifest/v1" or option_manifest.get("status") != "COLLECTED":
        raise WheelReplayError("WHEEL_OPTION_OBSERVATIONS_NOT_READY")
    if option_manifest.get("base_data_manifest_hash") != requests.get("base_data_manifest_hash"):
        raise WheelReplayError("WHEEL_BASE_MANIFEST_BINDING_MISMATCH")
    records = [{**selection, **request} for selection, request in zip(requests["selection_records"], requests["requests"], strict=True)]
    base_path = base_data_manifest_path or option_manifest_path.resolve().parents[3] / "underlying" / "data_manifest.json"
    base_manifest = _load(base_path)
    if base_manifest.get("manifest_hash") != option_manifest.get("base_data_manifest_hash"):
        raise WheelReplayError("WHEEL_BASE_MANIFEST_BINDING_MISMATCH")
    base_root = base_path.resolve().parent
    raw_bars = _bar(base_root, _dataset(base_manifest, "stock_bars_raw"))
    contracts = pd.read_parquet(base_root / _dataset(base_manifest, "option_contracts")["artifact"]["path"])
    strike_by_symbol = {str(row.symbol): float(row.strike) for row in contracts.itertuples(index=False)}
    root = option_manifest_path.resolve().parent
    target = ensure_empty_output(output)
    trades: list[dict[str, Any]] = []
    curves: dict[str, list[dict[str, Any]]] = {}
    metrics: dict[str, Any] = {}
    daily_rows: list[dict[str, Any]] = []
    for strategy, strategy_records in pd.DataFrame(records).groupby("strategy", sort=True):
        cash, shares, active = _INITIAL_CASH, 0, None
        equity_events: list[dict[str, Any]] = []
        for record in strategy_records.sort_values("decision_time", kind="stable").to_dict("records"):
            decision = pd.Timestamp(record["decision_time"])
            if active is not None:
                resolved = _resolve(active, option_bars=_option_bars(option_manifest, root, active.record["request_id"]), underlying_bars=raw_bars, cutoff=decision)
                if resolved is None:
                    continue
                if resolved["debit"] is not None:
                    cash -= float(resolved["debit"]) * _CONTRACT_MULTIPLIER + _FEE_PER_CONTRACT_SIDE
                if resolved["assigned"]:
                    cash -= active.strike * _CONTRACT_MULTIPLIER
                    shares = _CONTRACT_MULTIPLIER
                if resolved["called_away"]:
                    cash += active.strike * _CONTRACT_MULTIPLIER
                    shares = 0
                spot = _underlying_close(raw_bars, underlying=record["underlying"], at=resolved["time"])
                equity = cash + shares * (spot if spot is not None else 0.0)
                trades.append({"strategy": strategy, "kind": active.kind, "symbol": active.symbol, "entry_time": _utc_text(active.entry_time), "exit_time": _utc_text(resolved["time"]), "credit": active.credit, "debit": resolved["debit"], "reason": resolved["reason"], "assigned": resolved["assigned"], "called_away": resolved["called_away"], "cash_after": cash, "shares_after": shares, "equity_after": equity})
                equity_events.append({"time": resolved["time"], "equity": equity})
                active = None
            kind, symbol = ("CSP", record["put_symbol"]) if shares == 0 else ("CC", record["call_symbol"])
            strike = strike_by_symbol.get(str(symbol))
            if strike is None or (kind == "CSP" and cash < strike * _CONTRACT_MULTIPLIER):
                continue
            bars = _option_bars(option_manifest, root, record["request_id"])
            entry = _first_entry_bar(bars[bars["symbol"] == symbol], decision)
            if entry is None:
                continue
            credit = _entry_credit(entry)
            if credit <= 0:
                continue
            cash += credit * _CONTRACT_MULTIPLIER - _FEE_PER_CONTRACT_SIDE
            active = _ActiveOption(record, kind, str(symbol), strike, credit, entry["event_time"], pd.Timestamp(record["expiry_time"]))
        if active is not None:
            resolved = _resolve(active, option_bars=_option_bars(option_manifest, root, active.record["request_id"]), underlying_bars=raw_bars, cutoff=active.expiry_time)
            if resolved and resolved["debit"] is not None:
                cash -= float(resolved["debit"]) * _CONTRACT_MULTIPLIER + _FEE_PER_CONTRACT_SIDE
                if resolved["assigned"]:
                    cash -= active.strike * _CONTRACT_MULTIPLIER
                    shares = _CONTRACT_MULTIPLIER
                if resolved["called_away"]:
                    cash += active.strike * _CONTRACT_MULTIPLIER
                    shares = 0
                spot = _underlying_close(raw_bars, underlying=active.record["underlying"], at=resolved["time"])
                equity = cash + shares * (spot if spot is not None else 0.0)
                trades.append({"strategy": strategy, "kind": active.kind, "symbol": active.symbol, "entry_time": _utc_text(active.entry_time), "exit_time": _utc_text(resolved["time"]), "credit": active.credit, "debit": resolved["debit"], "reason": resolved["reason"], "assigned": resolved["assigned"], "called_away": resolved["called_away"], "cash_after": cash, "shares_after": shares, "equity_after": equity})
                equity_events.append({"time": resolved["time"], "equity": equity})
        daily, summary = _daily_metrics(equity_events, raw_bars)
        metrics[strategy] = {**summary, "starting_balance": _INITIAL_CASH, "trades": sum(item["strategy"] == strategy for item in trades), "turnover_contract_legs": 2 * sum(item["strategy"] == strategy for item in trades), "ending_shares": shares, "ending_cash": cash}
        curves[strategy] = daily[["date", "daily_pnl"]].to_dict("records")
        daily_rows.extend({"strategy": strategy, **row} for row in daily.to_dict("records"))
    write_parquet(target, "trades", trades, ("strategy", "kind", "symbol", "entry_time", "exit_time", "credit", "debit", "reason", "assigned", "called_away", "cash_after", "shares_after", "equity_after"))
    write_parquet(target, "daily_returns", daily_rows, ("strategy", "date", "daily_pnl", "daily_return", "equity"))
    plot = Path("plots") / "cumulative_pnl.svg"
    atomic_bytes(target / plot, _cumulative_pnl_svg(curves))
    atomic_json(target / "plots" / "cumulative_pnl_spec.json", {"schema_version": "cumulative-pnl-plot/v1", "plot": plot.as_posix(), "series": sorted(curves)})
    report = {"schema_version": "group-a-wheel-v12-replay/v1", "status": "RESEARCH_ONLY_STOCK_COLLATERALIZED_NON_EXECUTABLE", "starting_balance": _INITIAL_CASH, "position_unit": "one contract / 100 shares", "option_observation_manifest_hash": option_manifest["manifest_hash"], "option_request_manifest_hash": requests["manifest_hash"], "metrics": metrics, "cumulative_pnl_plot": plot.as_posix(), "limitations": ["historical option bars are non-executable proxies", "assignment is deterministic expiration settlement, not broker evidence", "equity legs are research accounting only and cannot be emitted by Strategy API V1", "no live option selection, sizing, account, or order execution"], "report_hash": None}
    report["report_hash"] = canonical_hash({key: value for key, value in report.items() if key != "report_hash"})
    atomic_json(target / "metrics.json", report)
    return target / "metrics.json"
