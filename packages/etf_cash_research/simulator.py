"""Strict settled-cash, next-open ETF simulator."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_DOWN, Decimal
from typing import Any, Mapping

import pandas as pd

from .protocol import DEFAULT_PROTOCOL, CostScenario, ResearchProtocol
from .strategies import ETFStrategy, Signal, build_strategy

REQUIRED_BAR_COLUMNS = {"date", "symbol", "open", "high", "low", "close"}


def _utc_normalize(value: str | date | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.normalize()


@dataclass(frozen=True)
class BacktestResult:
    strategy_id: str
    semiconductor: str
    variant: str
    cost_scenario: str
    protocol_hash: str
    equity: pd.DataFrame
    signals: pd.DataFrame
    orders: pd.DataFrame
    fills: pd.DataFrame
    cash_ledger: pd.DataFrame
    trades: pd.DataFrame
    metrics: dict[str, Any]


def _normalise_bars(bars: pd.DataFrame) -> pd.DataFrame:
    missing = REQUIRED_BAR_COLUMNS - set(bars.columns)
    if missing:
        raise ValueError(f"ETF_BARS_COLUMNS_MISSING:{','.join(sorted(missing))}")
    result = bars.copy()
    result["date"] = pd.to_datetime(result["date"], utc=True).dt.normalize()
    result["symbol"] = result["symbol"].astype(str).str.upper()
    for column in ("open", "high", "low", "close", "volume", "dividend", "split_factor"):
        if column in result.columns:
            result[column] = pd.to_numeric(result[column], errors="coerce")
    if result.duplicated(["date", "symbol"]).any():
        raise ValueError("ETF_BARS_DUPLICATE_SESSION")
    if (result[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("ETF_BARS_NONPOSITIVE_PRICE")
    if not (result["low"] <= result[["open", "close"]].min(axis=1)).all() or not (result["high"] >= result[["open", "close"]].max(axis=1)).all():
        raise ValueError("ETF_BARS_OHLC_INVALID")
    return result.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)


def _sessions(bars: pd.DataFrame, symbols: tuple[str, ...]) -> list[pd.Timestamp]:
    sets = [set(bars.loc[bars["symbol"] == symbol, "date"]) for symbol in symbols]
    if not sets or any(not item for item in sets):
        raise ValueError("ETF_BARS_SYMBOL_COVERAGE_MISSING")
    if any(item != sets[0] for item in sets[1:]):
        raise ValueError("ETF_BARS_SYMBOL_COVERAGE_GAP")
    return sorted(sets[0])


def _settlement_date(trade_date: pd.Timestamp, change: date, sessions: list[pd.Timestamp] | None = None) -> pd.Timestamp:
    offset = 1 if trade_date.date() >= change else 2
    if sessions is not None:
        future = [item for item in sessions if item > trade_date]
        if len(future) >= offset:
            return future[offset - 1]
    current = trade_date
    remaining = offset
    while remaining:
        current += pd.Timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current.normalize()


def _floor_quantity(value: float, decimals: int) -> float:
    if value <= 0:
        return 0.0
    quantum = Decimal(1).scaleb(-decimals)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_DOWN))


def _bars_for_date(bars: pd.DataFrame, date_value: pd.Timestamp, symbols: tuple[str, ...]) -> dict[str, pd.Series]:
    rows = bars[bars["date"] == date_value].set_index("symbol")
    if any(symbol not in rows.index for symbol in symbols):
        raise ValueError("ETF_MISSING_EXECUTION_BAR")
    return {symbol: rows.loc[symbol] for symbol in symbols}


def _mark_value(holdings: Mapping[str, float], rows: Mapping[str, pd.Series], price_column: str = "close") -> float:
    return sum(float(quantity) * float(rows[symbol][price_column]) for symbol, quantity in holdings.items() if symbol in rows)


def _normalise_payable_date(value: Any, fallback: pd.Timestamp) -> pd.Timestamp:
    if value is None or pd.isna(value):
        return fallback
    payable = pd.Timestamp(value)
    if payable.tzinfo is None:
        payable = payable.tz_localize("UTC")
    else:
        payable = payable.tz_convert("UTC")
    return payable.normalize()


def _target_signal(strategy: ETFStrategy, frames: Mapping[str, pd.DataFrame], index: int) -> Signal:
    return strategy.evaluate(frames, index)


def _weights_changed(current: Mapping[str, float], previous: Mapping[str, float], *, tolerance: float = 1e-12) -> bool:
    symbols = set(current) | set(previous)
    return any(abs(float(current.get(symbol, 0.0)) - float(previous.get(symbol, 0.0))) > tolerance for symbol in symbols)


def _make_frames(bars: pd.DataFrame, symbols: tuple[str, ...]) -> dict[str, pd.DataFrame]:
    return {symbol: bars[bars["symbol"] == symbol].sort_values("date", kind="stable").reset_index(drop=True) for symbol in symbols}


def _empty_frame(columns: tuple[str, ...]) -> pd.DataFrame:
    return pd.DataFrame(columns=list(columns))


def run_backtest(
    bars: pd.DataFrame,
    *,
    strategy_id: str,
    semiconductor: str,
    variant: str = "primary",
    cost: CostScenario | None = None,
    protocol: ResearchProtocol = DEFAULT_PROTOCOL,
    start: str | date | None = None,
    end: str | date | None = None,
    initial_cash: float | None = None,
    execution_delay_sessions: int = 0,
) -> BacktestResult:
    """Run one frozen candidate with a strict settled-cash ledger.

    ``bars`` must be regular-session daily bars and may contain optional
    ``dividend``, ``dividend_payable_date`` and ``split_factor`` fields.  The
    strategy sees only bars through the prior completed session.
    """
    cost = cost or protocol.costs[0]
    if execution_delay_sessions < 0:
        raise ValueError("ETF_EXECUTION_DELAY_INVALID")
    if cost.name not in {item.name for item in protocol.costs}:
        raise ValueError("ETF_COST_SCENARIO_UNKNOWN")
    prepared = _normalise_bars(bars)
    symbols = ("QQQM", semiconductor)
    all_sessions = _sessions(prepared, symbols)
    sessions = list(all_sessions)
    if start is not None:
        sessions = [item for item in sessions if item >= _utc_normalize(start)]
    if end is not None:
        sessions = [item for item in sessions if item <= _utc_normalize(end)]
    if len(sessions) < 2:
        raise ValueError("ETF_BACKTEST_INSUFFICIENT_SESSIONS")
    frames = _make_frames(prepared, symbols)
    frame_index_by_date = {pd.Timestamp(value): index for index, value in enumerate(frames["QQQM"]["date"])}
    # All pair symbols share the same session sequence by construction.
    strategy = build_strategy(strategy_id, semiconductor, variant)
    cash = float(protocol.initial_cash if initial_cash is None else initial_cash)
    holdings: dict[str, float] = {symbol: 0.0 for symbol in symbols}
    pending: list[dict[str, Any]] = []
    receivables: list[dict[str, Any]] = []
    average_basis: dict[str, float] = {symbol: 0.0 for symbol in symbols}
    signals: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []
    equity_rows: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    open_trades: dict[str, dict[str, Any]] = {}
    previous_target: dict[str, float] = {}
    delayed_targets: deque[tuple[dict[str, float], Signal]] = deque()

    for _execution_index, execution_date in enumerate(sessions):
        execution_rows = _bars_for_date(prepared, execution_date, symbols)
        prior_dates = [item for item in all_sessions if item < execution_date]
        prior_date = prior_dates[-1] if prior_dates else None
        previous_rows = _bars_for_date(prepared, prior_date, symbols) if prior_date is not None else execution_rows
        # Release proceeds and dividend receivables before the morning signal.
        released = 0.0
        for item in list(pending):
            if item["settle_date"] <= execution_date:
                cash += float(item["amount"])
                released += float(item["amount"])
                pending.remove(item)
        for item in list(receivables):
            if item["payable_date"] <= execution_date:
                cash += float(item["amount"])
                released += float(item["amount"])
                receivables.remove(item)
        if prior_date is None:
            signal = Signal(execution_date, {}, "WARMUP_OR_NO_PRIOR_SESSION")
        else:
            signal = _target_signal(strategy, frames, frame_index_by_date[pd.Timestamp(prior_date)])
        decision_target = {symbol: float(signal.target_weights.get(symbol, 0.0)) for symbol in symbols}
        if prior_date is None:
            execution_signal = Signal(execution_date, {}, "WARMUP_OR_NO_PRIOR_SESSION")
            target = {symbol: 0.0 for symbol in symbols}
        elif execution_delay_sessions == 0:
            execution_signal = signal
            target = decision_target
        else:
            delayed_targets.append((decision_target, signal))
            if len(delayed_targets) > execution_delay_sessions:
                target, execution_signal = delayed_targets.popleft()
            else:
                target = {symbol: 0.0 for symbol in symbols}
                execution_signal = Signal(execution_date, {}, "DELAYED_ORDER_PENDING")
        target_changed = _weights_changed(target, previous_target) if prior_date is not None else False
        # Position sizing is based on the last completed close.  The current
        # session's opening/closing prices are execution/marking observations
        # and must never leak into the signal or target notional.
        current_close_equity = cash + sum(float(item["amount"]) for item in pending + receivables) + _mark_value(holdings, previous_rows)
        signals.append({
            "decision_date": signal.asof.isoformat(),
            "execution_date": execution_date.isoformat(),
            "strategy_id": strategy_id,
            "semiconductor": semiconductor,
            "variant": variant,
            "execution_delay_sessions": execution_delay_sessions,
            "decision_target_qqqm": decision_target["QQQM"],
            "decision_target_semiconductor": decision_target[semiconductor],
            "target_qqqm": target["QQQM"],
            "target_semiconductor": target[semiconductor],
            "reason_code": signal.reason_code,
            "executed_reason_code": execution_signal.reason_code,
            "entries": ",".join(signal.entries),
            "exits": ",".join(signal.exits),
            "information_cutoff": prior_date.isoformat() if prior_date is not None else None,
        })
        if prior_date is not None:
            # Apply corporate actions before target sizing.  A split changes the
            # quantity and inverse basis but never changes economic value.
            for symbol, row in execution_rows.items():
                factor = float(row.get("split_factor", 1.0) or 1.0)
                if factor > 0 and factor != 1.0 and holdings[symbol] > 0:
                    holdings[symbol] *= factor
                    average_basis[symbol] /= factor
                    ledger.append({"date": execution_date.isoformat(), "kind": "split", "symbol": symbol, "amount": 0.0, "factor": factor})
                dividend = float(row.get("dividend", 0.0) or 0.0)
                if dividend > 0 and holdings[symbol] > 0:
                    payable_date = _normalise_payable_date(row.get("dividend_payable_date"), execution_date)
                    amount = holdings[symbol] * dividend
                    receivables.append({"payable_date": payable_date, "amount": amount})
                    ledger.append({"date": execution_date.isoformat(), "kind": "dividend_receivable", "symbol": symbol, "amount": amount, "factor": 1.0})
            # Equity used for target sizing includes marked holdings and pending
            # cash, but buys may use settled cash only.
            equity_for_target = current_close_equity
            desired: dict[str, float] = dict(holdings)
            if target_changed:
                for symbol in symbols:
                    if abs(target[symbol] - previous_target.get(symbol, 0.0)) > 1e-12:
                        open_price = float(execution_rows[symbol]["open"])
                        desired[symbol] = _floor_quantity(target[symbol] * equity_for_target / open_price, protocol.quantity_decimals)
            # Reductions first.  Sale proceeds remain unavailable until T+1/T+2.
            for symbol in symbols:
                quantity = holdings[symbol] - desired[symbol]
                if quantity <= 0:
                    continue
                quantity = _floor_quantity(min(quantity, holdings[symbol]), protocol.quantity_decimals)
                if quantity <= 0:
                    continue
                open_price = float(execution_rows[symbol]["open"])
                fill_price = open_price * (1.0 - cost.basis_points_per_side / 10_000.0)
                if desired[symbol] > 0 and quantity * fill_price < protocol.minimum_order_notional:
                    continue
                notional = quantity * fill_price
                fee = float(cost.sell_fee)
                proceeds = notional - fee
                holdings[symbol] -= quantity
                settlement_date = _settlement_date(execution_date, protocol.settlement_change, all_sessions)
                pending.append({"settle_date": settlement_date, "amount": proceeds})
                order_id = f"{strategy_id}-{execution_date.date()}-{symbol}-sell"
                orders.append({"order_id": order_id, "date": execution_date.isoformat(), "symbol": symbol, "side": "sell", "quantity": quantity, "target_quantity": desired[symbol], "status": "filled"})
                fills.append({"order_id": order_id, "date": execution_date.isoformat(), "symbol": symbol, "side": "sell", "quantity": quantity, "price": fill_price, "notional": notional, "fee": fee, "adverse_cost": quantity * (open_price - fill_price), "settlement_date": settlement_date.isoformat()})
                ledger.append({"date": execution_date.isoformat(), "kind": "sale_pending", "symbol": symbol, "amount": proceeds, "factor": 1.0})
                open_trade = open_trades.get(symbol)
                if open_trade:
                    closed_quantity = min(quantity, float(open_trade["entry_quantity"]))
                    trades.append({**open_trade, "exit_date": execution_date.isoformat(), "exit_price": fill_price, "exit_quantity": closed_quantity, "gross_pnl": (fill_price - open_trade["entry_price"]) * closed_quantity, "fees": open_trade["fees"] + fee})
                    remaining = float(open_trade["entry_quantity"]) - closed_quantity
                    if remaining > 0:
                        open_trade["entry_quantity"] = remaining
                        open_trade["fees"] = 0.0
                    else:
                        open_trades.pop(symbol, None)
            # Determine affordable buys from the settled ledger.  Skip ordinary
            # rebalance deltas below the protocol threshold.
            buy_requests: list[tuple[str, float, float, float, float]] = []
            for symbol in symbols:
                quantity = desired[symbol] - holdings[symbol]
                open_price = float(execution_rows[symbol]["open"])
                fill_price = open_price * (1.0 + cost.basis_points_per_side / 10_000.0)
                if quantity * fill_price >= protocol.minimum_order_notional:
                    buy_requests.append((symbol, quantity, fill_price, quantity * fill_price, open_price))
            available = max(0.0, cash)
            total_required = sum(item[3] for item in buy_requests)
            scale = min(1.0, available / total_required) if total_required > 0 else 0.0
            for symbol, requested, fill_price, _, open_price in buy_requests:
                quantity = _floor_quantity(requested * scale, protocol.quantity_decimals)
                if quantity <= 0:
                    continue
                notional = quantity * fill_price
                if notional > cash:
                    quantity = _floor_quantity(cash / fill_price, protocol.quantity_decimals)
                    notional = quantity * fill_price
                if quantity <= 0 or notional < protocol.minimum_order_notional:
                    continue
                cash -= notional
                prior_qty = holdings[symbol]
                holdings[symbol] += quantity
                average_basis[symbol] = ((average_basis[symbol] * prior_qty) + notional) / holdings[symbol] if holdings[symbol] else 0.0
                order_id = f"{strategy_id}-{execution_date.date()}-{symbol}-buy"
                orders.append({"order_id": order_id, "date": execution_date.isoformat(), "symbol": symbol, "side": "buy", "quantity": quantity, "target_quantity": desired[symbol], "status": "filled"})
                fills.append({"order_id": order_id, "date": execution_date.isoformat(), "symbol": symbol, "side": "buy", "quantity": quantity, "price": fill_price, "notional": notional, "fee": 0.0, "adverse_cost": quantity * (fill_price - open_price), "settlement_date": execution_date.isoformat()})
                ledger.append({"date": execution_date.isoformat(), "kind": "buy", "symbol": symbol, "amount": -notional, "factor": 1.0})
                existing_trade = open_trades.get(symbol)
                if existing_trade:
                    total_quantity = float(existing_trade["entry_quantity"]) + quantity
                    existing_trade["entry_price"] = ((float(existing_trade["entry_price"]) * float(existing_trade["entry_quantity"])) + (fill_price * quantity)) / total_quantity
                    existing_trade["entry_quantity"] = total_quantity
                else:
                    open_trades[symbol] = {"symbol": symbol, "entry_date": execution_date.isoformat(), "entry_price": fill_price, "entry_quantity": quantity, "fees": 0.0}
            previous_target = dict(target)
        close_value = _mark_value(holdings, execution_rows)
        total_pending = sum(float(item["amount"]) for item in pending + receivables)
        equity_value = cash + close_value + total_pending
        equity_rows.append({
            "date": execution_date.isoformat(),
            "cash_settled": cash,
            "cash_pending": total_pending,
            "holdings_value": close_value,
            "equity": equity_value,
            "invested_exposure": close_value / equity_value if equity_value else 0.0,
            "released_cash": released,
        })
    # Positions still held at the end are explicitly retained as open trades.
    final_rows = _bars_for_date(prepared, sessions[-1], symbols)
    for symbol, item in open_trades.items():
        trades.append({**item, "exit_date": None, "exit_price": None, "exit_quantity": holdings[symbol], "gross_pnl": (float(final_rows[symbol]["close"]) - item["entry_price"]) * holdings[symbol], "fees": item["fees"]})
    from .metrics import compute_metrics

    equity_frame = pd.DataFrame(equity_rows)
    starting_cash = float(protocol.initial_cash if initial_cash is None else initial_cash)
    equity_frame["daily_profit"] = equity_frame["equity"].diff().fillna(equity_frame["equity"].iloc[0] - starting_cash)
    equity_frame["daily_return"] = equity_frame["equity"].pct_change()
    equity_frame.loc[equity_frame.index[0], "daily_return"] = equity_frame["equity"].iloc[0] / starting_cash - 1.0
    equity_frame["daily_return"] = equity_frame["daily_return"].fillna(0.0)
    equity_frame["cumulative_profit"] = equity_frame["equity"] - starting_cash
    equity_frame["drawdown"] = equity_frame["equity"] / equity_frame["equity"].cummax() - 1.0
    metrics = compute_metrics(equity_frame, pd.DataFrame(fills), initial_cash=float(protocol.initial_cash if initial_cash is None else initial_cash), start=sessions[0], end=sessions[-1], trades=pd.DataFrame(trades), cash_ledger=pd.DataFrame(ledger))
    metrics.update({"strategy_id": strategy_id, "semiconductor": semiconductor, "variant": variant, "cost_scenario": cost.name, "protocol_hash": protocol.protocol_hash, "execution_delay_sessions": execution_delay_sessions})
    return BacktestResult(
        strategy_id=strategy_id,
        semiconductor=semiconductor,
        variant=variant,
        cost_scenario=cost.name,
        protocol_hash=protocol.protocol_hash,
        equity=equity_frame,
        signals=pd.DataFrame(signals),
        orders=pd.DataFrame(orders),
        fills=pd.DataFrame(fills),
        cash_ledger=pd.DataFrame(ledger),
        trades=pd.DataFrame(trades),
        metrics=metrics,
    )


def run_benchmark(bars: pd.DataFrame, *, symbol: str, protocol: ResearchProtocol = DEFAULT_PROTOCOL, cost: CostScenario | None = None, start: str | date | None = None, end: str | date | None = None) -> BacktestResult:
    """Buy-and-hold a benchmark using the same cash ledger and cost rules."""
    frame = bars[bars["symbol"].astype(str).str.upper() == symbol].copy()
    # Reuse the simulator with a one-symbol-compatible QQQM alias only after
    # preserving the public result naming.  This path is intentionally simple:
    # benchmark buys 99% on the first executable session and never rebalances.
    result = _run_static(frame, symbol="QQQM", public_symbol=symbol, protocol=protocol, cost=cost, start=start, end=end)
    return result


def run_static_pair_benchmark(
    bars: pd.DataFrame,
    *,
    semiconductor: str,
    protocol: ResearchProtocol = DEFAULT_PROTOCOL,
    cost: CostScenario | None = None,
    start: str | date | None = None,
    end: str | date | None = None,
) -> BacktestResult:
    """Buy 49.5% QQQM and 49.5% semiconductor ETF once, then hold."""
    cost = cost or protocol.costs[0]
    prepared = _normalise_bars(bars)
    symbols = ("QQQM", semiconductor)
    sessions = _sessions(prepared, symbols)
    if start is not None:
        sessions = [item for item in sessions if item >= _utc_normalize(start)]
    if end is not None:
        sessions = [item for item in sessions if item <= _utc_normalize(end)]
    if len(sessions) < 2:
        raise ValueError("ETF_BENCHMARK_INSUFFICIENT_SESSIONS")
    cash = float(protocol.initial_cash)
    holdings = {symbol: 0.0 for symbol in symbols}
    receivables: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []
    equity_rows: list[dict[str, Any]] = []
    fee_rate = cost.basis_points_per_side / 10_000.0
    for index, date_value in enumerate(sessions):
        rows = _bars_for_date(prepared, date_value, symbols)
        released = 0.0
        for item in list(receivables):
            if item["payable_date"] <= date_value:
                cash += float(item["amount"])
                released += float(item["amount"])
                receivables.remove(item)
        if index == 0:
            for symbol in symbols:
                price = float(rows[symbol]["open"]) * (1.0 + fee_rate)
                quantity = _floor_quantity(protocol.initial_cash * 0.495 / price, protocol.quantity_decimals)
                notional = quantity * price
                cash -= notional
                holdings[symbol] = quantity
                fills.append({"order_id": f"benchmark-pair-{date_value.date()}-{symbol}", "date": date_value.isoformat(), "symbol": symbol, "side": "buy", "quantity": quantity, "price": price, "notional": notional, "fee": 0.0, "adverse_cost": quantity * (price - float(rows[symbol]["open"])), "settlement_date": date_value.isoformat()})
                ledger.append({"date": date_value.isoformat(), "kind": "buy", "symbol": symbol, "amount": -notional, "factor": 1.0})
        for symbol in symbols:
            dividend = float(rows[symbol].get("dividend", 0.0) or 0.0)
            if dividend > 0 and holdings[symbol] > 0:
                amount = holdings[symbol] * dividend
                payable_date = _normalise_payable_date(rows[symbol].get("dividend_payable_date"), date_value)
                receivables.append({"payable_date": payable_date, "amount": amount})
                ledger.append({"date": date_value.isoformat(), "kind": "dividend_receivable", "symbol": symbol, "amount": amount, "factor": 1.0})
        value = _mark_value(holdings, rows)
        pending = sum(float(item["amount"]) for item in receivables)
        equity_rows.append({"date": date_value.isoformat(), "cash_settled": cash, "cash_pending": pending, "holdings_value": value, "equity": cash + value + pending, "invested_exposure": value / (cash + value + pending) if cash + value + pending else 0.0, "released_cash": released})
    eq = pd.DataFrame(equity_rows)
    eq["daily_profit"] = eq["equity"].diff().fillna(eq["equity"].iloc[0] - protocol.initial_cash)
    eq["daily_return"] = eq["equity"].pct_change()
    eq.loc[eq.index[0], "daily_return"] = eq["equity"].iloc[0] / protocol.initial_cash - 1.0
    eq["daily_return"] = eq["daily_return"].fillna(0.0)
    eq["cumulative_profit"] = eq["equity"] - protocol.initial_cash
    eq["drawdown"] = eq["equity"] / eq["equity"].cummax() - 1.0
    from .metrics import compute_metrics

    metrics = compute_metrics(eq, pd.DataFrame(fills), initial_cash=protocol.initial_cash, start=sessions[0], end=sessions[-1], cash_ledger=pd.DataFrame(ledger))
    metrics.update({"strategy_id": f"BENCHMARK_50_50_QQQM_{semiconductor}", "semiconductor": semiconductor, "variant": "static_pair", "cost_scenario": cost.name, "protocol_hash": protocol.protocol_hash})
    return BacktestResult(f"BENCHMARK_50_50_QQQM_{semiconductor}", semiconductor, "static_pair", cost.name, protocol.protocol_hash, eq, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(fills), pd.DataFrame(ledger), pd.DataFrame(), metrics)


def _run_static(bars: pd.DataFrame, *, symbol: str, public_symbol: str, protocol: ResearchProtocol, cost: CostScenario | None, start: str | date | None, end: str | date | None) -> BacktestResult:
    cost = cost or protocol.costs[0]
    prepared = _normalise_bars(bars)
    sessions = sorted(set(prepared["date"]))
    if start is not None:
        sessions = [item for item in sessions if item >= _utc_normalize(start)]
    if end is not None:
        sessions = [item for item in sessions if item <= _utc_normalize(end)]
    if len(sessions) < 2:
        raise ValueError("ETF_BENCHMARK_INSUFFICIENT_SESSIONS")
    cash = float(protocol.initial_cash)
    holdings = 0.0
    receivables: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []
    equity: list[dict[str, Any]] = []
    fee_rate = cost.basis_points_per_side / 10_000.0
    for index, date_value in enumerate(sessions):
        row = prepared[prepared["date"] == date_value].iloc[0]
        released = 0.0
        for item in list(receivables):
            if item["payable_date"] <= date_value:
                cash += float(item["amount"])
                released += float(item["amount"])
                receivables.remove(item)
        if index == 0:
            price = float(row["open"]) * (1.0 + fee_rate)
            quantity = _floor_quantity(protocol.initial_cash * protocol.target_investment / price, protocol.quantity_decimals)
            notional = quantity * price
            cash -= notional
            holdings = quantity
            fills.append({"order_id": f"benchmark-{public_symbol}-buy", "date": date_value.isoformat(), "symbol": public_symbol, "side": "buy", "quantity": quantity, "price": price, "notional": notional, "fee": 0.0, "adverse_cost": quantity * (price - float(row["open"])), "settlement_date": date_value.isoformat()})
            ledger.append({"date": date_value.isoformat(), "kind": "buy", "symbol": public_symbol, "amount": -notional, "factor": 1.0})
        dividend = float(row.get("dividend", 0.0) or 0.0)
        if dividend > 0 and holdings > 0:
            amount = holdings * dividend
            payable_date = _normalise_payable_date(row.get("dividend_payable_date"), date_value)
            receivables.append({"payable_date": payable_date, "amount": amount})
            ledger.append({"date": date_value.isoformat(), "kind": "dividend_receivable", "symbol": public_symbol, "amount": amount, "factor": 1.0})
        value = holdings * float(row["close"])
        pending = sum(float(item["amount"]) for item in receivables)
        equity.append({"date": date_value.isoformat(), "cash_settled": cash, "cash_pending": pending, "holdings_value": value, "equity": cash + value + pending, "invested_exposure": value / (cash + value + pending) if cash + value + pending else 0.0, "released_cash": released})
    eq = pd.DataFrame(equity)
    eq["daily_profit"] = eq["equity"].diff().fillna(eq["equity"].iloc[0] - protocol.initial_cash)
    eq["daily_return"] = eq["equity"].pct_change()
    eq.loc[eq.index[0], "daily_return"] = eq["equity"].iloc[0] / protocol.initial_cash - 1.0
    eq["daily_return"] = eq["daily_return"].fillna(0.0)
    eq["cumulative_profit"] = eq["equity"] - protocol.initial_cash
    eq["drawdown"] = eq["equity"] / eq["equity"].cummax() - 1.0
    from .metrics import compute_metrics

    metrics = compute_metrics(eq, pd.DataFrame(fills), initial_cash=protocol.initial_cash, start=sessions[0], end=sessions[-1], cash_ledger=pd.DataFrame(ledger))
    metrics.update({"strategy_id": f"BENCHMARK_{public_symbol}", "semiconductor": public_symbol, "variant": "buy_and_hold", "cost_scenario": cost.name, "protocol_hash": protocol.protocol_hash})
    return BacktestResult(f"BENCHMARK_{public_symbol}", public_symbol, "buy_and_hold", cost.name, protocol.protocol_hash, eq, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(fills), pd.DataFrame(ledger), pd.DataFrame(), metrics)
