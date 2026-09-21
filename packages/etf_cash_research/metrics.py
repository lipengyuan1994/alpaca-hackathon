"""Performance, risk, benchmark and uncertainty calculations."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _safe(value: float | int | np.floating | None) -> float | None:
    if value is None or not np.isfinite(float(value)):
        return None
    return float(value)


def max_drawdown(equity: pd.Series, dates: pd.Series | None = None) -> tuple[float, str | None, str | None, int]:
    values = pd.to_numeric(equity, errors="coerce").astype(float)
    peaks = values.cummax()
    drawdowns = values / peaks - 1.0
    if drawdowns.empty:
        return 0.0, None, None, 0
    trough_index = drawdowns.idxmin()
    trough = float(drawdowns.loc[trough_index])
    peak_index = values.loc[:trough_index].idxmax()
    recovery = values.loc[trough_index:]
    peak_value = values.loc[peak_index]
    recovery_indices = recovery[recovery >= peak_value].index
    recovery_days = int((recovery_indices[0] - peak_index) if len(recovery_indices) else len(values) - 1 - int(peak_index))
    if dates is None:
        peak_label, trough_label = str(peak_index), str(trough_index)
    else:
        peak_label, trough_label = str(pd.Timestamp(dates.iloc[int(peak_index)]).date()), str(pd.Timestamp(dates.iloc[int(trough_index)]).date())
    return abs(min(0.0, trough)), peak_label, trough_label, recovery_days


def _annualized_return(start_value: float, end_value: float, days: int) -> float | None:
    if start_value <= 0 or end_value <= 0 or days <= 0:
        return None
    return float((end_value / start_value) ** (365.25 / days) - 1.0)


def compute_metrics(
    equity: pd.DataFrame,
    fills: pd.DataFrame,
    *,
    initial_cash: float,
    start: Any,
    end: Any,
    trades: pd.DataFrame | None = None,
    cash_ledger: pd.DataFrame | None = None,
) -> dict[str, Any]:
    if equity.empty or "equity" not in equity:
        return {"status": "INSUFFICIENT_EQUITY"}
    frame = equity.copy().reset_index(drop=True)
    frame["equity"] = pd.to_numeric(frame["equity"], errors="coerce")
    frame = frame.dropna(subset=["equity"])
    values = frame["equity"].astype(float)
    returns = values.pct_change()
    returns.iloc[0] = values.iloc[0] / initial_cash - 1.0
    returns = returns.fillna(0.0)
    days = max(1, (pd.Timestamp(end) - pd.Timestamp(start)).days)
    # The account exists before its first fill: first-day losses count.
    risk_values = pd.concat([pd.Series([initial_cash]), values], ignore_index=True)
    risk_dates = pd.concat([pd.Series([pd.Timestamp(start)]), frame["date"]], ignore_index=True) if "date" in frame else None
    drawdown, peak_date, trough_date, recovery_days = max_drawdown(risk_values, risk_dates)
    downside_deviation = float(np.sqrt(np.mean(np.minimum(returns, 0.0) ** 2)))
    annual_vol = float(returns.std(ddof=1) * np.sqrt(252)) if len(returns) > 1 else None
    annual_return = _annualized_return(float(initial_cash), float(values.iloc[-1]), days)
    sharpe = float(returns.mean() / returns.std(ddof=1) * np.sqrt(252)) if len(returns) > 1 and returns.std(ddof=1) > 0 else None
    sortino = float(returns.mean() / downside_deviation * np.sqrt(252)) if downside_deviation > 0 else None
    turnover = float(pd.to_numeric(fills.get("notional", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum()) if not fills.empty else 0.0
    sell_count = int((fills.get("side", pd.Series(dtype=str)) == "sell").sum()) if not fills.empty else 0
    buy_count = int((fills.get("side", pd.Series(dtype=str)) == "buy").sum()) if not fills.empty else 0
    completed_trades = pd.DataFrame() if trades is None else trades.copy()
    if not completed_trades.empty and "exit_date" in completed_trades:
        completed_trades = completed_trades[completed_trades["exit_date"].notna()].copy()
    open_trades = pd.DataFrame() if trades is None else trades.copy()
    if not open_trades.empty and "exit_date" in open_trades:
        open_trades = open_trades[open_trades["exit_date"].isna()].copy()
    gross = pd.to_numeric(completed_trades.get("gross_pnl", pd.Series(dtype=float)), errors="coerce").dropna()
    fees = pd.to_numeric(completed_trades.get("fees", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    trade_net = gross.reset_index(drop=True) - fees.reset_index(drop=True) if len(gross) else pd.Series(dtype=float)
    wins = trade_net[trade_net > 0]
    losses = trade_net[trade_net < 0]
    holding_days = []
    if not completed_trades.empty and {"entry_date", "exit_date"}.issubset(completed_trades.columns):
        holding_days = (pd.to_datetime(completed_trades["exit_date"], utc=True) - pd.to_datetime(completed_trades["entry_date"], utc=True)).dt.total_seconds().div(86_400).tolist()
    pnl = float(values.iloc[-1] - initial_cash)
    max_value = float(values.max())
    min_value = float(values.min())
    profitable_days = int((returns > 0).sum())
    losing_days = int((returns < 0).sum())
    date_series = pd.to_datetime(frame["date"], utc=True).dt.tz_localize(None) if "date" in frame else pd.Series(pd.date_range(start, periods=len(frame), freq="D"))
    daily_returns = pd.Series(returns.to_numpy(), index=date_series)
    monthly_returns = ((1.0 + daily_returns).groupby(daily_returns.index.to_period("M")).prod() - 1.0).to_dict()
    calendar_year_returns = ((1.0 + daily_returns).groupby(daily_returns.index.to_period("Y")).prod() - 1.0).to_dict()
    ledger = pd.DataFrame() if cash_ledger is None else cash_ledger.copy()
    dividend_income = float(pd.to_numeric(ledger.loc[ledger.get("kind", pd.Series(index=ledger.index, dtype=str)) == "dividend_receivable", "amount"], errors="coerce").fillna(0.0).sum()) if not ledger.empty and "amount" in ledger else 0.0
    adverse_costs = float(pd.to_numeric(fills.get("adverse_cost", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum()) if not fills.empty else 0.0
    explicit_fees = float(pd.to_numeric(fills.get("fee", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum()) if not fills.empty else 0.0
    realized_pnl = float(trade_net.sum()) if len(trade_net) else 0.0
    unrealized_pnl = float(pd.to_numeric(open_trades.get("gross_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum()) if not open_trades.empty else 0.0
    return {
        "status": "OK",
        "start_date": str(pd.Timestamp(start).date()),
        "end_date": str(pd.Timestamp(end).date()),
        "starting_equity": float(initial_cash),
        "ending_equity": float(values.iloc[-1]),
        "net_pnl": pnl,
        "daily_profit_sum": float(pd.to_numeric(frame.get("daily_profit", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum()) if "daily_profit" in frame else pnl,
        "equity_reconciliation_error": float((pd.to_numeric(frame.get("daily_profit", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum() - pnl)) if "daily_profit" in frame else 0.0,
        "net_return": float(values.iloc[-1] / initial_cash - 1.0) if initial_cash else None,
        "cagr": _safe(annual_return),
        "max_drawdown": drawdown,
        "drawdown_peak_date": peak_date,
        "drawdown_trough_date": trough_date,
        "drawdown_recovery_sessions": recovery_days,
        "annualized_volatility": _safe(annual_vol),
        "sharpe_zero_rf": _safe(sharpe),
        "sortino_zero_mar": _safe(sortino),
        "calmar": _safe(annual_return / drawdown if annual_return is not None and drawdown > 0 else None),
        "turnover": turnover,
        "trading_costs": adverse_costs + explicit_fees,
        "adverse_execution_costs": adverse_costs,
        "explicit_fees": explicit_fees,
        "dividend_income": dividend_income,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "completed_round_trips": int(len(completed_trades)),
        "win_rate": _safe(float((trade_net > 0).mean()) if len(trade_net) else None),
        "payoff_ratio": _safe(float(wins.mean() / abs(losses.mean())) if len(wins) and len(losses) and losses.mean() else None),
        "profit_factor": _safe(float(wins.sum() / abs(losses.sum())) if len(wins) and len(losses) and losses.sum() else None),
        "average_holding_days": _safe(float(np.mean(holding_days)) if holding_days else None),
        "median_holding_days": _safe(float(np.median(holding_days)) if holding_days else None),
        "profitable_days": profitable_days,
        "losing_days": losing_days,
        "best_day": float(returns.max()),
        "worst_day": float(returns.min()),
        "average_invested_exposure": float(pd.to_numeric(frame["invested_exposure"], errors="coerce").mean()) if "invested_exposure" in frame else 0.0,
        "average_cash_allocation": float(1.0 - pd.to_numeric(frame["invested_exposure"], errors="coerce").mean()) if "invested_exposure" in frame else 1.0,
        "average_approx_3x_invested_exposure": _safe(float(pd.to_numeric(frame["approx_3x_invested_exposure"], errors="coerce").mean())) if "approx_3x_invested_exposure" in frame and pd.to_numeric(frame["approx_3x_invested_exposure"], errors="coerce").notna().any() else None,
        "best_month": _safe(float(max(monthly_returns.values())) if monthly_returns else None),
        "worst_month": _safe(float(min(monthly_returns.values())) if monthly_returns else None),
        "monthly_returns": {str(key): float(value) for key, value in monthly_returns.items()},
        "calendar_year_returns": {str(key): float(value) for key, value in calendar_year_returns.items()},
        "minimum_equity": min_value,
        "maximum_equity": max_value,
        "risk_definition": "daily_mark_to_market_equity",
    }


def add_benchmark_comparison(strategy_equity: pd.DataFrame, benchmark_equity: pd.DataFrame, *, prefix: str, initial_cash: float = 1000.0) -> dict[str, Any]:
    if strategy_equity.empty or benchmark_equity.empty:
        return {f"{prefix}_return_gap": None, f"{prefix}_beta": None, f"{prefix}_correlation": None}
    left = strategy_equity[["date", "equity"]].copy()
    right = benchmark_equity[["date", "equity"]].copy()
    joined = left.merge(right, on="date", suffixes=("_strategy", "_benchmark"))
    if joined.empty:
        return {f"{prefix}_return_gap": None, f"{prefix}_beta": None, f"{prefix}_correlation": None}
    joined = joined.sort_values("date").reset_index(drop=True)
    strategy_returns = joined["equity_strategy"].pct_change()
    benchmark_returns = joined["equity_benchmark"].pct_change()
    strategy_returns.iloc[0] = joined["equity_strategy"].iloc[0] / initial_cash - 1.0
    benchmark_returns.iloc[0] = joined["equity_benchmark"].iloc[0] / initial_cash - 1.0
    covariance = strategy_returns.cov(benchmark_returns)
    variance = benchmark_returns.var()
    return {
        f"{prefix}_return_gap": float((joined["equity_strategy"].iloc[-1] - joined["equity_benchmark"].iloc[-1]) / initial_cash),
        f"{prefix}_beta": _safe(covariance / variance if variance and not pd.isna(variance) else None),
        f"{prefix}_correlation": _safe(strategy_returns.corr(benchmark_returns)),
    }


def moving_block_bootstrap(strategy_returns: pd.Series, benchmark_returns: pd.Series, *, samples: int = 2000, block_length: int = 20, seed: int = 135) -> dict[str, Any]:
    """Return descriptive paired return-gap intervals without selection claims."""
    paired = pd.concat([pd.to_numeric(strategy_returns, errors="coerce"), pd.to_numeric(benchmark_returns, errors="coerce")], axis=1).dropna()
    left, right = paired.iloc[:, 0].to_numpy(dtype=float), paired.iloc[:, 1].to_numpy(dtype=float)
    length = len(left)
    if length == 0:
        return {"samples": 0, "block_length": block_length, "gap_quantiles": None}
    left, right = left[-length:], right[-length:]
    block_length = max(1, min(block_length, length))
    rng = np.random.default_rng(seed)
    block_count = int(np.ceil(length / block_length))
    starts = rng.integers(0, length - block_length + 1, size=(samples, block_count))
    offsets = np.arange(block_length, dtype=int)
    indices = (starts[:, :, None] + offsets[None, None, :]).reshape(samples, -1)[:, :length]
    sampled_left = left[indices]
    sampled_right = right[indices]
    gaps = np.prod(1.0 + sampled_left, axis=1) - np.prod(1.0 + sampled_right, axis=1)
    return {
        "samples": samples,
        "block_length": block_length,
        "seed": seed,
        "confidence_level": 0.95,
        "gap_quantiles": {"p025": float(np.quantile(gaps, 0.025)), "p05": float(np.quantile(gaps, 0.05)), "p50": float(np.quantile(gaps, 0.50)), "p95": float(np.quantile(gaps, 0.95)), "p975": float(np.quantile(gaps, 0.975))},
        "interpretation": "descriptive_interval; does_not_remove_strategy_selection_bias",
    }
