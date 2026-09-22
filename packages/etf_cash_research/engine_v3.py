"""The v3 deterministic ETF execution engine.

This module is intentionally independent from the v1 and v2 simulators.  It
owns the *execution* part of a study: a strategy receives a point-in-time
``DecisionContext`` and returns declarative ``OrderIntent`` objects.  The
engine sizes those intents from the previous close, queues immutable delayed
orders, applies corporate actions and settlement, and records physical and
component ledgers.

The module does not calculate indicators.  A caller can put already frozen
features in ``EngineConfig.feature_cache`` (or in the context returned to a
strategy).  This keeps the expensive feature work outside the per-account
execution loop and makes strategy decisions easy to reproduce.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field, replace
from datetime import date
from enum import Enum
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Protocol, Sequence

import numpy as np
import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

MICROSHARES = 1_000_000


class SizingMode(str, Enum):
    """How an intent changes a component's ownership."""

    ENTER_SLEEVE = "enter_sleeve"
    HOLD_SHARES = "hold_shares"
    REBALANCE = "rebalance"
    EXIT_FULLY = "exit_fully"
    REDUCE = "reduce"


@dataclass(frozen=True)
class PositionView:
    """Point-in-time actual ownership supplied to a strategy."""

    component_id: str
    symbol: str
    quantity: float
    quantity_microshares: int
    entry_session: pd.Timestamp | None
    holding_sessions: int


@dataclass(frozen=True)
class OrderIntent:
    """A strategy's declarative request.

    ``target_weight`` is a fraction of total prior-close account equity.
    ``target_quantity`` is expressed in shares at the information cutoff and
    is converted through any split occurring before execution.  The engine
    never treats an omitted symbol as an exit; a strategy must use
    ``EXIT_FULLY`` explicitly.
    """

    symbol: str
    mode: SizingMode | str
    component_id: str = "account"
    target_weight: float | None = None
    target_quantity: float | None = None
    reduction_fraction: float | None = None
    reason: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", str(self.symbol).upper())
        object.__setattr__(self, "component_id", str(self.component_id))
        mode = self.mode if isinstance(self.mode, SizingMode) else SizingMode(str(self.mode))
        object.__setattr__(self, "mode", mode)
        if self.target_weight is not None and (not math.isfinite(float(self.target_weight)) or float(self.target_weight) < 0):
            raise ValueError("ETF_V3_TARGET_WEIGHT_INVALID")
        if self.target_quantity is not None and (not math.isfinite(float(self.target_quantity)) or float(self.target_quantity) < 0):
            raise ValueError("ETF_V3_TARGET_QUANTITY_INVALID")
        if self.reduction_fraction is not None and not 0 <= float(self.reduction_fraction) <= 1:
            raise ValueError("ETF_V3_REDUCTION_FRACTION_INVALID")


@dataclass(frozen=True)
class SizedOrder:
    """Immutable request created at the decision cutoff.

    The requested quantity is in integer microshares.  For delayed orders it
    is the quantity calculated at the original cutoff; only split factors may
    change it before the designated execution session.
    """

    order_id: str
    component_id: str
    symbol: str
    side: str
    quantity_microshares: int
    requested_quantity_microshares: int
    target_quantity_microshares: int
    reference_price: float
    reference_equity: float
    decision_session: pd.Timestamp
    information_cutoff: pd.Timestamp | None
    execution_session: pd.Timestamp
    expires_session: pd.Timestamp
    mode: SizingMode
    reason: str = ""
    status: str = "scheduled"

    @property
    def quantity(self) -> float:
        return self.quantity_microshares / MICROSHARES

    def adjusted_for_split(self, factor: float) -> "SizedOrder":
        if factor <= 0 or not math.isfinite(float(factor)):
            raise ValueError("ETF_V3_SPLIT_FACTOR_INVALID")
        if factor == 1:
            return self
        qty = _micro_floor(self.quantity * factor)
        requested = _micro_floor(self.requested_quantity_microshares / MICROSHARES * factor)
        target = _micro_floor(self.target_quantity_microshares / MICROSHARES * factor)
        return replace(
            self,
            quantity_microshares=qty,
            requested_quantity_microshares=requested,
            target_quantity_microshares=target,
            reference_price=self.reference_price / factor,
        )


@dataclass(frozen=True)
class ExecutionFeedback:
    """Actual outcome returned in the next decision context."""

    order_id: str
    component_id: str
    symbol: str
    side: str
    decision_session: pd.Timestamp
    execution_session: pd.Timestamp | None
    requested_quantity_microshares: int
    filled_quantity_microshares: int
    fill_price: float | None
    status: str
    reason: str = ""
    settlement_session: pd.Timestamp | None = None
    ownership_before_microshares: int = 0
    ownership_after_microshares: int = 0
    holding_age_sessions: int = 0
    fully_liquidated: bool = False

    @property
    def filled_quantity(self) -> float:
        return self.filled_quantity_microshares / MICROSHARES


@dataclass(frozen=True)
class DecisionContext:
    """The only state a v3 strategy may use to make a decision.

    ``prior_close`` and ``features`` are point-in-time values.  ``history``
    contains rows through the cutoff only when the caller enables it; the
    default engine configuration supplies it for fixture-friendly strategies.
    Current-session OHLC/open values are deliberately absent.
    """

    execution_session: pd.Timestamp
    information_cutoff: pd.Timestamp | None
    prior_close: Mapping[str, float]
    prior_close_equity: float
    settled_cash: float
    unsettled_cash: float
    dividend_receivables: float
    positions: Mapping[tuple[str, str], PositionView]
    physical_positions: Mapping[str, PositionView]
    features: Mapping[str, Mapping[str, float]]
    history: Mapping[str, pd.DataFrame]
    review_weekly: bool
    review_monthly: bool
    initial_session: bool
    pending_orders: tuple[SizedOrder, ...] = ()
    recent_feedback: tuple[ExecutionFeedback, ...] = ()
    split_factors: Mapping[str, float] = field(default_factory=dict)


class StrategyV3(Protocol):
    """Protocol accepted by :func:`run_engine_v3`."""

    def decide(self, context: DecisionContext) -> Iterable[OrderIntent]: ...


@dataclass(frozen=True)
class EngineConfig:
    """Outcome-affecting execution settings."""

    initial_cash: float = 1_000.0
    target_investment: float = 0.99
    minimum_order_notional: float = 5.0
    quantity_decimals: int = 6
    cost_basis_points: float = 5.0
    sell_fee: float = 0.01
    execution_delay_sessions: int = 0
    settlement_change: date = date(2024, 5, 28)
    include_history: bool = True
    # A cache keyed by execution session or by its information cutoff.  The
    # value is ``{symbol: {feature_name: value}}``.  The engine only reads it.
    feature_cache: Mapping[Any, Mapping[str, Mapping[str, float]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.initial_cash <= 0 or not math.isfinite(float(self.initial_cash)):
            raise ValueError("ETF_V3_INITIAL_CASH_INVALID")
        if not 0 < self.target_investment <= 0.99 + 1e-12:
            raise ValueError("ETF_V3_TARGET_INVESTMENT_INVALID")
        if self.minimum_order_notional < 0 or self.quantity_decimals < 0:
            raise ValueError("ETF_V3_SIZING_CONFIG_INVALID")
        if self.cost_basis_points < 0 or self.sell_fee < 0 or self.execution_delay_sessions < 0:
            raise ValueError("ETF_V3_COST_OR_DELAY_INVALID")


@dataclass(frozen=True)
class EngineResult:
    """All outcome-bearing ledgers for one independent account."""

    equity: pd.DataFrame
    signals: pd.DataFrame
    orders: pd.DataFrame
    fills: pd.DataFrame
    cash_ledger: pd.DataFrame
    component_ledger: pd.DataFrame
    trades: pd.DataFrame
    metrics: Mapping[str, Any]
    account_hash: str
    final_positions: Mapping[str, PositionView]


@dataclass
class _Lot:
    quantity_microshares: int
    entry_index: int
    entry_session: pd.Timestamp
    entry_price: float


@dataclass
class _PendingCash:
    amount: float
    settlement_session: pd.Timestamp | None
    kind: str
    symbol: str | None = None
    order_id: str | None = None


@dataclass
class _MutableState:
    cash: float
    physical_lots: dict[str, list[_Lot]]
    component_lots: dict[tuple[str, str], list[_Lot]]
    pending_cash: list[_PendingCash]
    pending_orders: dict[tuple[str, str], SizedOrder]
    feedback: list[ExecutionFeedback]
    trades: list[dict[str, Any]]
    cash_ledger: list[dict[str, Any]]
    component_ledger: list[dict[str, Any]]
    orders: list[dict[str, Any]]
    fills: list[dict[str, Any]]
    realized: dict[tuple[str, str], dict[str, Any]]
    physical_realized_pnl: float
    physical_realized_gross_pnl: float
    physical_realized_fees: float


def _micro_floor(value: float) -> int:
    if not math.isfinite(float(value)) or value <= 0:
        return 0
    # Decimal quantization is slower than a microshare integer conversion and
    # the inputs are already positive finite floats from normalized bars.
    return int(math.floor(float(value) * MICROSHARES + 1e-9))


def _qty(microshares: int) -> float:
    return int(microshares) / MICROSHARES


def _utc(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    else:
        stamp = stamp.tz_convert("UTC")
    return stamp.normalize()


def _normalise_bars(bars: pd.DataFrame) -> pd.DataFrame:
    required = {"date", "symbol", "open", "high", "low", "close"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"ETF_V3_BARS_COLUMNS_MISSING:{','.join(sorted(missing))}")
    frame = bars.copy()
    frame["date"] = pd.to_datetime(frame["date"], utc=True).dt.normalize()
    frame["symbol"] = frame["symbol"].astype(str).str.upper()
    for column in ("open", "high", "low", "close", "volume", "dividend", "split_factor"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "dividend" not in frame:
        frame["dividend"] = 0.0
    if "split_factor" not in frame:
        frame["split_factor"] = 1.0
    if "dividend_payable_date" not in frame:
        frame["dividend_payable_date"] = pd.NaT
    if frame.duplicated(["date", "symbol"]).any():
        raise ValueError("ETF_V3_BARS_DUPLICATE_SESSION")
    prices = frame[["open", "high", "low", "close"]]
    if prices.isna().any().any() or (prices <= 0).any().any():
        raise ValueError("ETF_V3_BARS_INVALID_PRICE")
    if not (frame["low"] <= frame[["open", "close"]].min(axis=1)).all() or not (frame["high"] >= frame[["open", "close"]].max(axis=1)).all():
        raise ValueError("ETF_V3_BARS_OHLC_INVALID")
    if frame["split_factor"].isna().any() or (frame["split_factor"] <= 0).any():
        raise ValueError("ETF_V3_SPLIT_FACTOR_INVALID")
    if (frame["dividend"] < 0).any():
        raise ValueError("ETF_V3_DIVIDEND_INVALID")
    return frame.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)


def _calendar_map(calendar: pd.DataFrame | None, sessions: Sequence[pd.Timestamp], change: date) -> dict[pd.Timestamp, pd.Timestamp | None]:
    """Normalize the frozen settlement calendar supplied by the data layer."""

    if calendar is not None:
        required = {"date", "settlement_date"}
        missing = required - set(calendar.columns)
        if missing:
            raise ValueError(f"ETF_V3_CALENDAR_COLUMNS_MISSING:{','.join(sorted(missing))}")
        item = calendar.copy()
        item["date"] = pd.to_datetime(item["date"], utc=True).dt.normalize()
        item["settlement_date"] = pd.to_datetime(item["settlement_date"], utc=True).dt.normalize()
        if item.duplicated("date").any():
            raise ValueError("ETF_V3_CALENDAR_DUPLICATE_DATE")
        return {row.date: (None if pd.isna(row.settlement_date) else row.settlement_date) for row in item.itertuples()}

    # Fixture fallback.  Real runs should always pass the immutable calendar;
    # this fallback keeps the tiny public API convenient for unit tests.
    holidays = {
        stamp.date()
        for stamp in USFederalHolidayCalendar().holidays(
            start=min(sessions) if sessions else pd.Timestamp("2000-01-01", tz="UTC"),
            end=max(sessions) + pd.Timedelta(days=10) if sessions else pd.Timestamp("2000-01-31", tz="UTC"),
        )
    }
    ordered = list(sessions)
    output: dict[pd.Timestamp, pd.Timestamp | None] = {}
    for index, session in enumerate(ordered):
        offset = 1 if session.date() >= change else 2
        candidates = [item for item in ordered[index + 1 :] if item.date() not in holidays]
        output[session] = candidates[offset - 1] if len(candidates) >= offset else None
    return output


def _session_rows(frame: pd.DataFrame, sessions: Sequence[pd.Timestamp], symbols: Sequence[str]) -> dict[pd.Timestamp, dict[str, Mapping[str, Any]]]:
    # Materialize the keyed row records once.  The prior implementation used
    # a MultiIndex ``.loc`` for every symbol/session, which became a dominant
    # cost when the v3 matrix ran hundreds of independent accounts.
    keyed = frame.set_index(["date", "symbol"]).to_dict("index")
    result: dict[pd.Timestamp, dict[str, Mapping[str, Any]]] = {}
    for session in sessions:
        result[session] = {}
        for symbol in symbols:
            value = keyed.get((session, symbol))
            if value is None:
                raise ValueError(f"ETF_V3_EXECUTION_BAR_MISSING:{session.date()}:{symbol}")
            result[session][symbol] = value
    return result


def _last_rows(frame: pd.DataFrame, symbols: Sequence[str]) -> dict[str, pd.DataFrame]:
    return {
        symbol: frame.loc[frame["symbol"] == symbol].sort_values("date", kind="stable").reset_index(drop=True)
        for symbol in symbols
    }


def _prior_rows_by_session(
    frames: Mapping[str, pd.DataFrame], sessions: Sequence[pd.Timestamp]
) -> dict[pd.Timestamp, dict[str, pd.Series]]:
    output: dict[pd.Timestamp, dict[str, pd.Series]] = {}
    for index, session in enumerate(sessions):
        prior = sessions[index - 1] if index else None
        if prior is None:
            output[session] = {}
            continue
        output[session] = {
            symbol: value.iloc[value["date"].searchsorted(prior, side="right") - 1]
            for symbol, value in frames.items()
            if (value["date"] <= prior).any()
        }
    return output


def _all_lots_quantity(lots: Iterable[_Lot]) -> int:
    return sum(max(0, lot.quantity_microshares) for lot in lots)


def _position_view(component_id: str, symbol: str, lots: Sequence[_Lot], index: int, *, include_current_session: bool = True) -> PositionView:
    quantity = _all_lots_quantity(lots)
    if not quantity:
        return PositionView(component_id, symbol, 0.0, 0, None, 0)
    first = min(lots, key=lambda lot: lot.entry_index)
    return PositionView(
        component_id,
        symbol,
        _qty(quantity),
        quantity,
        first.entry_session,
        index - first.entry_index + (1 if include_current_session else 0),
    )


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _call_strategy(strategy: Any, context: DecisionContext) -> list[OrderIntent]:
    if hasattr(strategy, "decide"):
        raw = strategy.decide(context)
    elif hasattr(strategy, "evaluate"):
        raw = strategy.evaluate(context)
    else:
        raise TypeError("ETF_V3_STRATEGY_MISSING_DECIDE")
    if raw is None:
        return []
    if isinstance(raw, OrderIntent):
        raw = [raw]
    intents: list[OrderIntent] = []
    for item in raw:
        if not isinstance(item, OrderIntent):
            if isinstance(item, Mapping):
                item = OrderIntent(**item)
            else:
                raise TypeError("ETF_V3_STRATEGY_INTENT_INVALID")
        intents.append(item)
    return intents


def _intent_target_quantity(
    intent: OrderIntent,
    context: DecisionContext,
    *,
    split_factors: Mapping[str, float],
    config: EngineConfig,
) -> int | None:
    """Resolve an intent's absolute target for pending-order comparison."""

    factor = float(split_factors.get(intent.symbol, 1.0))
    current = context.positions.get((intent.component_id, intent.symbol), PositionView(intent.component_id, intent.symbol, 0.0, 0, None, 0)).quantity_microshares
    if intent.mode in {SizingMode.ENTER_SLEEVE, SizingMode.REBALANCE}:
        if intent.target_weight is not None:
            price = context.prior_close.get(intent.symbol)
            if price is None or price <= 0:
                return None
            return _micro_floor(float(intent.target_weight) * context.prior_close_equity / float(price) * factor)
        if intent.target_quantity is not None:
            return _micro_floor(float(intent.target_quantity) * factor)
        return None
    if intent.mode == SizingMode.HOLD_SHARES:
        for pending in context.pending_orders:
            if pending.component_id == intent.component_id and pending.symbol == intent.symbol:
                return pending.target_quantity_microshares
        return current if intent.target_quantity is None else _micro_floor(float(intent.target_quantity) * factor)
    if intent.mode == SizingMode.EXIT_FULLY:
        return 0
    if intent.mode == SizingMode.REDUCE:
        if intent.target_quantity is not None:
            return min(current, _micro_floor(float(intent.target_quantity) * factor))
        if intent.reduction_fraction is not None:
            return _micro_floor(_qty(current) * (1.0 - float(intent.reduction_fraction)))
    return None


def _feature_for(config: EngineConfig, session: pd.Timestamp, cutoff: pd.Timestamp | None) -> Mapping[str, Mapping[str, float]]:
    # A current-session cache key is never a valid fallback: doing so would
    # allow a feature store with forward bars to leak the execution bar into
    # a prior-close decision.  The session argument is retained in the
    # signature for callers that key their store by execution date, but is
    # deliberately unused.
    del session
    for key in (cutoff, cutoff.date() if cutoff is not None else None):
        if key is None:
            continue
        if key in config.feature_cache:
            return config.feature_cache[key]
    return {}


def _canonical_frame(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    records: list[dict[str, Any]] = []
    for item in frame.to_dict(orient="records"):
        record: dict[str, Any] = {}
        for key in sorted(item):
            value = item[key]
            if isinstance(value, (pd.Timestamp, np.datetime64)):
                value = pd.Timestamp(value).isoformat()
            elif pd.isna(value) if not isinstance(value, (list, dict, tuple)) else False:
                value = None
            elif isinstance(value, (np.integer, np.floating)):
                value = value.item()
            record[str(key)] = value
        records.append(record)
    return records


def _account_hash(result_parts: Mapping[str, Any]) -> str:
    payload = json.dumps(result_parts, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _empty_frame(columns: Sequence[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=list(columns))


def _sum_component_lots(state: _MutableState, symbol: str) -> int:
    return sum(_all_lots_quantity(lots) for (component, item), lots in state.component_lots.items() if item == symbol)


def _component_ids(state: _MutableState, symbols: Sequence[str]) -> set[str]:
    ids = {component for component, _ in state.component_lots}
    if not ids:
        ids.add("account")
        for symbol in symbols:
            state.component_lots[("account", symbol)] = []
    return ids


def _ensure_account_component(state: _MutableState, symbols: Sequence[str], index: int, sessions: Sequence[pd.Timestamp]) -> None:
    """Assign an unallocated physical opening position to the account sleeve."""

    for symbol in symbols:
        physical = _all_lots_quantity(state.physical_lots.get(symbol, []))
        owned = _sum_component_lots(state, symbol)
        if physical > owned:
            lots = state.component_lots.setdefault(("account", symbol), [])
            lots.append(_Lot(physical - owned, index, sessions[index], 0.0))


def _consume_lots(
    lots: list[_Lot], quantity: int, exit_index: int, exit_session: pd.Timestamp, exit_price: float, *, component_id: str, symbol: str, trades: list[dict[str, Any]], internal: bool = False
) -> int:
    remaining = quantity
    while remaining > 0 and lots:
        lot = lots[0]
        consumed = min(remaining, lot.quantity_microshares)
        lot.quantity_microshares -= consumed
        remaining -= consumed
        if lot.quantity_microshares <= 0:
            lots.pop(0)
    return quantity - remaining


def _add_lot(lots: list[_Lot], quantity: int, index: int, session: pd.Timestamp, price: float) -> None:
    if quantity <= 0:
        return
    lots.append(_Lot(quantity, index, session, price))


def _rows_with_schema(rows: list[dict[str, Any]], columns: Sequence[str]) -> pd.DataFrame:
    if not rows:
        return _empty_frame(columns)
    return pd.DataFrame(rows)


def run_engine_v3(
    bars: pd.DataFrame,
    *,
    strategy: StrategyV3 | Any,
    config: EngineConfig = EngineConfig(),
    calendar: pd.DataFrame | None = None,
    tradable_symbols: Sequence[str] | None = None,
    start: str | date | pd.Timestamp | None = None,
    end: str | date | pd.Timestamp | None = None,
    candidate_id: str = "V3_ACCOUNT",
    initial_positions: Mapping[str, float] | None = None,
    feature_store: Any | None = None,
) -> EngineResult:
    """Run one deterministic account.

    ``bars`` may contain warm-up rows before ``start``.  Sessions are selected
    from the intersection of ``tradable_symbols`` and the requested range,
    while prior-close context/history retains all earlier available rows.
    """

    if config.execution_delay_sessions < 0:
        raise ValueError("ETF_V3_EXECUTION_DELAY_INVALID")
    frame = _normalise_bars(bars)
    symbols = tuple(str(item).upper() for item in (tradable_symbols or sorted(frame["symbol"].unique())))
    if not symbols:
        raise ValueError("ETF_V3_SYMBOLS_EMPTY")
    by_symbol = _last_rows(frame, tuple(sorted(set(frame["symbol"]))))
    missing_symbols = [symbol for symbol in symbols if symbol not in by_symbol or by_symbol[symbol].empty]
    if missing_symbols:
        raise ValueError(f"ETF_V3_SYMBOL_COVERAGE_MISSING:{','.join(missing_symbols)}")
    date_sets = {symbol: set(by_symbol[symbol]["date"]) for symbol in symbols}
    common_start = max(min(values) for values in date_sets.values())
    common_end = min(max(values) for values in date_sets.values())
    sessions_set = set.intersection(*date_sets.values())
    all_sessions = sorted(sessions_set)
    if len(all_sessions) < 1:
        raise ValueError("ETF_V3_INSUFFICIENT_COMMON_SESSIONS")
    lower = _utc(start) if start is not None else all_sessions[0]
    upper = _utc(end) if end is not None else all_sessions[-1]
    coverage_start = lower if start is not None else common_start
    coverage_end = upper if end is not None else common_end
    if calendar is not None:
        calendar_dates = pd.to_datetime(calendar["date"], utc=True, errors="coerce").dt.normalize()
        if calendar_dates.isna().any():
            raise ValueError("ETF_V3_CALENDAR_DATE_INVALID")
        expected_coverage = {item for item in calendar_dates if coverage_start <= item <= coverage_end}
        if not expected_coverage:
            raise ValueError("ETF_V3_CALENDAR_COVERAGE_EMPTY")
    else:
        expected_coverage = {item for item in all_sessions if coverage_start <= item <= coverage_end}
    for symbol, values in date_sets.items():
        actual_coverage = {item for item in values if coverage_start <= item <= coverage_end}
        if actual_coverage != expected_coverage:
            missing = sorted(expected_coverage - actual_coverage)
            extra = sorted(actual_coverage - expected_coverage)
            detail = missing[0].date().isoformat() if missing else (extra[0].date().isoformat() if extra else "unknown")
            raise ValueError(f"ETF_V3_SESSION_COVERAGE_MISSING:{symbol}:{detail}")
    sessions = [item for item in all_sessions if lower <= item <= upper]
    if not sessions:
        raise ValueError("ETF_V3_EXECUTION_RANGE_EMPTY")
    calendar_map = _calendar_map(calendar, all_sessions, config.settlement_change)
    if calendar is not None:
        missing_calendar = [session for session in all_sessions if session not in calendar_map]
        if missing_calendar:
            raise ValueError("ETF_V3_CALENDAR_COVERAGE_MISSING:" + ",".join(item.date().isoformat() for item in missing_calendar))
        invalid_calendar = [session for session, settlement in calendar_map.items() if settlement is not None and settlement < session]
        if invalid_calendar:
            raise ValueError("ETF_V3_CALENDAR_SETTLEMENT_BEFORE_TRADE")
    rows_by_session = _session_rows(frame, all_sessions, symbols)
    by_symbol_frame = {symbol: by_symbol[symbol] for symbol in by_symbol}
    state = _MutableState(
        cash=float(config.initial_cash),
        physical_lots={symbol: [] for symbol in symbols},
        component_lots={("account", symbol): [] for symbol in symbols},
        pending_cash=[],
        pending_orders={},
        feedback=[],
        trades=[],
        cash_ledger=[],
        component_ledger=[],
        orders=[],
        fills=[],
        realized={},
        physical_realized_pnl=0.0,
        physical_realized_gross_pnl=0.0,
        physical_realized_fees=0.0,
    )
    if initial_positions:
        # Initial positions are valued at the first available prior close and
        # are useful for reconstruction fixtures.  They do not spend cash.
        first_index = all_sessions.index(sessions[0])
        first_session = sessions[0]
        for symbol, value in initial_positions.items():
            if symbol not in symbols:
                raise ValueError("ETF_V3_INITIAL_POSITION_SYMBOL_INVALID")
            quantity = _micro_floor(float(value))
            if quantity:
                price = float(rows_by_session[first_session][symbol]["open"])
                lot = _Lot(quantity, first_index, first_session, price)
                state.physical_lots[symbol].append(lot)
                state.component_lots[("account", symbol)].append(replace(lot))

    equity_rows: list[dict[str, Any]] = []
    signal_rows: list[dict[str, Any]] = []
    peak_equity = float(config.initial_cash)
    previous_session: pd.Timestamp | None = None
    previous_equity = float(config.initial_cash)
    previous_week: tuple[int, int] | None = None
    previous_month: tuple[int, int] | None = None
    pending_decisions: dict[tuple[str, str], SizedOrder] = state.pending_orders
    session_index_by_date = {value: index for index, value in enumerate(all_sessions)}

    for execution_session in sessions:
        execution_index = session_index_by_date[execution_session]
        current_rows = rows_by_session[execution_session]
        prior_session = all_sessions[execution_index - 1] if execution_index > 0 else None
        prior_rows = rows_by_session[prior_session] if prior_session is not None else {}
        feedback_for_context = tuple(state.feedback)
        state.feedback = []

        # Release cash and apply corporate actions before the decision.  This
        # ordering makes payable-date cash spendable on the same session.
        released = 0.0
        for item in list(state.pending_cash):
            if item.settlement_session is not None and item.settlement_session <= execution_session:
                state.cash += item.amount
                released += item.amount
                state.cash_ledger.append({"date": execution_session.isoformat(), "kind": item.kind + "_settled", "symbol": item.symbol, "amount": item.amount, "settlement_date": execution_session.isoformat(), "order_id": item.order_id})
                state.pending_cash.remove(item)

        for symbol in symbols:
            row = current_rows[symbol]
            factor = _safe_float(row.get("split_factor", 1.0), 1.0)
            if factor <= 0:
                raise ValueError("ETF_V3_SPLIT_FACTOR_INVALID")
            if factor != 1.0:
                for lots in (state.physical_lots[symbol],):
                    for lot in lots:
                        lot.quantity_microshares = _micro_floor(_qty(lot.quantity_microshares) * factor)
                        lot.entry_price /= factor if lot.entry_price else 1.0
                for (_component, item), lots in state.component_lots.items():
                    if item != symbol:
                        continue
                    for lot in lots:
                        lot.quantity_microshares = _micro_floor(_qty(lot.quantity_microshares) * factor)
                        lot.entry_price /= factor if lot.entry_price else 1.0
                for key, order in list(pending_decisions.items()):
                    if key[1] == symbol:
                        pending_decisions[key] = order.adjusted_for_split(factor)
                state.cash_ledger.append({"date": execution_session.isoformat(), "kind": "split", "symbol": symbol, "amount": 0.0, "factor": factor})

            dividend = _safe_float(row.get("dividend", 0.0), 0.0)
            if dividend < 0:
                raise ValueError("ETF_V3_DIVIDEND_INVALID")
            held = _all_lots_quantity(state.physical_lots[symbol])
            if dividend > 0 and held:
                payable = row.get("dividend_payable_date")
                if payable is None or pd.isna(payable):
                    raise ValueError("ETF_V3_DIVIDEND_PAYABLE_DATE_MISSING")
                payable_date = _utc(payable)
                if payable_date < execution_session:
                    raise ValueError("ETF_V3_DIVIDEND_PAYABLE_DATE_INVALID")
                amount = _qty(held) * dividend
                state.pending_cash.append(_PendingCash(amount, payable_date, "dividend", symbol))
                state.cash_ledger.append({"date": execution_session.isoformat(), "kind": "dividend_receivable", "symbol": symbol, "amount": amount, "payable_date": payable_date.isoformat()})
                if payable_date <= execution_session:
                    state.cash += amount
                    state.pending_cash.remove(state.pending_cash[-1])
                    released += amount
                    state.cash_ledger.append({"date": execution_session.isoformat(), "kind": "dividend_paid", "symbol": symbol, "amount": amount, "settlement_date": execution_session.isoformat()})

        _ensure_account_component(state, symbols, execution_index, all_sessions)
        pending_total = sum(item.amount for item in state.pending_cash)
        prior_close = {symbol: float(prior_rows[symbol]["close"]) for symbol in symbols} if prior_rows else {}
        if prior_session is None:
            prior_equity = previous_equity
        else:
            prior_equity = float(previous_equity)
        prior_equity = max(0.0, prior_equity)
        # This is a pre-open context.  A fill on today's open has not yet
        # completed today's holding session, so age excludes the execution
        # session.  End-of-day result positions use the inclusive form below.
        physical_positions = {symbol: _position_view("physical", symbol, state.physical_lots[symbol], execution_index, include_current_session=False) for symbol in symbols}
        positions = {
            (component, symbol): _position_view(component, symbol, lots, execution_index, include_current_session=False)
            for (component, symbol), lots in sorted(state.component_lots.items())
        }
        current_week_stamp = execution_session.isocalendar()
        current_week = (int(current_week_stamp.year), int(current_week_stamp.week))
        current_month = (execution_session.year, execution_session.month)
        review_weekly = previous_week is None or current_week != previous_week
        review_monthly = previous_month is None or current_month != previous_month
        split_factors = {symbol: _safe_float(current_rows[symbol].get("split_factor", 1.0), 1.0) for symbol in symbols}
        history: dict[str, pd.DataFrame] = {}
        if config.include_history and prior_session is not None:
            for symbol, item in by_symbol_frame.items():
                history[symbol] = item.loc[item["date"] <= prior_session].copy(deep=False).reset_index(drop=True)
        feature_value = _feature_for(config, execution_session, prior_session)
        if feature_store is not None and prior_session is not None:
            if not hasattr(feature_store, "at"):
                raise TypeError("ETF_V3_FEATURE_STORE_MISSING_AT")
            feature_value = {
                symbol: dict(feature_store.at(symbol, prior_session) or {})
                for symbol in by_symbol_frame
            }
        context = DecisionContext(
            execution_session=execution_session,
            information_cutoff=prior_session,
            prior_close=MappingProxyType(prior_close),
            prior_close_equity=prior_equity,
            settled_cash=float(state.cash),
            unsettled_cash=float(pending_total),
            dividend_receivables=float(sum(item.amount for item in state.pending_cash if item.kind == "dividend")),
            positions=MappingProxyType(positions),
            physical_positions=MappingProxyType(physical_positions),
            features=MappingProxyType({str(k): MappingProxyType(dict(v)) for k, v in feature_value.items()}),
            history=MappingProxyType(history),
            review_weekly=review_weekly,
            review_monthly=review_monthly,
            initial_session=previous_session is None,
            pending_orders=tuple(sorted(pending_decisions.values(), key=lambda item: item.order_id)),
            recent_feedback=feedback_for_context,
            split_factors=MappingProxyType(split_factors),
        )
        intents = _call_strategy(strategy, context) if prior_session is not None or context.initial_session else []
        for intent in intents:
            if intent.symbol not in symbols:
                raise ValueError(f"ETF_V3_INTENT_SYMBOL_UNKNOWN:{intent.symbol}")
        # A newer authorized intent supersedes an older unexecuted order for
        # this component/symbol.  Every cancellation is auditable.
        preserved_pending: set[tuple[str, str]] = set()
        for key in sorted({(intent.component_id, intent.symbol) for intent in intents}):
            old = pending_decisions.pop(key, None)
            if old is not None:
                intent = next(item for item in intents if (item.component_id, item.symbol) == key)
                new_target = _intent_target_quantity(intent, context, split_factors={key[1]: _safe_float(current_rows[key[1]].get("split_factor", 1.0), 1.0)}, config=config)
                if new_target is not None and new_target == old.target_quantity_microshares:
                    pending_decisions[key] = old
                    preserved_pending.add(key)
                else:
                    state.orders.append(_order_row(old, execution_session, "cancelled", "SUPERSEDED_BY_NEW_DECISION"))
                    state.feedback.append(ExecutionFeedback(old.order_id, old.component_id, old.symbol, old.side, old.decision_session, None, old.requested_quantity_microshares, 0, None, "cancelled", "SUPERSEDED_BY_NEW_DECISION"))

        desired_components: dict[tuple[str, str], int] = {
            key: _all_lots_quantity(lots) for key, lots in state.component_lots.items()
        }
        base_components = dict(desired_components)
        touched_keys = {(intent.component_id, intent.symbol) for intent in intents}
        # Normalize absolute target weights globally, so component requests
        # can never create more than 99% target exposure in aggregate.
        weight_intents = [item for item in intents if item.target_weight is not None and item.mode in {SizingMode.ENTER_SLEEVE, SizingMode.REBALANCE}]
        requested_weight_total = sum(max(0.0, float(item.target_weight or 0.0)) for item in weight_intents)
        weight_scale = min(1.0, config.target_investment / requested_weight_total) if requested_weight_total else 1.0
        for ordinal, intent in enumerate(intents):
            key = (intent.component_id, intent.symbol)
            current_quantity = desired_components.get(key, 0)
            if intent.mode in {SizingMode.ENTER_SLEEVE, SizingMode.REBALANCE}:
                if intent.target_weight is not None:
                    if not prior_close or intent.symbol not in prior_close or prior_close[intent.symbol] <= 0:
                        state.orders.append({"order_id": f"{candidate_id}-{execution_session.date()}-{ordinal}", "date": execution_session.isoformat(), "symbol": intent.symbol, "component_id": intent.component_id, "status": "rejected", "reason": "NO_PRIOR_CLOSE"})
                        continue
                    current_quantity = _micro_floor(weight_scale * float(intent.target_weight) * prior_equity / prior_close[intent.symbol] * split_factors[intent.symbol])
                elif intent.target_quantity is not None:
                    current_quantity = _micro_floor(float(intent.target_quantity) * split_factors[intent.symbol])
                else:
                    raise ValueError("ETF_V3_INTENT_TARGET_MISSING")
            elif intent.mode == SizingMode.HOLD_SHARES:
                current_quantity = current_quantity if intent.target_quantity is None else _micro_floor(float(intent.target_quantity) * split_factors[intent.symbol])
            elif intent.mode == SizingMode.EXIT_FULLY:
                current_quantity = 0
            elif intent.mode == SizingMode.REDUCE:
                if intent.target_quantity is not None:
                    current_quantity = min(current_quantity, _micro_floor(float(intent.target_quantity) * split_factors[intent.symbol]))
                elif intent.reduction_fraction is not None:
                    current_quantity = _micro_floor(_qty(current_quantity) * (1.0 - float(intent.reduction_fraction)))
                else:
                    raise ValueError("ETF_V3_REDUCE_TARGET_MISSING")
            desired_components[key] = max(0, current_quantity)

        # Enforce the aggregate target independently of how a strategy chose
        # to express it (weights or absolute quantities).  Valuation uses the
        # previous close and converts current share units back through today's
        # split factor, so a split cannot create artificial target capacity.
        if prior_close and prior_equity > 0 and touched_keys:
            target_notional = float(config.target_investment) * prior_equity
            # Existing/fixed-share ownership is preserved.  Only positive
            # deltas explicitly requested on this decision consume remaining
            # target room; a no-intent decision can never force a sale after
            # appreciation.
            increase_keys = {
                key for key in touched_keys
                if desired_components.get(key, 0) > base_components.get(key, 0)
            }
            fixed_notional = sum(
                _qty(base_components.get(key, 0) if key in increase_keys else desired_components.get(key, 0))
                * prior_close[key[1]]
                / split_factors[key[1]]
                for key in desired_components
                if key[1] in prior_close and split_factors[key[1]] > 0
            )
            increase_notional = sum(
                _qty(desired_components[key] - base_components.get(key, 0)) * prior_close[key[1]] / split_factors[key[1]]
                for key in sorted(increase_keys)
                if key[1] in prior_close and split_factors[key[1]] > 0
            )
            room = max(0.0, target_notional - fixed_notional)
            if increase_notional > room + 1e-9:
                scale = room / increase_notional if increase_notional else 0.0
                for key in sorted(increase_keys):
                    delta = desired_components[key] - base_components.get(key, 0)
                    desired_components[key] = base_components.get(key, 0) + _micro_floor(_qty(delta) * scale)

        # Net component ownership first.  Transfers are bookkeeping only; no
        # market order, cash movement, fee or external turnover is created.
        for symbol in symbols:
            surpluses = [[key, desired_components.get(key, 0) - _all_lots_quantity(state.component_lots.get(key, []))] for key in desired_components if key[1] == symbol and desired_components.get(key, 0) < _all_lots_quantity(state.component_lots.get(key, []))]
            deficits = [[key, desired_components.get(key, 0) - _all_lots_quantity(state.component_lots.get(key, []))] for key in desired_components if key[1] == symbol and desired_components.get(key, 0) > _all_lots_quantity(state.component_lots.get(key, []))]
            for deficit in sorted(deficits, key=lambda item: item[0]):
                for surplus in sorted(surpluses, key=lambda item: item[0]):
                    if deficit[1] <= 0 or surplus[1] >= 0:
                        continue
                    moved = min(deficit[1], -surplus[1])
                    source_lots = state.component_lots.setdefault(surplus[0], [])
                    reference_price = prior_close.get(symbol, float(current_rows[symbol]["open"])) / split_factors[symbol]
                    moved_snapshot: list[tuple[int, float]] = []
                    remaining_snapshot = moved
                    for source_lot in source_lots:
                        taken = min(remaining_snapshot, source_lot.quantity_microshares)
                        moved_snapshot.append((taken, source_lot.entry_price))
                        remaining_snapshot -= taken
                        if remaining_snapshot <= 0:
                            break
                    moved_actual = _consume_lots(source_lots, moved, execution_index, execution_session, reference_price, component_id=surplus[0][0], symbol=symbol, trades=state.trades, internal=True)
                    if moved_actual:
                        target_lots = state.component_lots.setdefault(deficit[0], [])
                        _add_lot(target_lots, moved_actual, execution_index, execution_session, reference_price)
                        deficit[1] -= moved_actual
                        surplus[1] += moved_actual
                        transfer_basis = sum(_qty(quantity) * price for quantity, price in moved_snapshot)
                        state.component_ledger.append({"date": execution_session.isoformat(), "kind": "internal_transfer", "symbol": symbol, "from_component": surplus[0][0], "to_component": deficit[0][0], "quantity": _qty(moved_actual), "reference_price": reference_price, "notional": _qty(moved_actual) * reference_price, "source_basis": transfer_basis, "source_gross_pnl": _qty(moved_actual) * reference_price - transfer_basis})

        _schedule_component_deltas(
            state,
            desired_components,
            symbols,
            prior_close,
            prior_equity,
            split_factors,
            execution_session,
            prior_session,
            execution_index,
            all_sessions,
            config,
            candidate_id,
            calendar_map,
            preserved_pending,
        )
        _execute_due(
            state,
            execution_session,
            execution_index,
            all_sessions,
            rows_by_session,
            calendar_map,
            symbols,
            config,
        )

        close_value = sum(_qty(_all_lots_quantity(state.physical_lots[symbol])) * float(current_rows[symbol]["close"]) for symbol in symbols)
        pending_value = sum(item.amount for item in state.pending_cash)
        total_equity = max(0.0, state.cash + close_value + pending_value)
        peak_equity = max(peak_equity, total_equity)
        unsettled_sale_proceeds = sum(item.amount for item in state.pending_cash if item.kind == "sale")
        dividend_receivables = sum(item.amount for item in state.pending_cash if item.kind == "dividend")
        equity_rows.append({"date": execution_session.isoformat(), "candidate_id": candidate_id, "cash_settled": state.cash, "cash_pending": pending_value, "unsettled_sale_proceeds": unsettled_sale_proceeds, "dividend_receivables": dividend_receivables, "holdings_value": close_value, "equity": total_equity, "invested_exposure": close_value / total_equity if total_equity else 0.0, "released_cash": released})
        for symbol in symbols:
            state.component_ledger.append({"date": execution_session.isoformat(), "kind": "physical_position", "component_id": "physical", "symbol": symbol, "quantity": _qty(_all_lots_quantity(state.physical_lots[symbol])), "quantity_microshares": _all_lots_quantity(state.physical_lots[symbol])})
        for (component, symbol), lots in sorted(state.component_lots.items()):
            state.component_ledger.append({"date": execution_session.isoformat(), "kind": "component_position", "component_id": component, "symbol": symbol, "quantity": _qty(_all_lots_quantity(lots)), "quantity_microshares": _all_lots_quantity(lots)})
        signal_base = {"candidate_id": candidate_id, "decision_session": execution_session.isoformat(), "execution_session": execution_session.isoformat(), "information_cutoff": prior_session.isoformat() if prior_session is not None else None, "review_weekly": review_weekly, "review_monthly": review_monthly, "intent_count": len(intents), "prior_close_equity": prior_equity, "settled_cash": state.cash}
        if intents:
            for intent_index, intent in enumerate(intents):
                if intent.target_weight is not None:
                    sizing = "target_weight"
                elif intent.target_quantity is not None:
                    sizing = "target_quantity"
                elif intent.reduction_fraction is not None:
                    sizing = "reduction_fraction"
                else:
                    sizing = "hold_current"
                if prior_session is None:
                    decision_status = "UNAVAILABLE_NO_INFORMATION_CUTOFF"
                elif intent.mode in {SizingMode.ENTER_SLEEVE, SizingMode.REBALANCE} and intent.symbol not in prior_close:
                    decision_status = "REJECTED_NO_PRIOR_CLOSE"
                else:
                    decision_status = "SUBMITTED"
                signal_rows.append({**signal_base, "intent_index": intent_index, "action": intent.mode.value, "symbol": intent.symbol, "component_id": intent.component_id, "sizing": sizing, "target_weight": intent.target_weight, "target_quantity": intent.target_quantity, "reduction_fraction": intent.reduction_fraction, "reason": intent.reason, "metadata": json.dumps(dict(intent.metadata), sort_keys=True, separators=(",", ":"), default=str), "decision_status": decision_status})
        else:
            signal_rows.append({**signal_base, "intent_index": None, "action": "NO_INTENT", "symbol": None, "component_id": None, "sizing": None, "target_weight": None, "target_quantity": None, "reduction_fraction": None, "reason": "NO_INTENTS", "metadata": "{}", "decision_status": "NO_INTENT"})
        previous_session = execution_session
        previous_equity = total_equity
        previous_week = current_week
        previous_month = current_month

    equity = pd.DataFrame(equity_rows)
    if not equity.empty:
        equity["daily_profit"] = equity["equity"].diff().fillna(equity["equity"].iloc[0] - config.initial_cash)
        equity["daily_return"] = equity["equity"].pct_change().fillna(equity["equity"].iloc[0] / config.initial_cash - 1.0)
        equity["cumulative_profit"] = equity["equity"] - config.initial_cash
        equity["drawdown"] = equity["equity"] / equity["equity"].cummax().clip(lower=float(config.initial_cash)) - 1.0
    fills = _rows_with_schema(state.fills, ("order_id", "date", "symbol", "component_id", "side", "quantity", "price", "notional", "fee", "adverse_cost", "settlement_date", "holding_age_sessions"))
    orders = _rows_with_schema(state.orders, ("order_id", "date", "symbol", "component_id", "side", "quantity", "status", "reason"))
    cash_ledger = _rows_with_schema(state.cash_ledger, ("date", "kind", "symbol", "amount", "settlement_date"))
    component_ledger = _rows_with_schema(state.component_ledger, ("date", "kind", "symbol", "from_component", "to_component", "quantity", "reference_price", "notional"))
    for (component, symbol), lots in sorted(state.component_lots.items()):
        for lot in lots:
            if lot.quantity_microshares:
                mark_price = float(rows_by_session[sessions[-1]][symbol]["close"])
                gross_pnl = (mark_price - lot.entry_price) * _qty(lot.quantity_microshares) if lot.entry_price else None
                trades_row = {"component_id": component, "symbol": symbol, "entry_session": lot.entry_session.isoformat(), "exit_session": None, "entry_date": lot.entry_session.isoformat(), "exit_date": None, "entry_price": lot.entry_price, "exit_price": None, "mark_price": mark_price, "quantity": _qty(lot.quantity_microshares), "holding_sessions": session_index_by_date[sessions[-1]] - lot.entry_index + 1, "gross_pnl": gross_pnl, "fees": 0.0, "internal_transfer": False}
                state.trades.append(trades_row)
    trades = _rows_with_schema(state.trades, ("component_id", "symbol", "entry_session", "exit_session", "entry_date", "exit_date", "entry_price", "exit_price", "mark_price", "quantity", "holding_sessions", "gross_pnl", "fees", "internal_transfer"))
    signals = pd.DataFrame(signal_rows)
    final_positions = {symbol: _position_view("physical", symbol, state.physical_lots[symbol], session_index_by_date[sessions[-1]]) for symbol in symbols}
    physical_unrealized_pnl = 0.0
    for symbol in symbols:
        mark_price = float(rows_by_session[sessions[-1]][symbol]["close"])
        physical_unrealized_pnl += sum((mark_price - lot.entry_price) * _qty(lot.quantity_microshares) for lot in state.physical_lots[symbol] if lot.entry_price)
    dividend_income = sum(float(row.get("amount", 0.0) or 0.0) for row in state.cash_ledger if row.get("kind") == "dividend_receivable")
    physical_account_pnl = state.physical_realized_pnl + physical_unrealized_pnl + dividend_income
    metrics: dict[str, Any] = {
        "candidate_id": candidate_id,
        "starting_equity": float(config.initial_cash),
        "ending_equity": float(equity["equity"].iloc[-1]) if not equity.empty else float(config.initial_cash),
        "net_pnl": float(equity["equity"].iloc[-1] - config.initial_cash) if not equity.empty else 0.0,
        "max_drawdown": float(equity["drawdown"].min()) if not equity.empty else 0.0,
        "execution_delay_sessions": config.execution_delay_sessions,
        "cost_basis_points": config.cost_basis_points,
        "physical_realized_pnl": float(state.physical_realized_pnl),
        "physical_unrealized_pnl": float(physical_unrealized_pnl),
        "dividend_income": float(dividend_income),
        "physical_account_pnl": float(physical_account_pnl),
        "physical_pnl_reconciliation_error": float(physical_account_pnl - (equity["equity"].iloc[-1] - config.initial_cash) if not equity.empty else physical_account_pnl),
    }
    parts = {"candidate_id": candidate_id, "equity": _canonical_frame(equity), "signals": _canonical_frame(signals), "orders": _canonical_frame(orders), "fills": _canonical_frame(fills), "cash_ledger": _canonical_frame(cash_ledger), "component_ledger": _canonical_frame(component_ledger), "trades": _canonical_frame(trades), "metrics": metrics}
    return EngineResult(equity, signals, orders, fills, cash_ledger, component_ledger, trades, MappingProxyType(metrics), _account_hash(parts), MappingProxyType(final_positions))


def _order_row(order: SizedOrder, date_value: pd.Timestamp, status: str, reason: str = "", external_order_id: str | None = None) -> dict[str, Any]:
    return {
        "order_id": order.order_id,
        "external_order_id": external_order_id,
        "date": date_value.isoformat(),
        "symbol": order.symbol,
        "component_id": order.component_id,
        "side": order.side,
        "quantity": order.quantity,
        "requested_quantity": _qty(order.requested_quantity_microshares),
        "target_quantity": _qty(order.target_quantity_microshares),
        "reference_price": order.reference_price,
        "reference_equity": order.reference_equity,
        "information_cutoff": order.information_cutoff.isoformat() if order.information_cutoff is not None else None,
        "decision_session": order.decision_session.isoformat(),
        "execution_session": order.execution_session.isoformat(),
        "expires_session": order.expires_session.isoformat(),
        "sizing_mode": order.mode.value,
        "intent_reason": order.reason,
        "status": status,
        "reason": reason,
    }


def _schedule_component_deltas(
    state: _MutableState,
    desired: Mapping[tuple[str, str], int],
    symbols: Sequence[str],
    prior_close: Mapping[str, float],
    prior_equity: float,
    split_factors: Mapping[str, float],
    decision_session: pd.Timestamp,
    information_cutoff: pd.Timestamp | None,
    execution_index: int,
    all_sessions: Sequence[pd.Timestamp],
    config: EngineConfig,
    candidate_id: str,
    calendar_map: Mapping[pd.Timestamp, pd.Timestamp | None],
    preserved_pending: set[tuple[str, str]] | None = None,
) -> None:
    preserved_pending = preserved_pending or set()
    for ordinal, key in enumerate(sorted(desired)):
        if key in preserved_pending:
            continue
        component, symbol = key
        current = _all_lots_quantity(state.component_lots.get(key, []))
        target = max(0, int(desired[key]))
        delta = target - current
        if delta == 0:
            continue
        side = "buy" if delta > 0 else "sell"
        execution_position = execution_index + config.execution_delay_sessions
        if execution_position >= len(all_sessions):
            execution_session = all_sessions[-1]
            expires = execution_session
            status = "expired_boundary"
        else:
            execution_session = all_sessions[execution_position]
            expires = execution_session
            status = "scheduled"
        ref_price = prior_close.get(symbol, 0.0)
        if ref_price <= 0:
            state.orders.append({"order_id": f"{candidate_id}-{decision_session.date()}-{component}-{symbol}-noprior", "date": decision_session.isoformat(), "symbol": symbol, "component_id": component, "side": side, "quantity": _qty(abs(delta)), "status": "rejected", "reason": "NO_PRIOR_CLOSE"})
            continue
        order = SizedOrder(
            order_id=f"{candidate_id}-{decision_session.date()}-{component}-{symbol}-{ordinal}",
            component_id=component,
            symbol=symbol,
            side=side,
            quantity_microshares=abs(delta),
            requested_quantity_microshares=abs(delta),
            target_quantity_microshares=target,
            reference_price=float(ref_price),
            reference_equity=float(prior_equity),
            decision_session=decision_session,
            information_cutoff=information_cutoff,
            execution_session=execution_session,
            expires_session=expires,
            mode=SizingMode.REBALANCE,
            reason="",
            status=status,
        )
        state.orders.append(_order_row(order, decision_session, status))
        if status == "scheduled":
            state.pending_orders[(component, symbol)] = order
        else:
            state.feedback.append(ExecutionFeedback(order.order_id, component, symbol, side, decision_session, None, abs(delta), 0, None, "expired_boundary", "EXECUTION_AFTER_ACCOUNT_BOUNDARY"))
    

def _execute_due(
    state: _MutableState,
    execution_session: pd.Timestamp,
    execution_index: int,
    all_sessions: Sequence[pd.Timestamp],
    rows_by_session: Mapping[pd.Timestamp, Mapping[str, Mapping[str, Any]]],
    calendar_map: Mapping[pd.Timestamp, pd.Timestamp | None],
    symbols: Sequence[str],
    config: EngineConfig,
) -> None:
    """Execute frozen orders due on this session, reductions before buys."""

    due = [order for order in state.pending_orders.values() if order.status == "scheduled" and order.execution_session <= execution_session]
    if not due:
        return
    # Orders cannot be both due and beyond their expiry with the current
    # scheduler, but retain an explicit boundary record for reconstruction.
    due.sort(key=lambda order: (0 if order.side == "sell" else 1, order.order_id))
    current_rows = rows_by_session[execution_session]
    sell_orders = [item for item in due if item.side == "sell"]
    buy_orders = [item for item in due if item.side == "buy"]
    for symbol in sorted({item.symbol for item in sell_orders}):
        group = [item for item in sell_orders if item.symbol == symbol]
        open_price = float(current_rows[symbol]["open"])
        fill_price = open_price * (1.0 - config.cost_basis_points / 10_000.0)
        requested_effective = [
            (item, min(item.quantity_microshares, _all_lots_quantity(state.component_lots.get((item.component_id, symbol), []))))
            for item in group
        ]
        effective = [(item, quantity) for item, quantity in requested_effective if quantity > 0]
        no_ownership = [(item, quantity) for item, quantity in requested_effective if quantity <= 0]
        total_quantity = sum(quantity for _, quantity in effective)
        total_notional = _qty(total_quantity) * fill_price
        full_exit = all(item.target_quantity_microshares == 0 for item, _ in effective) if effective else False
        external_order_id = f"{execution_session.date()}-sell-{symbol}"
        if total_quantity <= 0:
            for item in group:
                state.orders.append(_order_row(item, execution_session, "rejected", "NO_OWNERSHIP", external_order_id))
                state.feedback.append(ExecutionFeedback(item.order_id, item.component_id, item.symbol, item.side, item.decision_session, execution_session, item.requested_quantity_microshares, 0, None, "rejected", "NO_OWNERSHIP"))
                state.pending_orders.pop((item.component_id, item.symbol), None)
            continue
        for item, _ in no_ownership:
            state.orders.append(_order_row(item, execution_session, "rejected", "NO_OWNERSHIP", external_order_id))
            state.feedback.append(ExecutionFeedback(item.order_id, item.component_id, item.symbol, item.side, item.decision_session, execution_session, item.requested_quantity_microshares, 0, None, "rejected", "NO_OWNERSHIP"))
            state.pending_orders.pop((item.component_id, item.symbol), None)
        if not full_exit and total_notional < config.minimum_order_notional:
            for item, _ in effective:
                state.orders.append(_order_row(item, execution_session, "expired_minimum_notional", "MINIMUM_ORDER_NOTIONAL", external_order_id))
                state.feedback.append(ExecutionFeedback(item.order_id, item.component_id, item.symbol, item.side, item.decision_session, execution_session, item.requested_quantity_microshares, 0, None, "expired_minimum_notional", "MINIMUM_ORDER_NOTIONAL"))
                state.pending_orders.pop((item.component_id, item.symbol), None)
            continue
        effective_total = sum(quantity for _, quantity in effective)
        for item, quantity in effective:
            fee_share = float(config.sell_fee) * quantity / effective_total if effective_total else 0.0
            _execute_one(
                state,
                item,
                execution_session,
                execution_index,
                current_rows,
                calendar_map,
                config,
                is_buy=False,
                fee_override=fee_share,
                minimum_checked=True,
                external_order_id=external_order_id,
                quantity_override=quantity,
            )
    available = max(0.0, state.cash)
    buy_groups: list[tuple[str, list[SizedOrder], int, float, float]] = []
    for symbol in sorted({item.symbol for item in buy_orders}):
        group = [item for item in buy_orders if item.symbol == symbol]
        requested = sum(item.quantity_microshares for item in group)
        open_price = float(current_rows[symbol]["open"])
        fill_price = open_price * (1.0 + config.cost_basis_points / 10_000.0)
        notional = _qty(requested) * fill_price
        buy_groups.append((symbol, group, requested, fill_price, notional))
    valid_groups = []
    for symbol, group, requested, fill_price, notional in buy_groups:
        external_order_id = f"{execution_session.date()}-buy-{symbol}"
        if notional < config.minimum_order_notional:
            for item in group:
                state.orders.append(_order_row(item, execution_session, "expired_minimum_notional", "MINIMUM_ORDER_NOTIONAL", external_order_id))
                state.feedback.append(ExecutionFeedback(item.order_id, item.component_id, item.symbol, item.side, item.decision_session, execution_session, item.requested_quantity_microshares, 0, None, "expired_minimum_notional", "MINIMUM_ORDER_NOTIONAL"))
                state.pending_orders.pop((item.component_id, item.symbol), None)
            continue
        valid_groups.append((symbol, group, requested, fill_price, notional))
    total_required = sum(item[4] for item in valid_groups)
    scale = min(1.0, available / total_required) if total_required else 0.0
    for symbol, group, requested, fill_price, _ in valid_groups:
        external_order_id = f"{execution_session.date()}-buy-{symbol}"
        quantity_total = min(requested, _micro_floor(_qty(requested) * scale))
        quantity_total = min(quantity_total, _micro_floor(state.cash / fill_price))
        if quantity_total <= 0:
            for item in group:
                state.orders.append(_order_row(item, execution_session, "rejected", "INSUFFICIENT_SETTLED_CASH", external_order_id))
                state.feedback.append(ExecutionFeedback(item.order_id, item.component_id, item.symbol, item.side, item.decision_session, execution_session, item.requested_quantity_microshares, 0, None, "rejected", "INSUFFICIENT_SETTLED_CASH"))
                state.pending_orders.pop((item.component_id, item.symbol), None)
            continue
        remaining = quantity_total
        allocations: list[tuple[SizedOrder, int]] = []
        for item in group[:-1]:
            allocation = min(item.quantity_microshares, _micro_floor(_qty(quantity_total) * item.quantity_microshares / requested))
            allocations.append((item, allocation))
            remaining -= allocation
        if group:
            allocations.append((group[-1], min(group[-1].quantity_microshares, remaining)))
        for order, quantity in allocations:
            open_price = float(current_rows[order.symbol]["open"])
            notional = _qty(quantity) * fill_price
            if quantity <= 0 or notional < config.minimum_order_notional and len(group) == 1:
                state.orders.append(_order_row(order, execution_session, "rejected", "INSUFFICIENT_SETTLED_CASH", external_order_id))
                state.feedback.append(ExecutionFeedback(order.order_id, order.component_id, order.symbol, order.side, order.decision_session, execution_session, order.requested_quantity_microshares, 0, None, "rejected", "INSUFFICIENT_SETTLED_CASH"))
                state.pending_orders.pop((order.component_id, order.symbol), None)
                continue
            state.cash -= notional
            _allocate_buy(state, order.component_id, order.symbol, quantity, execution_index, execution_session, fill_price)
            status = "filled" if quantity >= order.quantity_microshares else "filled_partial"
            state.orders.append(_order_row(order, execution_session, status, external_order_id=external_order_id))
            state.fills.append({"order_id": order.order_id, "external_order_id": external_order_id, "date": execution_session.isoformat(), "symbol": order.symbol, "component_id": order.component_id, "side": "buy", "quantity": _qty(quantity), "price": fill_price, "notional": notional, "fee": 0.0, "adverse_cost": _qty(quantity) * (fill_price - open_price), "settlement_date": execution_session.isoformat(), "holding_age_sessions": 1})
            state.cash_ledger.append({"date": execution_session.isoformat(), "kind": "buy", "symbol": order.symbol, "amount": -notional, "order_id": order.order_id, "external_order_id": external_order_id})
            after = _all_lots_quantity(state.component_lots.get((order.component_id, order.symbol), []))
            state.feedback.append(ExecutionFeedback(order.order_id, order.component_id, order.symbol, order.side, order.decision_session, execution_session, order.requested_quantity_microshares, quantity, fill_price, status, settlement_session=execution_session, ownership_after_microshares=after, holding_age_sessions=1))
            state.pending_orders.pop((order.component_id, order.symbol), None)


def _allocate_buy(state: _MutableState, component: str, symbol: str, quantity: int, index: int, session: pd.Timestamp, price: float) -> None:
    _add_lot(state.physical_lots.setdefault(symbol, []), quantity, index, session, price)
    _add_lot(state.component_lots.setdefault((component, symbol), []), quantity, index, session, price)


def _execute_one(
    state: _MutableState,
    order: SizedOrder,
    execution_session: pd.Timestamp,
    execution_index: int,
    current_rows: Mapping[str, Mapping[str, Any]],
    calendar_map: Mapping[pd.Timestamp, pd.Timestamp | None],
    config: EngineConfig,
    *,
    is_buy: bool,
    fee_override: float | None = None,
    minimum_checked: bool = False,
    external_order_id: str | None = None,
    quantity_override: int | None = None,
) -> None:
    open_price = float(current_rows[order.symbol]["open"])
    fill_price = open_price * (1.0 - config.cost_basis_points / 10_000.0)
    component_lots = state.component_lots.setdefault((order.component_id, order.symbol), [])
    physical_lots = state.physical_lots.setdefault(order.symbol, [])
    requested = order.quantity_microshares
    owned_component = _all_lots_quantity(component_lots)
    owned_physical = _all_lots_quantity(physical_lots)
    quantity = min(requested, owned_component, owned_physical)
    if quantity_override is not None:
        quantity = min(quantity, int(quantity_override))
    notional = _qty(quantity) * fill_price
    full_exit = order.target_quantity_microshares == 0
    if quantity <= 0:
        state.orders.append(_order_row(order, execution_session, "rejected", "NO_OWNERSHIP"))
        state.feedback.append(ExecutionFeedback(order.order_id, order.component_id, order.symbol, order.side, order.decision_session, execution_session, requested, 0, None, "rejected", "NO_OWNERSHIP"))
        state.pending_orders.pop((order.component_id, order.symbol), None)
        return
    if not minimum_checked and not full_exit and notional < config.minimum_order_notional:
        state.orders.append(_order_row(order, execution_session, "expired_minimum_notional", "MINIMUM_ORDER_NOTIONAL", external_order_id))
        state.feedback.append(ExecutionFeedback(order.order_id, order.component_id, order.symbol, order.side, order.decision_session, execution_session, requested, 0, None, "expired_minimum_notional", "MINIMUM_ORDER_NOTIONAL"))
        state.pending_orders.pop((order.component_id, order.symbol), None)
        return
    fee = float(config.sell_fee if fee_override is None else fee_override)
    # Check the fixed-fee edge before consuming any FIFO lots.  A tiny
    # full-exit can be smaller than the fixed sell fee; either fund the
    # shortfall from settled cash or reject without mutating ownership.
    fee_shortfall = max(0.0, fee - notional)
    if fee_shortfall > state.cash + 1e-12:
        state.orders.append(_order_row(order, execution_session, "rejected", "INSUFFICIENT_CASH_FOR_SELL_FEE", external_order_id))
        state.feedback.append(ExecutionFeedback(order.order_id, order.component_id, order.symbol, order.side, order.decision_session, execution_session, requested, 0, None, "rejected", "INSUFFICIENT_CASH_FOR_SELL_FEE"))
        state.pending_orders.pop((order.component_id, order.symbol), None)
        return
    if fee_shortfall > 0:
        state.cash -= fee_shortfall
        state.cash_ledger.append({"date": execution_session.isoformat(), "kind": "fee", "symbol": order.symbol, "amount": -fee_shortfall, "order_id": order.order_id, "external_order_id": external_order_id})
    before = owned_component
    sell_snapshot: list[tuple[int, int, pd.Timestamp, float]] = []
    remaining_for_snapshot = quantity
    for lot in component_lots:
        taken = min(remaining_for_snapshot, lot.quantity_microshares)
        sell_snapshot.append((taken, lot.entry_index, lot.entry_session, lot.entry_price))
        remaining_for_snapshot -= taken
        if remaining_for_snapshot <= 0:
            break
    # Component sells are mirrored into physical lots FIFO.  The engine only
    # reaches this path after component internal transfers have been applied.
    consumed_component = _consume_lots(component_lots, quantity, execution_index, execution_session, fill_price, component_id=order.component_id, symbol=order.symbol, trades=state.trades)
    physical_snapshot: list[tuple[int, float]] = []
    remaining_physical = consumed_component
    for lot in physical_lots:
        taken = min(remaining_physical, lot.quantity_microshares)
        physical_snapshot.append((taken, lot.entry_price))
        remaining_physical -= taken
        if remaining_physical <= 0:
            break
    _consume_lots(physical_lots, consumed_component, execution_index, execution_session, fill_price, component_id="physical", symbol=order.symbol, trades=[], internal=True)
    physical_basis = sum(_qty(item[0]) * item[1] for item in physical_snapshot)
    # When the fee was larger than gross proceeds, the shortfall was already
    # charged against settled cash, so the pending sale amount is gross and
    # remains nonnegative.  Otherwise the broker nets the fee from proceeds.
    proceeds = notional if fee_shortfall > 0 else notional - fee
    settlement = calendar_map.get(execution_session)
    state.pending_cash.append(_PendingCash(proceeds, settlement, "sale", order.symbol, order.order_id))
    settlement_label = settlement.isoformat() if settlement is not None else None
    status = "filled" if consumed_component >= requested else "filled_partial"
    state.orders.append(_order_row(order, execution_session, status, external_order_id=external_order_id))
    state.fills.append({"order_id": order.order_id, "external_order_id": external_order_id, "date": execution_session.isoformat(), "symbol": order.symbol, "component_id": order.component_id, "side": "sell", "quantity": _qty(consumed_component), "price": fill_price, "notional": notional, "fee": fee, "adverse_cost": _qty(consumed_component) * (open_price - fill_price), "settlement_date": settlement_label, "holding_age_sessions": execution_index - min((item[1] for item in sell_snapshot), default=execution_index) + 1})
    state.cash_ledger.append({"date": execution_session.isoformat(), "kind": "sale_pending", "symbol": order.symbol, "amount": proceeds, "settlement_date": settlement_label, "order_id": order.order_id, "external_order_id": external_order_id})
    after = _all_lots_quantity(component_lots)
    basis = sum(_qty(item[0]) * item[3] for item in sell_snapshot)
    first_entry_index = min((item[1] for item in sell_snapshot), default=execution_index)
    first_entry_session = min((item[2] for item in sell_snapshot), default=execution_session)
    realized_key = (order.component_id, order.symbol)
    carried = state.realized.setdefault(realized_key, {"quantity_microshares": 0, "basis": 0.0, "proceeds": 0.0, "fees": 0.0, "entry_index": first_entry_index, "entry_session": first_entry_session})
    carried["quantity_microshares"] += consumed_component
    carried["basis"] += basis
    carried["proceeds"] += notional
    carried["fees"] += fee
    carried["entry_index"] = min(int(carried["entry_index"]), first_entry_index)
    carried["entry_session"] = min(carried["entry_session"], first_entry_session)
    state.component_ledger.append({"date": execution_session.isoformat(), "kind": "realized_partial", "symbol": order.symbol, "component_id": order.component_id, "quantity": _qty(consumed_component), "gross_pnl": notional - basis, "fees": fee})
    if after == 0:
        state.trades.append({"component_id": order.component_id, "symbol": order.symbol, "entry_session": carried["entry_session"].isoformat(), "exit_session": execution_session.isoformat(), "entry_date": carried["entry_session"].isoformat(), "exit_date": execution_session.isoformat(), "entry_price": carried["basis"] / _qty(carried["quantity_microshares"]) if carried["quantity_microshares"] else None, "exit_price": carried["proceeds"] / _qty(carried["quantity_microshares"]) if carried["quantity_microshares"] else None, "quantity": _qty(carried["quantity_microshares"]), "holding_sessions": execution_index - int(carried["entry_index"]) + 1, "gross_pnl": carried["proceeds"] - carried["basis"], "fees": carried["fees"], "internal_transfer": False})
        state.realized.pop(realized_key, None)
    state.feedback.append(ExecutionFeedback(order.order_id, order.component_id, order.symbol, order.side, order.decision_session, execution_session, requested, consumed_component, fill_price, status, settlement_session=settlement, ownership_before_microshares=before, ownership_after_microshares=after, holding_age_sessions=execution_index - first_entry_index + 1, fully_liquidated=after == 0))
    state.physical_realized_gross_pnl += notional - physical_basis
    state.physical_realized_fees += fee
    state.physical_realized_pnl += notional - physical_basis - fee
    state.pending_orders.pop((order.component_id, order.symbol), None)


def reconstruct_account_hash(result: EngineResult) -> str:
    """Recompute the result hash from public ledgers for independent checks."""

    parts = {"candidate_id": result.metrics.get("candidate_id"), "equity": _canonical_frame(result.equity), "signals": _canonical_frame(result.signals), "orders": _canonical_frame(result.orders), "fills": _canonical_frame(result.fills), "cash_ledger": _canonical_frame(result.cash_ledger), "component_ledger": _canonical_frame(result.component_ledger), "trades": _canonical_frame(result.trades), "metrics": dict(result.metrics)}
    return _account_hash(parts)


__all__ = [
    "DecisionContext",
    "EngineConfig",
    "EngineResult",
    "ExecutionFeedback",
    "MICROSHARES",
    "OrderIntent",
    "PositionView",
    "SizingMode",
    "SizedOrder",
    "StrategyV3",
    "reconstruct_account_hash",
    "run_engine_v3",
]
