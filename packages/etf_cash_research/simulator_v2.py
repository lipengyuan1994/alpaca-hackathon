"""Generic strict-cash simulator for the v2 multi-universe ETF study.

The simulator deliberately accepts a strategy object rather than importing a
particular universe.  This keeps Track A and Track B deterministic strategy
logic independent from execution, settlement and accounting.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_DOWN, Decimal
from typing import Any, Mapping, Protocol

import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

from .metrics import compute_metrics
from .protocol_v2 import CandidateSpec, CostScenarioV2, StudyProtocolV2


class StrategySignalLike(Protocol):
    asof: pd.Timestamp
    target_weights: Mapping[str, float]
    reason_code: str
    entries: tuple[str, ...]
    exits: tuple[str, ...]


class StrategyLike(Protocol):
    def evaluate(self, frames: Mapping[str, pd.DataFrame], index: int) -> StrategySignalLike: ...


@dataclass(frozen=True)
class GenericBacktestResult:
    candidate: CandidateSpec
    cost_scenario: str
    execution_delay_sessions: int
    equity: pd.DataFrame
    signals: pd.DataFrame
    orders: pd.DataFrame
    fills: pd.DataFrame
    cash_ledger: pd.DataFrame
    trades: pd.DataFrame
    metrics: dict[str, Any]
    component_ledger: pd.DataFrame = field(default_factory=pd.DataFrame)


def _normalise_bars(bars: pd.DataFrame) -> pd.DataFrame:
    required = {"date", "symbol", "open", "high", "low", "close"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"ETF_V2_BARS_COLUMNS_MISSING:{','.join(sorted(missing))}")
    frame = bars.copy()
    frame["date"] = pd.to_datetime(frame["date"], utc=True).dt.normalize()
    frame["symbol"] = frame["symbol"].astype(str).str.upper()
    for column in (
        "open",
        "high",
        "low",
        "close",
        "signal_open",
        "signal_high",
        "signal_low",
        "signal_close",
        "volume",
        "dividend",
        "split_factor",
    ):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame.duplicated(["date", "symbol"]).any():
        raise ValueError("ETF_V2_BARS_DUPLICATE_SESSION")
    prices = frame[["open", "high", "low", "close"]]
    if prices.isna().any().any() or (prices <= 0).any().any():
        raise ValueError("ETF_V2_BARS_INVALID_PRICE")
    if not (frame["low"] <= frame[["open", "close"]].min(axis=1)).all() or not (frame["high"] >= frame[["open", "close"]].max(axis=1)).all():
        raise ValueError("ETF_V2_BARS_OHLC_INVALID")
    return frame.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)


def _utc_normalize(value: str | date | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.normalize()


def _floor_quantity(value: float, decimals: int) -> float:
    if value <= 0:
        return 0.0
    quantum = Decimal(1).scaleb(-decimals)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_DOWN))


def _intersection_sessions(frame: pd.DataFrame, symbols: tuple[str, ...]) -> list[pd.Timestamp]:
    sets = [set(frame.loc[frame["symbol"] == symbol, "date"]) for symbol in symbols]
    if any(not values for values in sets):
        missing = [symbol for symbol, values in zip(symbols, sets, strict=True) if not values]
        raise ValueError(f"ETF_V2_SYMBOL_COVERAGE_MISSING:{','.join(missing)}")
    common = set.intersection(*sets)
    if len(common) < 2:
        raise ValueError("ETF_V2_INSUFFICIENT_COMMON_SESSIONS")
    first = max(min(values) for values in sets)
    last = min(max(values) for values in sets)
    expected = {value for values in sets for value in values if first <= value <= last}
    if expected != common:
        raise ValueError("ETF_V2_INTERNAL_SESSION_GAP")
    return sorted(common)


def _align_signal_frames(
    signal_frame: pd.DataFrame,
    symbols: tuple[str, ...],
    common_sessions: list[pd.Timestamp],
) -> tuple[dict[str, pd.DataFrame], dict[pd.Timestamp, int]]:
    """Build positional strategy frames on one shared calendar.

    Strategy implementations intentionally expose a small positional API:
    ``evaluate(frames, index)``.  Passing each symbol's independent history
    through that API is unsafe when listings have different start dates.  For
    example, QQQM begins in 2020 while SMH has history from 2019; QQQM's
    position 100 and SMH's position 100 then refer to different sessions.

    Restrict every signal frame to the intersection calendar before resetting
    its index.  The resulting positional index has the same date for every
    symbol while retaining all common warm-up history.  Execution and
    valuation continue to use the raw, date-keyed bars elsewhere in the
    simulator.
    """
    if not common_sessions:
        raise ValueError("ETF_V2_SIGNAL_CALENDAR_EMPTY")
    expected = pd.DatetimeIndex(common_sessions)
    expected_set = set(expected)
    aligned: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        item = signal_frame[signal_frame["symbol"] == symbol].sort_values("date", kind="stable")
        item = item[item["date"].isin(expected_set)].reset_index(drop=True)
        if len(item) != len(expected):
            raise ValueError(f"ETF_V2_SIGNAL_CALENDAR_MISMATCH:{symbol}")
        if not item["date"].reset_index(drop=True).equals(pd.Series(expected, name="date")):
            raise ValueError(f"ETF_V2_SIGNAL_CALENDAR_ORDER_MISMATCH:{symbol}")
        aligned[symbol] = item
    return aligned, {pd.Timestamp(value): index for index, value in enumerate(expected)}


def _settlement_date_v2(trade_date: pd.Timestamp, change: date, sessions: list[pd.Timestamp]) -> pd.Timestamp:
    """Return the next one or two actual exchange sessions.

    A settlement date is selected from the frozen exchange-session calendar,
    so weekends and exchange holidays can never become a fake settlement day.
    """
    offset = 1 if trade_date.date() >= change else 2
    # Clearing/settlement observes US federal market holidays even where a
    # synthetic fixture includes an exchange session on that date.  Derive the
    # holiday set from pandas' maintained calendar rather than a weekday-only
    # offset.  The exchange-session list still supplies the actual available
    # dates and handles weekends naturally.
    holiday_dates = {
        value.date()
        for value in USFederalHolidayCalendar().holidays(
            start=trade_date - pd.Timedelta(days=5),
            end=max(sessions) if sessions else trade_date + pd.Timedelta(days=10),
        )
    }
    future = [item for item in sessions if item > trade_date and item.date() not in holiday_dates]
    # At the final valuation boundary the settlement session can lie outside
    # the collected range.  Keep proceeds pending and record NaT rather than
    # inventing a weekday settlement or forcing an artificial liquidation.
    return future[offset - 1] if len(future) >= offset else pd.NaT


def _rows_for_date(frame: pd.DataFrame, date_value: pd.Timestamp, symbols: tuple[str, ...]) -> dict[str, pd.Series]:
    rows = frame[frame["date"] == date_value].set_index("symbol")
    missing = [symbol for symbol in symbols if symbol not in rows.index]
    if missing:
        raise ValueError(f"ETF_V2_EXECUTION_BAR_MISSING:{','.join(missing)}")
    return {symbol: rows.loc[symbol] for symbol in symbols}


def _mark_value(holdings: Mapping[str, float], rows: Mapping[str, pd.Series], column: str = "close") -> float:
    return sum(float(quantity) * float(rows[symbol][column]) for symbol, quantity in holdings.items() if symbol in rows)


def _payable(value: Any, fallback: pd.Timestamp) -> pd.Timestamp:
    if value is None or pd.isna(value):
        raise ValueError("ETF_V2_DIVIDEND_PAYABLE_DATE_MISSING")
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    payable = timestamp.normalize()
    if payable < fallback:
        raise ValueError("ETF_V2_DIVIDEND_PAYABLE_DATE_INVALID")
    return payable


def _target_changed(current: Mapping[str, float], previous: Mapping[str, float]) -> bool:
    return any(abs(float(current.get(symbol, 0.0)) - float(previous.get(symbol, 0.0))) > 1e-12 for symbol in set(current) | set(previous))


def run_generic_backtest(
    bars: pd.DataFrame,
    *,
    candidate: CandidateSpec,
    strategy: StrategyLike,
    cost: CostScenarioV2,
    protocol: StudyProtocolV2,
    start: str | date | None = None,
    end: str | date | None = None,
    initial_cash: float | None = None,
    execution_delay_sessions: int = 0,
) -> GenericBacktestResult:
    if execution_delay_sessions < 0:
        raise ValueError("ETF_V2_EXECUTION_DELAY_INVALID")
    frame = _normalise_bars(bars)
    universe = candidate.universe
    sessions = _intersection_sessions(frame, universe.all_symbols)
    if start is not None:
        sessions = [item for item in sessions if item >= _utc_normalize(start)]
    if end is not None:
        sessions = [item for item in sessions if item <= _utc_normalize(end)]
    if len(sessions) < 2:
        raise ValueError("ETF_V2_INSUFFICIENT_SESSIONS")
    # Keep the complete common calendar for indicator warm-up.  The requested
    # execution range is filtered above, but strategy histories must retain
    # preceding common sessions so long indicators are initialized correctly.
    all_session_dates = _intersection_sessions(frame, universe.all_symbols)
    signal_frame = frame.copy()
    signal_columns = {"signal_open": "open", "signal_high": "high", "signal_low": "low", "signal_close": "close"}
    if set(signal_columns).issubset(signal_frame.columns):
        for signal_column, feature_column in signal_columns.items():
            signal_frame[feature_column] = signal_frame[signal_column]
        signal_frame = signal_frame.drop(columns=list(signal_columns))
    frames, common_indices = _align_signal_frames(signal_frame, universe.all_symbols, all_session_dates)
    cash = float(protocol.initial_cash if initial_cash is None else initial_cash)
    starting_cash = cash
    holdings = {symbol: 0.0 for symbol in universe.tradable_symbols}
    average_basis = {symbol: 0.0 for symbol in universe.tradable_symbols}
    pending: list[dict[str, Any]] = []
    receivables: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []
    equity_rows: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    component_rows: list[dict[str, Any]] = []
    open_trades: dict[str, dict[str, Any]] = {}
    previous_target: dict[str, float] = {symbol: 0.0 for symbol in universe.tradable_symbols}
    delayed: deque[tuple[dict[str, float], StrategySignalLike]] = deque()
    peak_equity = starting_cash
    previous_signal_asof: pd.Timestamp | None = None
    shadow_equity = starting_cash
    shadow_peak_equity = starting_cash
    shadow_target: dict[str, float] = {symbol: 0.0 for symbol in universe.tradable_symbols}
    shadow_last_close: dict[str, float] | None = None

    for execution_date in sessions:
        execution_rows = _rows_for_date(frame, execution_date, universe.all_symbols)
        prior_dates = [item for item in all_session_dates if item < execution_date]
        prior_date = prior_dates[-1] if prior_dates else None
        prior_rows = _rows_for_date(frame, prior_date, universe.all_symbols) if prior_date is not None else execution_rows
        if candidate.strategy_id == "A09" and prior_date is not None:
            current_shadow_close = {symbol: float(prior_rows[symbol]["close"]) for symbol in universe.tradable_symbols}
            if shadow_last_close is not None:
                shadow_return = 0.0
                for symbol in universe.tradable_symbols:
                    previous_close = shadow_last_close.get(symbol, current_shadow_close[symbol])
                    if previous_close > 0:
                        shadow_return += float(shadow_target.get(symbol, 0.0)) * (current_shadow_close[symbol] / previous_close - 1.0)
                shadow_equity *= max(0.0, 1.0 + shadow_return)
                shadow_peak_equity = max(shadow_peak_equity, shadow_equity)
            shadow_last_close = current_shadow_close
        released = 0.0
        for item in list(pending):
            if not pd.isna(item["settle_date"]) and item["settle_date"] <= execution_date:
                cash += float(item["amount"])
                released += float(item["amount"])
                ledger.append({"date": execution_date.isoformat(), "kind": "sale_settled", "symbol": None, "amount": float(item["amount"]), "factor": 1.0})
                pending.remove(item)
        for item in list(receivables):
            if item["payable_date"] <= execution_date:
                cash += float(item["amount"])
                released += float(item["amount"])
                ledger.append({"date": execution_date.isoformat(), "kind": "dividend_paid", "symbol": None, "amount": float(item["amount"]), "factor": 1.0})
                receivables.remove(item)

        equity_before = cash + sum(item["amount"] for item in pending + receivables) + _mark_value(holdings, prior_rows)
        if prior_date is None:
            signal = _empty_signal(execution_date)
        else:
            # ``frames`` are restricted to the common session calendar by
            # _align_signal_frames, so this one index names ``prior_date`` in
            # every symbol's history (including signal-only proxies).
            prior_index = common_indices[prior_date]
            if candidate.strategy_id == "A09":
                context = {"shadow_equity": shadow_equity, "shadow_peak_equity": shadow_peak_equity, "shadow_drawdown": max(0.0, 1.0 - shadow_equity / shadow_peak_equity) if shadow_peak_equity > 0 else 0.0}
            else:
                context = {"shadow_equity": equity_before, "shadow_peak_equity": peak_equity, "shadow_drawdown": max(0.0, 1.0 - equity_before / peak_equity) if peak_equity > 0 else 0.0}
            try:
                signal = strategy.evaluate(frames, prior_index, context=context)
            except TypeError:
                signal = strategy.evaluate(frames, prior_index)
        decision_target = {symbol: max(0.0, min(protocol.target_investment, float(signal.target_weights.get(symbol, 0.0)))) for symbol in universe.tradable_symbols}
        if prior_date is None:
            execution_signal = _empty_signal(execution_date)
            target = {symbol: 0.0 for symbol in universe.tradable_symbols}
        elif execution_delay_sessions == 0:
            execution_signal, target = signal, decision_target
        else:
            delayed.append((decision_target, signal))
            if len(delayed) > execution_delay_sessions:
                target, execution_signal = delayed.popleft()
                # A newly observed deterministic exit invalidates a queued
                # increase for that symbol.  Reductions remain eligible and
                # are handled by the current session's target below.
                for symbol in universe.tradable_symbols:
                    if float(decision_target.get(symbol, 0.0)) <= 0.0 and float(target.get(symbol, 0.0)) > 0.0:
                        target[symbol] = 0.0
            else:
                target, execution_signal = {symbol: 0.0 for symbol in universe.tradable_symbols}, _empty_signal(execution_date, "DELAYED_ORDER_PENDING")
        force_rebalance = False
        reason = str(getattr(execution_signal, "reason_code", ""))
        if prior_date is not None and previous_signal_asof is not None and any(marker in reason for marker in ("WEEKLY", "MONTHLY", "COVARIANCE_CONTROLLED", "LEADERSHIP_FALLBACK", "EXPOSURE_LADDER", "ENSEMBLE")):
            current_signal_asof = pd.Timestamp(getattr(execution_signal, "asof", prior_date))
            if "MONTHLY" in reason:
                force_rebalance = (current_signal_asof.year, current_signal_asof.month) != (previous_signal_asof.year, previous_signal_asof.month)
            else:
                current_week = current_signal_asof.isocalendar()
                previous_week = previous_signal_asof.isocalendar()
                force_rebalance = (current_week.year, current_week.week) != (previous_week.year, previous_week.week)
        target_changed = (_target_changed(target, previous_target) if prior_date is not None else False) or force_rebalance
        signals.append({
            "candidate_id": candidate.candidate_id,
            "track_id": universe.track_id,
            "pair_id": universe.pair_id,
            "strategy_id": candidate.strategy_id,
            "variant": candidate.variant,
            "decision_date": signal.asof.isoformat(),
            "execution_date": execution_date.isoformat(),
            "information_cutoff": prior_date.isoformat() if prior_date is not None else None,
            "decision_target": json_weights(decision_target),
            "target": json_weights(target),
            "reason_code": signal.reason_code,
            "executed_reason_code": execution_signal.reason_code,
            "entries": ",".join(signal.entries),
            "exits": ",".join(signal.exits),
            "equity_before": equity_before,
            "force_rebalance": force_rebalance,
        })
        component_targets = getattr(execution_signal, "component_target_weights", {}) or {}
        if candidate.strategy_id == "A09":
            shadow_target = {symbol: float(getattr(execution_signal, "shadow_target_weights", {}).get(symbol, 0.0)) for symbol in universe.tradable_symbols}
        if not component_targets:
            component_targets = {"account": target}
        for component_id, component_weights in sorted(component_targets.items()):
            for symbol in universe.tradable_symbols:
                component_rows.append({
                    "candidate_id": candidate.candidate_id,
                    "track_id": universe.track_id,
                    "pair_id": universe.pair_id,
                    "execution_date": execution_date.isoformat(),
                    "information_cutoff": prior_date.isoformat() if prior_date is not None else None,
                    "component_id": str(component_id),
                    "symbol": symbol,
                    "target_weight": float(component_weights.get(symbol, 0.0)),
                    "execution_delay_sessions": execution_delay_sessions,
                })

        if prior_date is not None:
            # Corporate actions apply to positions held through the ex-date.
            for symbol in universe.tradable_symbols:
                row = execution_rows[symbol]
                factor = float(row.get("split_factor", 1.0) or 1.0)
                if not pd.notna(factor) or factor <= 0:
                    raise ValueError("ETF_V2_SPLIT_FACTOR_INVALID")
                if factor != 1.0 and holdings[symbol] > 0:
                    holdings[symbol] *= factor
                    average_basis[symbol] /= factor
                    if symbol in open_trades:
                        open_trades[symbol]["entry_quantity"] *= factor
                        open_trades[symbol]["entry_price"] /= factor
                    ledger.append({"date": execution_date.isoformat(), "kind": "split", "symbol": symbol, "amount": 0.0, "factor": factor})
                dividend = float(row.get("dividend", 0.0) or 0.0)
                if dividend < 0:
                    raise ValueError("ETF_V2_DIVIDEND_INVALID")
                if dividend > 0 and holdings[symbol] > 0:
                    payable_date = _payable(row.get("dividend_payable_date"), execution_date)
                    amount = holdings[symbol] * dividend
                    receivables.append({"payable_date": payable_date, "amount": amount})
                    ledger.append({"date": execution_date.isoformat(), "kind": "dividend_receivable", "symbol": symbol, "amount": amount, "factor": 1.0, "payable_date": payable_date.isoformat()})

            # Size from the valuation captured before current-session splits,
            # dividends and opening prices.  Revaluing split-adjusted shares
            # against the pre-split prior close would multiply equity.
            equity_for_target = equity_before
            desired = dict(holdings)
            if target_changed:
                for symbol in universe.tradable_symbols:
                    if abs(target[symbol] - previous_target.get(symbol, 0.0)) > 1e-12:
                        desired[symbol] = _floor_quantity(target[symbol] * equity_for_target / float(execution_rows[symbol]["open"]), protocol.quantity_decimals)

            # Reductions always precede purchases; their proceeds settle later.
            for symbol in universe.tradable_symbols:
                quantity = _floor_quantity(holdings[symbol] - desired[symbol], protocol.quantity_decimals)
                if quantity <= 0:
                    continue
                open_price = float(execution_rows[symbol]["open"])
                fill_price = open_price * (1.0 - cost.basis_points_per_side / 10_000.0)
                if desired[symbol] > 0 and quantity * fill_price < protocol.minimum_order_notional:
                    orders.append({"order_id": f"{candidate.candidate_id}-{execution_date.date()}-{symbol}-sell-expired", "candidate_id": candidate.candidate_id, "date": execution_date.isoformat(), "symbol": symbol, "side": "sell", "quantity": quantity, "target_quantity": desired[symbol], "status": "EXPIRED_MINIMUM_NOTIONAL"})
                    continue
                notional = quantity * fill_price
                fee = float(cost.sell_fee)
                proceeds = notional - fee
                holdings[symbol] -= quantity
                settlement = _settlement_date_v2(execution_date, protocol.settlement_change, all_session_dates)
                pending.append({"settle_date": settlement, "amount": proceeds})
                order_id = f"{candidate.candidate_id}-{execution_date.date()}-{symbol}-sell"
                orders.append({"order_id": order_id, "candidate_id": candidate.candidate_id, "date": execution_date.isoformat(), "symbol": symbol, "side": "sell", "quantity": quantity, "target_quantity": desired[symbol], "status": "filled"})
                settlement_label = settlement.isoformat() if not pd.isna(settlement) else None
                fills.append({"order_id": order_id, "candidate_id": candidate.candidate_id, "date": execution_date.isoformat(), "symbol": symbol, "side": "sell", "quantity": quantity, "price": fill_price, "notional": notional, "fee": fee, "adverse_cost": quantity * (open_price - fill_price), "settlement_date": settlement_label})
                ledger.append({"date": execution_date.isoformat(), "kind": "sale_pending", "symbol": symbol, "amount": proceeds, "settlement_date": settlement_label, "factor": 1.0})
                open_trade = open_trades.get(symbol)
                if open_trade:
                    closed_quantity = min(quantity, float(open_trade["entry_quantity"]))
                    trades.append({**open_trade, "exit_date": execution_date.isoformat(), "exit_price": fill_price, "exit_quantity": closed_quantity, "gross_pnl": (fill_price - open_trade["entry_price"]) * closed_quantity, "fees": open_trade["fees"] + fee})
                    remainder = float(open_trade["entry_quantity"]) - closed_quantity
                    if remainder > 0:
                        open_trade["entry_quantity"] = remainder
                        open_trade["fees"] = 0.0
                    else:
                        open_trades.pop(symbol, None)

            buy_requests: list[tuple[str, float, float, float, float]] = []
            for symbol in universe.tradable_symbols:
                quantity = desired[symbol] - holdings[symbol]
                open_price = float(execution_rows[symbol]["open"])
                fill_price = open_price * (1.0 + cost.basis_points_per_side / 10_000.0)
                if quantity * fill_price >= protocol.minimum_order_notional:
                    buy_requests.append((symbol, quantity, fill_price, quantity * fill_price, open_price))
                elif quantity > 0:
                    orders.append({"order_id": f"{candidate.candidate_id}-{execution_date.date()}-{symbol}-buy-expired", "candidate_id": candidate.candidate_id, "date": execution_date.isoformat(), "symbol": symbol, "side": "buy", "quantity": quantity, "target_quantity": desired[symbol], "status": "EXPIRED_MINIMUM_NOTIONAL"})
            available = max(0.0, cash)
            total_required = sum(item[3] for item in buy_requests)
            scale = min(1.0, available / total_required) if total_required else 0.0
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
                old_quantity = holdings[symbol]
                holdings[symbol] += quantity
                average_basis[symbol] = ((average_basis[symbol] * old_quantity) + notional) / holdings[symbol] if holdings[symbol] else 0.0
                order_id = f"{candidate.candidate_id}-{execution_date.date()}-{symbol}-buy"
                orders.append({"order_id": order_id, "candidate_id": candidate.candidate_id, "date": execution_date.isoformat(), "symbol": symbol, "side": "buy", "quantity": quantity, "requested_quantity": requested, "target_quantity": desired[symbol], "status": "filled" if quantity + 10 ** -protocol.quantity_decimals >= requested * scale else "FILLED_PARTIAL"})
                fills.append({"order_id": order_id, "candidate_id": candidate.candidate_id, "date": execution_date.isoformat(), "symbol": symbol, "side": "buy", "quantity": quantity, "price": fill_price, "notional": notional, "fee": 0.0, "adverse_cost": quantity * (fill_price - open_price), "settlement_date": execution_date.isoformat()})
                ledger.append({"date": execution_date.isoformat(), "kind": "buy", "symbol": symbol, "amount": -notional, "factor": 1.0})
                if symbol in open_trades:
                    trade = open_trades[symbol]
                    total = float(trade["entry_quantity"]) + quantity
                    trade["entry_price"] = ((trade["entry_price"] * trade["entry_quantity"]) + fill_price * quantity) / total
                    trade["entry_quantity"] = total
                else:
                    open_trades[symbol] = {"symbol": symbol, "entry_date": execution_date.isoformat(), "entry_price": fill_price, "entry_quantity": quantity, "fees": 0.0}
            previous_target = dict(target)
            previous_signal_asof = pd.Timestamp(getattr(execution_signal, "asof", prior_date))

        value = _mark_value(holdings, execution_rows)
        pending_value = sum(float(item["amount"]) for item in pending + receivables)
        total_equity = cash + value + pending_value
        invested_exposure = value / total_equity if total_equity else 0.0
        equity_rows.append({"date": execution_date.isoformat(), "candidate_id": candidate.candidate_id, "track_id": universe.track_id, "pair_id": universe.pair_id, "cash_settled": cash, "cash_pending": pending_value, "holdings_value": value, "equity": total_equity, "invested_exposure": invested_exposure, "approx_3x_invested_exposure": 3.0 * invested_exposure if universe.track_id == "b" else None, "released_cash": released})
        peak_equity = max(peak_equity, cash + value + pending_value)

    final_rows = _rows_for_date(frame, sessions[-1], universe.tradable_symbols)
    for symbol, trade in open_trades.items():
        trades.append({**trade, "exit_date": None, "exit_price": None, "exit_quantity": holdings[symbol], "gross_pnl": (float(final_rows[symbol]["close"]) - trade["entry_price"]) * holdings[symbol], "fees": trade["fees"]})
    equity = pd.DataFrame(equity_rows)
    equity["daily_profit"] = equity["equity"].diff().fillna(equity["equity"].iloc[0] - starting_cash)
    equity["daily_return"] = equity["equity"].pct_change().fillna(equity["equity"].iloc[0] / starting_cash - 1.0)
    equity["cumulative_profit"] = equity["equity"] - starting_cash
    equity["drawdown"] = equity["equity"] / equity["equity"].cummax() - 1.0
    fills_frame = pd.DataFrame(fills)
    metrics = compute_metrics(equity, fills_frame, initial_cash=starting_cash, start=sessions[0], end=sessions[-1], trades=pd.DataFrame(trades), cash_ledger=pd.DataFrame(ledger))
    metrics.update({"starting_equity": starting_cash, "ending_equity": float(equity["equity"].iloc[-1]), "net_pnl": float(equity["equity"].iloc[-1] - starting_cash), "candidate_id": candidate.candidate_id, "track_id": universe.track_id, "pair_id": universe.pair_id, "strategy_id": candidate.strategy_id, "variant": candidate.variant, "cost_scenario": cost.name, "execution_delay_sessions": execution_delay_sessions, "protocol_hash": protocol.protocol_hash})
    return GenericBacktestResult(
        candidate,
        cost.name,
        execution_delay_sessions,
        equity,
        pd.DataFrame(signals),
        pd.DataFrame(orders),
        fills_frame,
        pd.DataFrame(ledger),
        pd.DataFrame(trades),
        metrics,
        pd.DataFrame(component_rows),
    )


def json_weights(value: Mapping[str, float]) -> str:
    import json

    return json.dumps({str(key): round(float(item), 12) for key, item in value.items()}, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class _EmptySignal:
    asof: pd.Timestamp
    target_weights: Mapping[str, float]
    reason_code: str
    entries: tuple[str, ...] = ()
    exits: tuple[str, ...] = ()


def _empty_signal(asof: pd.Timestamp, reason: str = "WARMUP_OR_NO_PRIOR_SESSION") -> _EmptySignal:
    return _EmptySignal(asof, {}, reason)
