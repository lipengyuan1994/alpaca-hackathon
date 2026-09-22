"""Causal leveraged ETF strategies for the repaired v3 cash engine.

The strategy objects in this module are deliberately small state machines.  A
decision only sees ``DecisionContext.features`` through the previous completed
session, while ownership, holding age, cooldowns, and split-safe quantities
come from the v3 engine.  No evaluator in this module sizes from a current
open or a future bar.

``B`` is TQQQ or SPXL, ``S`` is SOXL, ``U`` is QQQ or SPY, and ``V`` is SOXX.
L01--L10 are the frozen strategies from the leveraged-pair study and L11--L16
are the additional broad-leadership hypotheses in
``docs/research/ETF_LEVERAGED_NEXT_ROUND.md``.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from .engine_v3 import DecisionContext, OrderIntent, SizingMode

TRACK_B_STRATEGY_IDS = tuple(f"L{i:02d}" for i in range(1, 17))
_BROAD_PROXY = {"TQQQ": "QQQ", "SPXL": "SPY"}
_EPS = 1e-12


def _finite(value: Any) -> bool:
    try:
        return value is not None and not pd.isna(value) and bool(np.isfinite(float(value)))
    except (TypeError, ValueError):
        return False


def _number(value: Any, default: float = np.nan) -> float:
    return float(value) if _finite(value) else default


def _stamp(value: Any) -> pd.Timestamp:
    result = pd.Timestamp(value)
    if result.tzinfo is None:
        result = result.tz_localize("UTC")
    else:
        result = result.tz_convert("UTC")
    return result.normalize()


@dataclass
class _StrategyState:
    selected: str | None = None
    armed: dict[str, int] = field(default_factory=dict)
    cooldown_until: dict[str, int] = field(default_factory=dict)
    shock_exit_pending: set[str] = field(default_factory=set)
    highest_close: dict[str, float] = field(default_factory=dict)
    base_targets: dict[str, float] = field(default_factory=dict)
    current_targets: dict[str, float] = field(default_factory=dict)
    reduced: set[str] = field(default_factory=set)
    reduced_targets: dict[str, float] = field(default_factory=dict)
    reentry_blocked: set[str] = field(default_factory=set)


class LeveragedStrategyV3:
    """One deterministic L01--L16 strategy for one leveraged pair.

    ``component_id`` is ``account`` for ordinary candidates.  L10 creates two
    child instances with component ids ``L02`` and ``L06`` so that the engine
    can keep virtual component ownership while executing one shared cash
    ledger.
    """

    def __init__(
        self,
        strategy_id: str,
        broad_symbol: str,
        semiconductor: str = "SOXL",
        proxies: tuple[str, str] | None = None,
        *,
        store: Any | None = None,
        data: Any | None = None,
        config: Any | None = None,
        start: Any | None = None,
        end: Any | None = None,
        variant: str = "primary",
        component_id: str = "account",
    ) -> None:
        self.strategy_id = str(strategy_id).upper()
        if self.strategy_id not in TRACK_B_STRATEGY_IDS:
            raise ValueError(f"ETF_V3_LEVERAGED_STRATEGY_UNKNOWN:{self.strategy_id}")
        self.broad_symbol = str(broad_symbol).upper()
        if self.broad_symbol not in _BROAD_PROXY:
            raise ValueError("ETF_V3_LEVERAGED_BROAD_SYMBOL_INVALID")
        self.semiconductor = str(semiconductor).upper()
        self.proxies = tuple(str(x).upper() for x in (proxies or (_BROAD_PROXY[self.broad_symbol], "SOXX")))
        if len(self.proxies) != 2 or self.proxies[0] != _BROAD_PROXY[self.broad_symbol] or not self.proxies[1]:
            raise ValueError("ETF_V3_LEVERAGED_PROXY_CONFIGURATION_INVALID")
        self.broad_proxy, self.sector_proxy = self.proxies
        self.store = store
        self.data = data
        self.config = config
        self.start = start
        self.end = end
        self.variant = str(variant)
        self.component_id = str(component_id)
        self.state = _StrategyState()
        self._last_execution: pd.Timestamp | None = None
        self._session_dates = self._make_session_dates(data)
        self._session_index = {d: i for i, d in enumerate(self._session_dates)}
        self._pair_frame: pd.DataFrame | None = None
        self._actual_pair_frame: pd.DataFrame | None = None
        if store is not None and hasattr(store, "pair"):
            try:
                self._pair_frame = store.pair(self.broad_proxy, self.sector_proxy)
                self._actual_pair_frame = store.pair(self.broad_symbol, self.semiconductor)
            except (KeyError, ValueError, TypeError):
                self._pair_frame = None
                self._actual_pair_frame = None
        self._trend_child: LeveragedStrategyV3 | None = None
        self._pullback_child: LeveragedStrategyV3 | None = None
        if self.strategy_id == "L10" and self.component_id == "account":
            self._trend_child = LeveragedStrategyV3(
                "L02", self.broad_symbol, self.semiconductor, self.proxies,
                store=store, data=data, config=config, start=start, end=end,
                variant="primary", component_id="L02",
            )
            self._pullback_child = LeveragedStrategyV3(
                "L06", self.broad_symbol, self.semiconductor, self.proxies,
                store=store, data=data, config=config, start=start, end=end,
                variant="primary", component_id="L06",
            )

    @staticmethod
    def _make_session_dates(data: Any | None) -> list[pd.Timestamp]:
        if data is not None and hasattr(data, "calendar"):
            frame = data.calendar
            if "date" in frame:
                return sorted({_stamp(x) for x in frame["date"].tolist()})
        return []

    @property
    def B(self) -> str:
        return self.broad_symbol

    @property
    def S(self) -> str:
        return self.semiconductor

    @property
    def U(self) -> str:
        return self.broad_proxy

    @property
    def V(self) -> str:
        return self.sector_proxy

    @property
    def symbols(self) -> tuple[str, str]:
        return self.broad_symbol, self.semiconductor

    @property
    def signal_symbols(self) -> tuple[str, str]:
        return self.proxies

    @property
    def all_symbols(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.symbols, *self.signal_symbols)))

    def _index(self, context: DecisionContext) -> int:
        stamp = _stamp(context.execution_session)
        if stamp in self._session_index:
            return self._session_index[stamp]
        if self._last_execution is None:
            self._last_execution = stamp
            return 0
        if stamp > self._last_execution:
            # Fixture contexts need a usable monotone index even without a
            # calendar.  Real runs always use the immutable calendar above.
            self._last_execution = stamp
        return len(self._session_index)

    def _feature(self, context: DecisionContext, symbol: str, name: str) -> float:
        return _number(context.features.get(symbol, {}).get(name))

    def _held(self, context: DecisionContext, symbol: str, component: str | None = None) -> bool:
        component = self.component_id if component is None else component
        item = context.positions.get((component, symbol))
        return bool(item is not None and item.quantity_microshares > 0)

    def _position(self, context: DecisionContext, symbol: str, component: str | None = None):
        component = self.component_id if component is None else component
        return context.positions.get((component, symbol))

    def _pending(self, context: DecisionContext, symbol: str, component: str | None = None) -> tuple[Any, ...]:
        component = self.component_id if component is None else component
        return tuple(item for item in context.pending_orders if item.component_id == component and item.symbol == symbol)

    def _in_position(self, context: DecisionContext, symbol: str) -> bool:
        return self._held(context, symbol) or bool(self._pending(context, symbol))

    def _cutoff_index(self, context: DecisionContext) -> int:
        cutoff = context.information_cutoff
        if cutoff is None:
            return -1
        stamp = _stamp(cutoff)
        if stamp in self._session_index:
            return self._session_index[stamp]
        return bisect_right(self._session_dates, stamp) - 1 if self._session_dates else 0

    def _pair_value(self, context: DecisionContext, name: str) -> float:
        cutoff = context.information_cutoff
        if cutoff is not None and self._pair_frame is not None:
            stamp = _stamp(cutoff)
            try:
                row = self._pair_frame.loc[stamp]
                if isinstance(row, pd.DataFrame):
                    row = row.iloc[-1]
                value = _number(row.get(name))
                if _finite(value):
                    return value
            except (KeyError, IndexError, TypeError):
                pass
        # Unit and hidden-fixture fallback.  history is explicitly truncated
        # by the v3 engine at the information cutoff.
        u = context.history.get(self.broad_proxy)
        v = context.history.get(self.sector_proxy)
        if u is None or v is None or u.empty or v.empty or name not in {"ratio", "ratio_sma20"}:
            cu = self._feature(context, self.broad_proxy, "close")
            cv = self._feature(context, self.sector_proxy, "close")
            return cv / cu if name == "ratio" and _finite(cu) and _finite(cv) and cu else np.nan
        merged = pd.DataFrame({"u": u.set_index("date")["close"], "v": v.set_index("date")["close"]}).dropna()
        ratio = merged.v / merged.u
        if ratio.empty:
            return np.nan
        return _number(ratio.iloc[-1] if name == "ratio" else ratio.rolling(20, min_periods=20).mean().iloc[-1])

    def _actual_return_frame(self, context: DecisionContext) -> pd.DataFrame | None:
        if context.information_cutoff is not None and self._actual_pair_frame is not None:
            stamp = _stamp(context.information_cutoff)
            frame = self._actual_pair_frame.loc[:stamp]
            if not frame.empty:
                # FeatureStore.pair uses generic ``a``/``b`` columns.  Keep
                # the strategy helper's stable ``b``/``s`` names explicit so
                # covariance code cannot confuse the broad proxy with SOXL.
                return frame.rename(columns={"a": "b", "b": "s"})
        b = context.history.get(self.broad_symbol)
        s = context.history.get(self.semiconductor)
        if b is None or s is None:
            return None
        left = b.set_index("date")["close"].pct_change(fill_method=None).rename("b")
        right = s.set_index("date")["close"].pct_change(fill_method=None).rename("s")
        return pd.concat([left, right], axis=1).dropna()

    def _volatility(self, context: DecisionContext, symbol: str, period: int = 60) -> float:
        value = self._feature(context, symbol, f"vol{period}")
        if _finite(value) and value > 0:
            return value
        history = context.history.get(symbol)
        if history is None:
            return np.nan
        returns = pd.to_numeric(history["close"], errors="coerce").pct_change(fill_method=None).dropna().tail(period)
        if len(returns) < 2:
            return np.nan
        result = float(returns.std(ddof=1) * np.sqrt(252.0))
        return result if np.isfinite(result) else np.nan

    def _portfolio_volatility(self, context: DecisionContext, weights: Mapping[str, float]) -> float:
        active = {symbol: float(weight) for symbol, weight in weights.items() if weight > 0}
        if not active:
            return np.nan
        frame = self._actual_return_frame(context)
        if frame is None or len(frame) < 2:
            return np.nan
        names = ["b" if symbol == self.broad_symbol else "s" for symbol in active]
        if not set(names).issubset(frame.columns):
            return np.nan
        joined = frame[names].dropna().tail(60)
        if len(joined) < 2:
            return np.nan
        vector = np.asarray([active[symbol] for symbol in active], dtype=float)
        covariance = joined.to_numpy(dtype=float).T @ joined.to_numpy(dtype=float)
        covariance = (covariance - len(joined) * joined.mean().to_numpy()[:, None] * joined.mean().to_numpy()[None, :]) / (len(joined) - 1)
        variance = float(vector @ covariance @ vector * 252.0)
        return float(np.sqrt(max(variance, 0.0))) if np.isfinite(variance) else np.nan

    def _downside_volatility(self, context: DecisionContext, weights: Mapping[str, float]) -> float:
        active = {symbol: float(weight) for symbol, weight in weights.items() if weight > 0}
        frame = self._actual_return_frame(context)
        if not active or frame is None or len(frame) < 2:
            return np.nan
        names = ["b" if symbol == self.broad_symbol else "s" for symbol in active]
        joined = frame[names].dropna().tail(60)
        if len(joined) < 2:
            return np.nan
        vec = np.asarray([active[symbol] for symbol in active], dtype=float)
        portfolio = joined.to_numpy(dtype=float) @ vec
        downside = np.minimum(portfolio, 0.0)
        result = float(np.sqrt(252.0 * np.mean(downside * downside)))
        return result if np.isfinite(result) else np.nan

    def _score(self, context: DecisionContext, fund: str, horizon: int = 126) -> float:
        proxy = self.broad_proxy if fund == self.broad_symbol else self.sector_proxy
        r63 = self._feature(context, proxy, "r63")
        long_return = self._feature(context, proxy, f"r{horizon}")
        return 0.5 * r63 + 0.5 * long_return if _finite(r63) and _finite(long_return) else np.nan

    def _eligible(self, context: DecisionContext, fund: str, *, horizon: int = 126, trend_period: int = 200) -> bool:
        proxy = self.broad_proxy if fund == self.broad_symbol else self.sector_proxy
        close = self._feature(context, proxy, "close")
        trend = self._feature(context, proxy, f"sma{trend_period}")
        momentum = self._feature(context, proxy, f"r{horizon}")
        return _finite(close) and _finite(trend) and _finite(momentum) and close > trend and momentum > 0

    def _review(self, context: DecisionContext, weekly: bool = True) -> bool:
        return bool(context.review_weekly if weekly else context.review_monthly)

    def _sync_feedback(self, context: DecisionContext) -> None:
        """Advance fill-driven state from the prior execution session."""
        current_index = self._index(context)
        for feedback in context.recent_feedback:
            if feedback.component_id != self.component_id:
                continue
            if feedback.status.startswith("filled") and feedback.side == "buy" and feedback.filled_quantity_microshares:
                close = self._feature(context, feedback.symbol, "close")
                if _finite(close):
                    self.state.highest_close[feedback.symbol] = max(self.state.highest_close.get(feedback.symbol, close), close)
                # A contraction setup is consumed only by an actual fill.  A
                # delayed or cancelled order therefore cannot erase the
                # setup before its ten-session expiry.
                if self.strategy_id == "L07":
                    self.state.armed.pop(feedback.symbol, None)
            if feedback.side == "sell" and feedback.fully_liquidated:
                self.state.highest_close.pop(feedback.symbol, None)
                if feedback.symbol in self.state.shock_exit_pending:
                    self.state.cooldown_until[feedback.symbol] = current_index + self._cooldown_length()
                    self.state.shock_exit_pending.discard(feedback.symbol)
        for fund in self.symbols:
            if self._held(context, fund):
                close = self._feature(context, fund, "close")
                if _finite(close):
                    self.state.highest_close[fund] = max(self.state.highest_close.get(fund, close), close)

    def _cooldown_length(self) -> int:
        if self.strategy_id == "L06":
            return 2
        if self.strategy_id == "L09":
            return int(self.variant.removeprefix("cooldown_") or 10) if self.variant.startswith("cooldown_") else 10
        return 0

    def _cooldown_ready(self, context: DecisionContext, fund: str) -> bool:
        return self._index(context) > self.state.cooldown_until.get(fund, -1)

    def _intent(self, symbol: str, mode: SizingMode, *, weight: float | None = None, quantity: float | None = None, reason: str = "", component: str | None = None) -> OrderIntent:
        return OrderIntent(symbol, mode, component_id=component or self.component_id, target_weight=weight, target_quantity=quantity, reason=reason)

    def _exit(self, symbol: str, reason: str) -> OrderIntent:
        return self._intent(symbol, SizingMode.EXIT_FULLY, reason=reason)

    def _target_intents(self, context: DecisionContext, targets: Mapping[str, float], reason: str, *, review: bool = True, suppress: set[str] | None = None) -> list[OrderIntent]:
        suppress = suppress or set()
        output: list[OrderIntent] = []
        for fund in self.symbols:
            if fund in suppress:
                continue
            target = max(0.0, float(targets.get(fund, 0.0)))
            held = self._held(context, fund)
            pending = self._pending(context, fund)
            if target > 0:
                if review or not held:
                    mode = SizingMode.REBALANCE if held else SizingMode.ENTER_SLEEVE
                    if not pending or mode == SizingMode.REBALANCE:
                        output.append(self._intent(fund, mode, weight=target, reason=reason))
            elif held or any(item.side == "buy" for item in pending):
                output.append(self._exit(fund, reason))
        return output

    def _daily_proxy_exits(self, context: DecisionContext, funds: Iterable[str] | None = None, period: int = 200, reason: str = "PROXY_TREND_EXIT") -> list[OrderIntent]:
        result = []
        for fund in funds or self.symbols:
            proxy = self.broad_proxy if fund == self.broad_symbol else self.sector_proxy
            close = self._feature(context, proxy, "close")
            trend = self._feature(context, proxy, f"sma{period}")
            if self._in_position(context, fund) and _finite(close) and _finite(trend) and close < trend:
                result.append(self._exit(fund, reason))
                if self.state.selected == fund:
                    self.state.selected = None
        return result

    def decide(self, context: DecisionContext) -> Iterable[OrderIntent]:
        self._sync_feedback(context)
        if context.information_cutoff is None:
            return []
        dispatch = getattr(self, f"_decide_{self.strategy_id.lower()}")
        return dispatch(context)

    # ----- L01 and L02 -------------------------------------------------
    def _rotation_targets(self, context: DecisionContext, *, horizon: int, weekly: bool, vol_target: float | None, trend_period: int = 200) -> tuple[dict[str, float], str | None]:
        targets: dict[str, float] = {}
        candidates = [fund for fund in self.symbols if self._eligible(context, fund, horizon=horizon, trend_period=trend_period)]
        if candidates:
            winner = max(candidates, key=lambda fund: (self._score(context, fund, horizon), 1 if fund == self.broad_symbol else 0))
        else:
            winner = None
        if winner is not None:
            target = 0.99
            if vol_target is not None:
                volatility = self._volatility(context, winner, 60)
                target = 0.99 * min(1.0, vol_target / volatility) if _finite(volatility) and volatility > 0 else 0.0
            if target > 0:
                targets[winner] = target
        return targets, winner

    def _decide_l01(self, context: DecisionContext) -> list[OrderIntent]:
        exits = self._daily_proxy_exits(context, period=200, reason="L01_DAILY_PROXY_SMA200_EXIT")
        if exits:
            return exits
        if not self._review(context, weekly=False):
            return []
        horizon = 126
        if self.variant.startswith("long_"):
            horizon = int(self.variant.split("_", 1)[1])
        targets, winner = self._rotation_targets(context, horizon=horizon, weekly=False, vol_target=None)
        self.state.selected = winner
        self.state.current_targets = dict(targets)
        return self._target_intents(context, targets, "L01_MONTHLY_PROXY_MOMENTUM", review=True)

    def _decide_l02(self, context: DecisionContext) -> list[OrderIntent]:
        exits = self._daily_proxy_exits(context, period=200, reason="L02_DAILY_PROXY_SMA200_EXIT")
        if exits:
            return exits
        if not self._review(context):
            return []
        target = 0.40
        if self.variant.startswith("vol_target_"):
            target = float(self.variant.split("_", 2)[2]) / 100.0
        targets, winner = self._rotation_targets(context, horizon=126, weekly=True, vol_target=target)
        self.state.selected = winner
        self.state.current_targets = dict(targets)
        return self._target_intents(context, targets, "L02_WEEKLY_VOLATILITY_TARGETED_MOMENTUM", review=True)

    # ----- L03 and L04 -------------------------------------------------
    def _decide_l03(self, context: DecisionContext) -> list[OrderIntent]:
        exits: list[OrderIntent] = []
        entries: list[OrderIntent] = []
        slow = 100
        if self.variant.startswith("ema_"):
            slow = int(self.variant.split("_", 1)[1])
        for fund in self.symbols:
            proxy = self.broad_proxy if fund == self.broad_symbol else self.sector_proxy
            close = self._feature(context, proxy, "close")
            fast = self._feature(context, proxy, "ema20")
            slow_value = self._feature(context, proxy, f"ema{slow}")
            trend = self._feature(context, proxy, "sma200")
            held = self._held(context, fund)
            pending = self._pending(context, fund)
            if held and ((_finite(fast) and _finite(slow_value) and fast < slow_value) or (_finite(close) and _finite(trend) and close < trend)):
                exits.append(self._exit(fund, "L03_PROXY_TREND_EXIT"))
            elif not held and not pending and _finite(fast) and _finite(slow_value) and _finite(close) and _finite(trend) and fast > slow_value and close > trend:
                entries.append(self._intent(fund, SizingMode.ENTER_SLEEVE, weight=0.495, reason="L03_DUAL_PROXY_TREND_SLEEVE"))
        if exits:
            entries = [item for item in entries if item.symbol not in {x.symbol for x in exits}]
        return [*exits, *entries]

    def _decide_l04(self, context: DecisionContext) -> list[OrderIntent]:
        exits = self._daily_proxy_exits(context, period=200, reason="L04_DAILY_PROXY_SMA200_EXIT")
        exited = {item.symbol for item in exits}
        if not self._review(context):
            return exits
        # L04's eligibility is only the proxy SMA200 test; momentum is not
        # part of this strategy.  Keep the condition explicit instead of
        # routing through the generic momentum helper.
        eligible = {
            fund: (0.60 if fund == self.broad_symbol else 0.40)
            for fund in self.symbols
            if _finite(self._feature(context, self.broad_proxy if fund == self.broad_symbol else self.sector_proxy, "close"))
            and _finite(self._feature(context, self.broad_proxy if fund == self.broad_symbol else self.sector_proxy, "sma200"))
            and self._feature(context, self.broad_proxy if fund == self.broad_symbol else self.sector_proxy, "close") > self._feature(context, self.broad_proxy if fund == self.broad_symbol else self.sector_proxy, "sma200")
        }
        scale = 1.0
        target = 0.40
        if self.variant.startswith("vol_target_"):
            target = float(self.variant.split("_", 2)[2]) / 100.0
        volatility = self._portfolio_volatility(context, eligible)
        if not _finite(volatility):
            return exits
        if volatility > 0:
            scale = min(1.0, target / volatility)
        targets = {fund: 0.99 * weight * scale for fund, weight in eligible.items()}
        self.state.current_targets = dict(targets)
        return [*exits, *self._target_intents(context, targets, "L04_WEEKLY_COVARIANCE_CONTROL", suppress=exited)]

    # ----- L05, L06 and L07 -------------------------------------------
    def _decide_l05(self, context: DecisionContext) -> list[OrderIntent]:
        multiple = 3.0
        if self.variant.startswith("atr_"):
            multiple = float(self.variant.split("_", 1)[1])
        exits: list[OrderIntent] = []
        entries: list[OrderIntent] = []
        for fund in self.symbols:
            proxy = self.broad_proxy if fund == self.broad_symbol else self.sector_proxy
            close = self._feature(context, proxy, "close")
            channel = self._feature(context, proxy, "hh55")
            trend = self._feature(context, proxy, "sma200")
            actual_close = self._feature(context, fund, "close")
            atr = self._feature(context, fund, "atr14")
            held = self._held(context, fund)
            pending = self._pending(context, fund)
            if held:
                high = self.state.highest_close.get(fund, actual_close)
                if _finite(actual_close):
                    high = max(high, actual_close) if _finite(high) else actual_close
                    self.state.highest_close[fund] = high
                lower = self._feature(context, proxy, "ll20")
                trailing = _finite(high) and _finite(atr) and _finite(actual_close) and actual_close < high - multiple * atr
                channel_exit = _finite(close) and _finite(lower) and close < lower
                if trailing or channel_exit:
                    exits.append(self._exit(fund, "L05_BREAKOUT_TRAILING_OR_CHANNEL_EXIT"))
                    self.state.highest_close.pop(fund, None)
            elif not pending and _finite(close) and _finite(channel) and _finite(trend) and close > channel and close > trend and _finite(actual_close) and actual_close > 0 and _finite(atr) and atr > 0:
                risk_fraction = multiple * atr / actual_close
                weight = min(0.495, 0.02 / risk_fraction) if risk_fraction > 0 else 0.0
                if weight > 0:
                    entries.append(self._intent(fund, SizingMode.ENTER_SLEEVE, weight=weight, reason="L05_PROXY_BREAKOUT_RISK_BUDGET"))
        return [*exits, *entries]

    def _decide_l06(self, context: DecisionContext) -> list[OrderIntent]:
        threshold = 10.0
        if self.variant.startswith("rsi_"):
            threshold = float(self.variant.split("_", 1)[1])
        exits: list[OrderIntent] = []
        entries: list[OrderIntent] = []
        for fund in self.symbols:
            proxy = self.broad_proxy if fund == self.broad_symbol else self.sector_proxy
            close = self._feature(context, proxy, "close")
            sma50 = self._feature(context, proxy, "sma50")
            sma200 = self._feature(context, proxy, "sma200")
            rsi = self._feature(context, proxy, "rsi2")
            position = self._position(context, fund)
            held = position is not None and position.quantity_microshares > 0
            pending = self._pending(context, fund)
            if held:
                timed = position.holding_sessions >= 5
                if (_finite(rsi) and rsi > 70) or (_finite(close) and _finite(sma50) and close < sma50) or timed:
                    exits.append(self._exit(fund, "L06_TACTICAL_REBOUND_EXIT"))
                    self.state.highest_close.pop(fund, None)
            elif not pending and self._cooldown_ready(context, fund) and _finite(close) and _finite(sma50) and _finite(sma200) and _finite(rsi) and close > sma200 and sma50 > sma200 and rsi < threshold:
                entries.append(self._intent(fund, SizingMode.ENTER_SLEEVE, weight=0.33, reason="L06_TACTICAL_OVERSOLD_REBOUND"))
        return [*exits, *entries]

    def _contraction(self, context: DecisionContext, proxy: str) -> bool:
        value = self._feature(context, proxy, "contraction")
        if self.variant.startswith("contraction_"):
            cutoff = context.information_cutoff
            frame = self.store.frames.get(proxy) if self.store is not None and hasattr(self.store, "frames") else None
            if frame is not None and cutoff is not None:
                values = frame.loc[frame.index <= _stamp(cutoff), "bandwidth"].dropna().shift(1).tail(126)
                percentile = float(self.variant.split("_", 1)[1]) / 100.0
                bandwidth = self._feature(context, proxy, "bandwidth")
                value = bandwidth < values.quantile(percentile) if len(values) >= 20 and _finite(bandwidth) else False
        return bool(value) if not pd.isna(value) else False

    def _decide_l07(self, context: DecisionContext) -> list[OrderIntent]:
        exits: list[OrderIntent] = []
        entries: list[OrderIntent] = []
        index = self._cutoff_index(context)
        for fund in self.symbols:
            proxy = self.broad_proxy if fund == self.broad_symbol else self.sector_proxy
            close = self._feature(context, proxy, "close")
            fast = self._feature(context, proxy, "ema20")
            channel = self._feature(context, proxy, "hh20")
            trend = self._feature(context, proxy, "sma100")
            position = self._position(context, fund)
            held = position is not None and position.quantity_microshares > 0
            pending = self._pending(context, fund)
            if held:
                if (_finite(fast) and _finite(close) and close < fast) or position.holding_sessions >= 20:
                    exits.append(self._exit(fund, "L07_CONTRACTION_BREAKOUT_EXIT"))
            elif not pending:
                if self._contraction(context, proxy):
                    self.state.armed[fund] = index
                arm = self.state.armed.get(fund)
                if arm is not None and index - arm > 10:
                    self.state.armed.pop(fund, None)
                    arm = None
                if arm is not None and index - arm <= 10 and _finite(close) and _finite(channel) and _finite(trend) and close > channel and close > trend:
                    entries.append(self._intent(fund, SizingMode.ENTER_SLEEVE, weight=0.495, reason="L07_VOLATILITY_CONTRACTION_BREAKOUT"))
        return [*exits, *entries]

    # ----- L08, L09 and L10 -------------------------------------------
    def _l08_selection(self, context: DecisionContext) -> str | None:
        vclose = self._feature(context, self.sector_proxy, "close")
        vtrend = self._feature(context, self.sector_proxy, "sma100")
        uclose = self._feature(context, self.broad_proxy, "close")
        utrend = self._feature(context, self.broad_proxy, "sma100")
        vr = self._feature(context, self.sector_proxy, "r63")
        ur = self._feature(context, self.broad_proxy, "r63")
        ratio = self._pair_value(context, "ratio")
        ratio_sma = self._pair_value(context, "ratio_sma20")
        if _finite(vclose) and _finite(vtrend) and _finite(ratio) and _finite(ratio_sma) and _finite(vr) and _finite(ur) and vclose > vtrend and ratio > ratio_sma and vr > ur:
            return self.semiconductor
        if _finite(uclose) and _finite(utrend) and uclose > utrend:
            return self.broad_symbol
        return None

    def _decide_l08(self, context: DecisionContext) -> list[OrderIntent]:
        exits = self._daily_proxy_exits(context, funds=(self.state.selected,) if self.state.selected else (), period=100, reason="L08_SELECTED_PROXY_SMA100_EXIT")
        if exits:
            return exits
        if not self._review(context):
            return []
        selected = self._l08_selection(context)
        targets: dict[str, float] = {}
        if selected is not None:
            target = 0.50
            if self.variant.startswith("vol_target_"):
                target = float(self.variant.split("_", 2)[2]) / 100.0
            volatility = self._volatility(context, selected, 60)
            if _finite(volatility) and volatility > 0:
                targets[selected] = 0.99 * min(1.0, target / volatility)
        self.state.selected = selected
        self.state.current_targets = dict(targets)
        return self._target_intents(context, targets, "L08_SEMICONDUCTOR_LEADERSHIP_FALLBACK", review=True)

    def _ladder_targets(self, context: DecisionContext) -> dict[str, float]:
        nominal = {self.broad_symbol: 0.60, self.semiconductor: 0.40}
        result: dict[str, float] = {}
        for fund, base in nominal.items():
            proxy = self.broad_proxy if fund == self.broad_symbol else self.sector_proxy
            close = self._feature(context, proxy, "close")
            sma200 = self._feature(context, proxy, "sma200")
            ema20 = self._feature(context, proxy, "ema20")
            ema100 = self._feature(context, proxy, "ema100")
            r63 = self._feature(context, proxy, "r63")
            points = sum((_finite(close) and _finite(sma200) and close > sma200, _finite(ema20) and _finite(ema100) and ema20 > ema100, _finite(r63) and r63 > 0))
            if self._cooldown_ready(context, fund):
                result[fund] = 0.99 * base * points / 3.0
        return {fund: weight for fund, weight in result.items() if weight > 0}

    def _decide_l09(self, context: DecisionContext) -> list[OrderIntent]:
        exits: list[OrderIntent] = []
        exited: set[str] = set()
        for fund in self.symbols:
            proxy = self.broad_proxy if fund == self.broad_symbol else self.sector_proxy
            r1 = self._feature(context, proxy, "r1")
            r5 = self._feature(context, proxy, "r5")
            close = self._feature(context, proxy, "close")
            trend = self._feature(context, proxy, "sma200")
            shock = (_finite(r1) and r1 <= -0.04) or (_finite(r5) and r5 <= -0.08)
            trend_exit = _finite(close) and _finite(trend) and close < trend
            if self._in_position(context, fund) and (shock or trend_exit):
                exits.append(self._exit(fund, "L09_SHOCK_OR_PROXY_TREND_EXIT"))
                exited.add(fund)
                if shock:
                    self.state.shock_exit_pending.add(fund)
        if not self._review(context):
            return exits
        targets = self._ladder_targets(context)
        self.state.current_targets = dict(targets)
        return [*exits, *self._target_intents(context, targets, "L09_EXPOSURE_LADDER_SHOCK_COOLDOWN", suppress=exited)]

    def _decide_l10(self, context: DecisionContext) -> list[OrderIntent]:
        if self._trend_child is None or self._pullback_child is None:
            return []
        share = 0.70
        if self.variant.startswith("trend_share_"):
            share = float(self.variant.split("_", 2)[2]) / 100.0
        trend = list(self._trend_child.decide(context))
        pullback = list(self._pullback_child.decide(context))
        output: list[OrderIntent] = []
        for item in trend:
            weight = None if item.target_weight is None else share * float(item.target_weight)
            output.append(OrderIntent(item.symbol, item.mode, component_id="L02", target_weight=weight, target_quantity=item.target_quantity, reduction_fraction=item.reduction_fraction, reason=item.reason, metadata={**dict(item.metadata), "ensemble_component": "L02", "component_budget": share}))
        for item in pullback:
            weight = None if item.target_weight is None else (1.0 - share) * float(item.target_weight)
            output.append(OrderIntent(item.symbol, item.mode, component_id="L06", target_weight=weight, target_quantity=item.target_quantity, reduction_fraction=item.reduction_fraction, reason=item.reason, metadata={**dict(item.metadata), "ensemble_component": "L06", "component_budget": 1.0 - share}))
        return output

    # ----- L11--L16 ----------------------------------------------------
    def _l11_targets(self, context: DecisionContext) -> dict[str, float]:
        broad_ok = self._eligible(context, self.broad_symbol, horizon=126, trend_period=200)
        sector_ok = self._eligible(context, self.semiconductor, horizon=126, trend_period=200)
        if sector_ok:
            vr = self._feature(context, self.sector_proxy, "r63")
            ur = self._feature(context, self.broad_proxy, "r63")
            ratio = self._pair_value(context, "ratio")
            ratio_sma = self._pair_value(context, "ratio_sma20")
            sector_ok = _finite(vr) and _finite(ur) and _finite(ratio) and _finite(ratio_sma) and vr > ur and ratio > ratio_sma
        if broad_ok and sector_ok:
            return {self.broad_symbol: 0.495, self.semiconductor: 0.495}
        if broad_ok:
            return {self.broad_symbol: 0.99}
        if sector_ok:
            return {self.semiconductor: 0.495}
        return {}

    def _decide_l11(self, context: DecisionContext) -> list[OrderIntent]:
        exits = self._daily_proxy_exits(context, period=200, reason="L11_DAILY_PROXY_SMA200_EXIT")
        exited = {item.symbol for item in exits}
        if not self._review(context):
            return exits
        targets = self._l11_targets(context)
        self.state.current_targets = dict(targets)
        return [*exits, *self._target_intents(context, targets, "L11_BROAD_FIRST_LEADERSHIP", suppress=exited)]

    def _choose_fast_rotation(self, context: DecisionContext) -> str | None:
        candidates = [fund for fund in self.symbols if self._eligible(context, fund, horizon=126, trend_period=200)]
        scored = [(0.5 * self._feature(context, self.broad_proxy if fund == self.broad_symbol else self.sector_proxy, "r21") + 0.5 * self._feature(context, self.broad_proxy if fund == self.broad_symbol else self.sector_proxy, "r63"), fund) for fund in candidates]
        scored = [(score, fund) for score, fund in scored if _finite(score) and self._feature(context, self.broad_proxy if fund == self.broad_symbol else self.sector_proxy, "r21") > 0]
        return max(scored, key=lambda x: (x[0], 1 if x[1] == self.broad_symbol else 0))[1] if scored else None

    def _decide_l12(self, context: DecisionContext) -> list[OrderIntent]:
        exits = self._daily_proxy_exits(context, funds=(self.state.selected,) if self.state.selected else (), period=100, reason="L12_SELECTED_PROXY_SMA100_EXIT")
        if exits:
            return exits
        if not self._review(context):
            return []
        selected = self._choose_fast_rotation(context)
        targets: dict[str, float] = {}
        if selected is not None:
            volatility = self._volatility(context, selected, 60)
            if _finite(volatility) and volatility > 0:
                target = 0.30 if self.variant.startswith("vol_target_30") else 0.50 if self.variant.startswith("vol_target_50") else 0.40
                targets[selected] = 0.99 * min(1.0, target / volatility)
        self.state.selected = selected
        self.state.current_targets = dict(targets)
        return self._target_intents(context, targets, "L12_FAST_SLOW_AGREEMENT_ROTATION", review=True)

    def _decide_l13(self, context: DecisionContext) -> list[OrderIntent]:
        exits = self._daily_proxy_exits(context, period=200, reason="L13_DAILY_PROXY_SMA200_EXIT")
        exited = {item.symbol for item in exits}
        if not self._review(context):
            return exits
        targets: dict[str, float] = {}
        for fund, base in ((self.broad_symbol, 0.60), (self.semiconductor, 0.40)):
            proxy = self.broad_proxy if fund == self.broad_symbol else self.sector_proxy
            if self._eligible(context, fund, horizon=126, trend_period=200):
                persistence = self._feature(context, proxy, "trend_persistence20")
                if _finite(persistence):
                    targets[fund] = 0.99 * base * min(1.0, max(0.0, persistence))
        self.state.current_targets = dict(targets)
        return [*exits, *self._target_intents(context, targets, "L13_TREND_PERSISTENCE_EXPOSURE", suppress=exited)]

    def _decide_l14(self, context: DecisionContext) -> list[OrderIntent]:
        exits = self._daily_proxy_exits(context, period=200, reason="L14_DAILY_PROXY_SMA200_EXIT")
        exited = {item.symbol for item in exits}
        if not self._review(context):
            return exits
        nominal = {fund: base for fund, base in ((self.broad_symbol, 0.60), (self.semiconductor, 0.40)) if self._eligible(context, fund, horizon=126, trend_period=200)}
        downside = self._downside_volatility(context, nominal)
        if not _finite(downside):
            return exits
        target_vol = 0.25
        scale = 1.0 if downside <= 0 else min(1.0, target_vol / downside)
        targets = {fund: 0.99 * weight * scale for fund, weight in nominal.items()}
        self.state.current_targets = dict(targets)
        return [*exits, *self._target_intents(context, targets, "L14_DOWNSIDE_VOLATILITY_ALLOCATION", suppress=exited)]

    def _decide_l15(self, context: DecisionContext) -> list[OrderIntent]:
        exits = self._daily_proxy_exits(context, period=200, reason="L15_DAILY_PROXY_SMA200_EXIT")
        if exits:
            return exits
        if not self._review(context):
            return []
        candidates = [fund for fund in self.symbols if self._eligible(context, fund, horizon=126, trend_period=200)]
        scores = [(self._score(context, fund, 126), fund) for fund in candidates]
        selected = max(scores, key=lambda item: (item[0], 1 if item[1] == self.broad_symbol else 0))[1] if scores else None
        targets: dict[str, float] = {}
        if selected is not None:
            proxy = self.broad_proxy if selected == self.broad_symbol else self.sector_proxy
            er = self._feature(context, proxy, "er63")
            if _finite(er) and er > 0:
                targets[selected] = 0.99 * min(1.0, er / 0.30)
        self.state.selected = selected
        self.state.current_targets = dict(targets)
        return self._target_intents(context, targets, "L15_TREND_QUALITY_ROTATION", review=True)

    def _risk_flag(self, context: DecisionContext, fund: str) -> bool:
        proxy = self.broad_proxy if fund == self.broad_symbol else self.sector_proxy
        close = self._feature(context, proxy, "close")
        ema = self._feature(context, proxy, "ema20")
        r5 = self._feature(context, proxy, "r5")
        return (_finite(close) and _finite(ema) and close < ema) or (_finite(r5) and r5 <= -0.06)

    def _recovery(self, context: DecisionContext, fund: str) -> bool:
        proxy = self.broad_proxy if fund == self.broad_symbol else self.sector_proxy
        value = self._feature(context, proxy, "recovery5")
        if _finite(value):
            return bool(value > 0.5)
        close = self._feature(context, proxy, "close")
        ema = self._feature(context, proxy, "ema20")
        r5 = self._feature(context, proxy, "r5")
        return _finite(close) and _finite(ema) and _finite(r5) and close > ema and r5 > -0.06

    def _decide_l16(self, context: DecisionContext) -> list[OrderIntent]:
        exits: list[OrderIntent] = []
        suppressed: set[str] = set()
        daily_reductions: dict[str, float] = {}
        # Full SMA200 exits are deterministic and take priority over all risk
        # reductions.  A full exit sets a re-entry gate; restoration therefore
        # needs a later weekly review plus the five-session recovery condition.
        for fund in self.symbols:
            proxy = self.broad_proxy if fund == self.broad_symbol else self.sector_proxy
            close = self._feature(context, proxy, "close")
            trend = self._feature(context, proxy, "sma200")
            if self._in_position(context, fund) and _finite(close) and _finite(trend) and close < trend:
                exits.append(self._exit(fund, "L16_FULL_PROXY_SMA200_EXIT"))
                suppressed.add(fund)
                self.state.reentry_blocked.add(fund)
                self.state.reduced.discard(fund)
                self.state.reduced_targets.pop(fund, None)
        # Existing holdings can be cut immediately when the fast risk flag is
        # raised.  This is a reduction only; the state prevents a daily
        # restoration when the flag clears.
        base = self.state.base_targets
        for fund in self.symbols:
            if fund in suppressed or not self._held(context, fund) or fund in self.state.reduced:
                continue
            if self._risk_flag(context, fund):
                cap = 0.5 * float(base.get(fund, self.state.current_targets.get(fund, 0.0)))
                position = self._position(context, fund)
                current = 0.0
                price = context.prior_close.get(fund)
                if position is not None and price and context.prior_close_equity > 0:
                    current = position.quantity * float(price) / float(context.split_factors.get(fund,1.0)) / context.prior_close_equity
                reduced = min(cap, current) if current > 0 else cap
                if reduced > 0:
                    self.state.reduced.add(fund)
                    self.state.reduced_targets[fund] = reduced
                    if reduced < current - _EPS:
                        daily_reductions[fund] = reduced
        if not self._review(context):
            for fund, reduced in daily_reductions.items():
                price = context.prior_close.get(fund)
                if price and context.prior_close_equity > 0:
                    quantity = reduced * context.prior_close_equity / float(price)
                    exits.append(self._intent(fund, SizingMode.REDUCE, quantity=quantity, reason="L16_FAST_RISK_REDUCTION"))
            return exits
        base = self._l11_targets(context)
        self.state.base_targets = dict(base)
        targets: dict[str, float] = {}
        for fund in self.symbols:
            desired = float(base.get(fund, 0.0))
            if fund in self.state.reentry_blocked:
                if self._recovery(context, fund):
                    self.state.reentry_blocked.discard(fund)
                else:
                    desired = 0.0
            if fund in self.state.reduced:
                if self._recovery(context, fund):
                    self.state.reduced.discard(fund)
                    self.state.reduced_targets.pop(fund, None)
                else:
                    prior_reduced = self.state.reduced_targets.get(fund, desired)
                    desired = min(prior_reduced, 0.5 * float(base.get(fund,0.0))) if desired > 0 else 0.0
            if desired > 0 and self._risk_flag(context, fund):
                # Use the new *base* target for the cap.  Applying 0.5 to an
                # already reduced target would halve the position again on
                # every weekly review.
                cap = 0.5 * float(base.get(fund, 0.0))
                prior_reduced = self.state.reduced_targets.get(fund, cap)
                desired = min(prior_reduced, cap)
                self.state.reduced.add(fund)
                self.state.reduced_targets[fund] = desired
            targets[fund] = desired
        self.state.current_targets = dict(targets)
        review_intents = self._target_intents(context, targets, "L16_WEEKLY_BROAD_FIRST_RISK_RESTORATION", review=True, suppress=suppressed)
        # A weekly review and a same-session daily reduction address the same
        # component/symbol.  Emit exactly one intent per key; the review target
        # wins when it changes the base, while a non-review day uses REDUCE so
        # a risk response can never create a new buy.
        review_symbols = {item.symbol for item in review_intents}
        for fund, reduced in daily_reductions.items():
            if fund in review_symbols or fund in suppressed:
                continue
            price = context.prior_close.get(fund)
            if price and context.prior_close_equity > 0:
                quantity = reduced * context.prior_close_equity / float(price)
                exits.append(self._intent(fund, SizingMode.REDUCE, quantity=quantity, reason="L16_FAST_RISK_REDUCTION"))
        return [*exits, *review_intents]


def make_l_strategy(candidate: Any, *, store: Any, data: Any, config: Any, start: Any, end: Any) -> LeveragedStrategyV3:
    """Build a frozen leveraged candidate from a protocol ``CandidateSpec``."""
    if str(candidate.strategy_id).upper() not in TRACK_B_STRATEGY_IDS:
        raise ValueError("ETF_V3_LEVERAGED_CANDIDATE_REQUIRED")
    universe = candidate.universe
    broad, semiconductor = tuple(universe.tradable_symbols)
    proxies = (universe.broad_proxy or _BROAD_PROXY[str(broad).upper()], universe.semiconductor_proxy or "SOXX")
    return LeveragedStrategyV3(
        candidate.strategy_id,
        broad,
        semiconductor,
        proxies,
        store=store,
        data=data,
        config=config,
        start=start,
        end=end,
        variant=getattr(candidate, "variant", "primary"),
    )


def build_track_b(strategy_id: str, broad_symbol: str, semiconductor: str = "SOXL", proxies: tuple[str, str] | None = None, *, store: Any | None = None, data: Any | None = None, config: Any | None = None, start: Any | None = None, end: Any | None = None, variant: str = "primary") -> LeveragedStrategyV3:
    """Convenience constructor retained for direct fixture users."""
    return LeveragedStrategyV3(strategy_id, broad_symbol, semiconductor, proxies, store=store, data=data, config=config, start=start, end=end, variant=variant)


__all__ = ["TRACK_B_STRATEGY_IDS", "LeveragedStrategyV3", "build_track_b", "make_l_strategy"]
