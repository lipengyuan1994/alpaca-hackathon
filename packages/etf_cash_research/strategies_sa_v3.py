"""Causal v3 implementations for the unleveraged ETF candidates.

The older research strategies returned target weights from a synthetic index
and kept signal state instead of fill state.  This module is the adapter used
by the repaired execution engine.  It reads only ``DecisionContext`` values
through the previous completed close and expresses changes as ``OrderIntent``
objects.  The engine owns prices, cash, settlement and actual ownership.

``S01``--``S10`` are the frozen first-study reference candidates.  ``A01``--
``A11`` are the additional QQQM/SMH candidates from the approved v3 study.
The class is deliberately small and serializable: all state is ordinary
Python data, and an entry is considered real only after a position appears in
``context.positions`` or a filled feedback event is received.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping

import pandas as pd

from .engine_v3 import DecisionContext, EngineConfig, OrderIntent, SizingMode

_SA_IDS = {*(f"S{i:02d}" for i in range(1, 11)), *(f"A{i:02d}" for i in range(1, 12))}
_SYMBOLS = ("QQQM", "SMH")


def _finite(value: Any) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _number(value: Any, default: float = math.nan) -> float:
    return float(value) if _finite(value) else default


def _session_index(ctx: DecisionContext, symbol: str = "QQQM") -> int:
    """Return the execution-session ordinal known at the cutoff.

    FeatureStore puts ``session_index`` on every row.  The cutoff row is one
    session before the execution row, so adding one gives an ordinal useful for
    holding ages and cooldowns without looking at current-session prices.
    """

    row = ctx.features.get(symbol, {})
    value = row.get("session_index")
    return int(value) + 1 if _finite(value) else 0


def _feature(ctx: DecisionContext, symbol: str, key: str, default: float = math.nan) -> float:
    return _number(ctx.features.get(symbol, {}).get(key), default)


def _held(ctx: DecisionContext, symbol: str, component: str = "account") -> Any:
    return ctx.positions.get((component, symbol))


def _quantity(ctx: DecisionContext, symbol: str, component: str = "account") -> int:
    position = _held(ctx, symbol, component)
    return int(position.quantity_microshares) if position is not None else 0


def _has_pending(ctx: DecisionContext, symbol: str, component: str = "account", side: str | None = None) -> bool:
    for order in ctx.pending_orders:
        if order.component_id == component and order.symbol == symbol and (side is None or order.side == side):
            return True
    return False


def _position_age(ctx: DecisionContext, symbol: str, component: str) -> int:
    position = _held(ctx, symbol, component)
    return int(position.holding_sessions) if position is not None else 0


def _is_filled_sell(feedback: Any) -> bool:
    return getattr(feedback, "side", None) == "sell" and str(getattr(feedback, "status", "")).startswith("filled") and int(getattr(feedback, "filled_quantity_microshares", 0)) > 0


def _recent_exit(ctx: DecisionContext, symbol: str, component: str) -> bool:
    return any(
        _is_filled_sell(item)
        and getattr(item, "symbol", None) == symbol
        and getattr(item, "component_id", None) == component
        for item in ctx.recent_feedback
    )


def _review(ctx: DecisionContext, kind: str) -> bool:
    return bool(getattr(ctx, f"review_{kind}"))


def _ratio_row(strategy: "SAEngineStrategy", ctx: DecisionContext) -> Mapping[str, Any]:
    """Return point-in-time cross-symbol fields for the QQQM/semiconductor pair."""

    for symbol in ("SMH", strategy.semiconductor):
        values = ctx.features.get(symbol, {})
        if "ratio_sma20" in values or "corr60" in values:
            return values
    if strategy.store is not None and ctx.information_cutoff is not None:
        try:
            pair = strategy.store.pair("QQQM", strategy.semiconductor)
            key = pd.Timestamp(ctx.information_cutoff)
            if key in pair.index:
                return pair.loc[key]
        except (KeyError, TypeError, ValueError):
            pass
    return {}


def _close(ctx: DecisionContext, symbol: str) -> float:
    return _feature(ctx, symbol, "close")


def _target_intent(symbol: str, weight: float, component: str, reason: str, *, rebalance: bool = False) -> OrderIntent:
    return OrderIntent(
        symbol=symbol,
        component_id=component,
        mode=SizingMode.REBALANCE if rebalance else SizingMode.ENTER_SLEEVE,
        target_weight=max(0.0, min(0.99, float(weight))),
        reason=reason,
    )


def _exit_intent(symbol: str, component: str, reason: str) -> OrderIntent:
    return OrderIntent(symbol=symbol, component_id=component, mode=SizingMode.EXIT_FULLY, reason=reason)


@dataclass
class _State:
    selected: str | None = None
    target_weights: dict[str, float] = field(default_factory=dict)
    active: dict[str, bool] = field(default_factory=dict)
    armed: dict[str, int] = field(default_factory=dict)
    highest: dict[str, float] = field(default_factory=dict)
    cooldown_until: dict[str, int] = field(default_factory=dict)
    scale: float = 1.0
    trend_share: float = 0.70


class SAEngineStrategy:
    """One causal S/A candidate bound to an execution component."""

    def __init__(
        self,
        strategy_id: str,
        semiconductor: str = "SMH",
        variant: str = "primary",
        *,
        store: Any | None = None,
        component_id: str = "account",
    ) -> None:
        if strategy_id not in _SA_IDS:
            raise ValueError(f"ETF_V3_SA_STRATEGY_UNKNOWN:{strategy_id}")
        if semiconductor not in {"SMH", "SOXX"}:
            raise ValueError("ETF_V3_SA_SECTOR_UNKNOWN")
        self.strategy_id = strategy_id
        self.semiconductor = semiconductor
        self.variant = variant
        self.store = store
        self.component_id = component_id
        self.state = _State()
        self.shadow_result: Any | None = None
        self._trend: SAEngineStrategy | None = None
        self._pullback: SAEngineStrategy | None = None
        if strategy_id in {"S10", "A09", "A10"}:
            self._trend = SAEngineStrategy("S01", semiconductor, store=store, component_id="S01")
            self._pullback = SAEngineStrategy("S04", semiconductor, store=store, component_id="S04")

    @property
    def symbols(self) -> tuple[str, str]:
        return ("QQQM", self.semiconductor)

    def _variant_number(self, prefix: str, default: float) -> float:
        if self.variant == "primary":
            return default
        for candidate in (prefix + "_",):
            if self.variant.startswith(candidate):
                try:
                    return float(self.variant[len(candidate) :])
                except ValueError:
                    return default
        return default

    def _held_any(self, ctx: DecisionContext, symbol: str) -> bool:
        return _quantity(ctx, symbol, self.component_id) > 0

    def _base_plan(self, ctx: DecisionContext, *, component: str | None = None) -> tuple[dict[str, float], set[str], set[str]]:
        """Return desired weights, explicit exits and symbols to rebalance.

        The third value marks scheduled target-weight decisions.  Fixed-share
        strategies leave it empty so a filled position is not rebalanced on
        every day.
        """

        identifier = self.strategy_id
        if identifier == "S01":
            return self._s01(ctx)
        if identifier == "S02":
            return self._s02(ctx)
        if identifier == "S03":
            return self._s03(ctx)
        if identifier == "S04":
            return self._s04(ctx)
        if identifier == "S05":
            return self._s05(ctx)
        if identifier == "S06":
            return self._s06(ctx)
        if identifier == "S07":
            return self._s07(ctx)
        if identifier == "S08":
            return self._s08(ctx)
        if identifier == "S09":
            return self._s09(ctx)
        if identifier == "S10":
            return self._ensemble(ctx, 0.70, "S10_FIXED_ENSEMBLE")
        if identifier == "A01":
            return self._a01(ctx)
        if identifier == "A02":
            return self._a02(ctx)
        if identifier == "A03":
            return self._a03(ctx)
        if identifier == "A04":
            return self._a04(ctx)
        if identifier == "A05":
            return self._a05(ctx)
        if identifier == "A06":
            return self._a06(ctx)
        if identifier == "A07":
            return self._a07(ctx)
        if identifier == "A08":
            return self._a08(ctx)
        if identifier == "A09":
            return self._a09(ctx)
        if identifier == "A10":
            return self._a10(ctx)
        return self._a11(ctx)

    # ---- frozen S01--S10 -------------------------------------------------

    def _s01(self, ctx: DecisionContext) -> tuple[dict[str, float], set[str], set[str]]:
        horizon = int(self._variant_number("momentum", 126))
        exits = {symbol for symbol in self.symbols if self._held_any(ctx, symbol) and _finite(_feature(ctx, symbol, "sma200")) and _close(ctx, symbol) < _feature(ctx, symbol, "sma200")}
        if exits and self.state.selected in exits:
            self.state.selected = None
            for symbol in exits:
                self.state.target_weights.pop(symbol, None)
        if _review(ctx, "monthly"):
            eligible: list[tuple[float, str]] = []
            for symbol in self.symbols:
                trend = _feature(ctx, symbol, "sma200")
                momentum = _feature(ctx, symbol, f"r{horizon}")
                r63 = _feature(ctx, symbol, "r63")
                if _finite(trend) and _finite(momentum) and _finite(r63) and _close(ctx, symbol) > trend and momentum > 0:
                    eligible.append((0.5 * r63 + 0.5 * momentum, symbol))
            winner = max(eligible, key=lambda item: (item[0], 1 if item[1] == "QQQM" else 0))[1] if eligible else None
            if winner != self.state.selected:
                if self.state.selected:
                    exits.add(self.state.selected)
                self.state.selected = winner
            self.state.target_weights = {winner: 0.99} if winner else {}
            scheduled = set(self.state.target_weights)
        else:
            scheduled = set()
        # A daily exit stays out until the next monthly review.
        desired = {symbol: weight for symbol, weight in self.state.target_weights.items() if symbol not in exits}
        return desired, exits, scheduled

    def _s02(self, ctx: DecisionContext) -> tuple[dict[str, float], set[str], set[str]]:
        slow = int(self._variant_number("ema", 100))
        desired: dict[str, float] = {}
        exits: set[str] = set()
        for symbol in self.symbols:
            fast, slow_value, trend, close = (_feature(ctx, symbol, key) for key in ("ema20", f"ema{slow}", "sma200", "close"))
            qualifies = _finite(fast) and _finite(slow_value) and _finite(trend) and fast > slow_value and close > trend
            if self._held_any(ctx, symbol):
                if (_finite(fast) and _finite(slow_value) and fast < slow_value) or (_finite(trend) and close < trend):
                    exits.add(symbol)
                else:
                    desired[symbol] = 0.495
            elif qualifies:
                desired[symbol] = 0.495
        return desired, exits, set()

    def _s03(self, ctx: DecisionContext) -> tuple[dict[str, float], set[str], set[str]]:
        entry_period = int(self._variant_number("breakout", 55))
        desired: dict[str, float] = {}
        exits: set[str] = set()
        for symbol in self.symbols:
            close, high, low = _feature(ctx, symbol, "close"), _feature(ctx, symbol, f"hh{entry_period}"), _feature(ctx, symbol, "ll20")
            held = self._held_any(ctx, symbol)
            if held and _finite(low) and close < low:
                exits.add(symbol)
            elif held or (_finite(high) and close > high):
                desired[symbol] = 0.495
        return desired, exits, set()

    def _s04(self, ctx: DecisionContext) -> tuple[dict[str, float], set[str], set[str]]:
        threshold = self._variant_number("rsi", 10.0)
        desired: dict[str, float] = {}
        exits: set[str] = set()
        current_idx = _session_index(ctx)
        for symbol in self.symbols:
            held = self._held_any(ctx, symbol)
            age = _position_age(ctx, symbol, self.component_id)
            close, trend, fast, rsi2 = (_feature(ctx, symbol, key) for key in ("close", "sma200", "sma50", "rsi2"))
            if _recent_exit(ctx, symbol, self.component_id):
                self.state.cooldown_until[symbol] = max(self.state.cooldown_until.get(symbol, -1), current_idx)
            if held:
                if ( _finite(rsi2) and rsi2 > 70) or (_finite(trend) and close < trend) or age >= 10:
                    exits.add(symbol)
                else:
                    desired[symbol] = 0.495
            else:
                ready = current_idx > self.state.cooldown_until.get(symbol, -1)
                if ready and _finite(trend) and _finite(fast) and _finite(rsi2) and close > trend and fast > trend and rsi2 < threshold:
                    desired[symbol] = 0.495
        return desired, exits, set()

    def _s05(self, ctx: DecisionContext) -> tuple[dict[str, float], set[str], set[str]]:
        width = self._variant_number("band", 2.0)
        desired: dict[str, float] = {}
        exits: set[str] = set()
        idx = _session_index(ctx)
        for symbol in self.symbols:
            close, middle, std, trend = (_feature(ctx, symbol, key) for key in ("close", "sma20", "std20", "sma200"))
            lower = middle - width * std if _finite(middle) and _finite(std) else math.nan
            held = self._held_any(ctx, symbol)
            if held:
                age = _position_age(ctx, symbol, self.component_id)
                if (_finite(middle) and close > middle) or (_finite(trend) and close < trend) or age >= 15:
                    exits.add(symbol)
                    self.state.armed.pop(symbol, None)
                else:
                    desired[symbol] = 0.495
                continue
            arm = self.state.armed.get(symbol)
            if _finite(trend) and _finite(lower) and close < lower and close > trend:
                self.state.armed[symbol] = idx
                arm = idx
            if arm is not None:
                if idx - arm > 5 or (_finite(trend) and close < trend):
                    self.state.armed.pop(symbol, None)
                elif idx > arm and _finite(lower) and close > lower and _finite(trend) and close > trend:
                    desired[symbol] = 0.495
                    self.state.armed.pop(symbol, None)
        return desired, exits, set()

    def _s06(self, ctx: DecisionContext) -> tuple[dict[str, float], set[str], set[str]]:
        ratio_period = int(self._variant_number("ratio_sma", 20))
        exits = {symbol for symbol in self.symbols if self._held_any(ctx, symbol) and _finite(_feature(ctx, symbol, "sma100")) and _close(ctx, symbol) < _feature(ctx, symbol, "sma100")}
        for symbol in exits:
            self.state.target_weights.pop(symbol, None)
        if _review(ctx, "weekly"):
            q, s = "QQQM", self.semiconductor
            pair = _ratio_row(self, ctx)
            q_trend, s_trend = _feature(ctx, q, "sma100"), _feature(ctx, s, "sma100")
            ratio = _number(pair.get("ratio"), _close(ctx, s) / _close(ctx, q) if _close(ctx, q) > 0 else math.nan)
            ratio_sma = _number(pair.get("ratio_sma20"), math.nan)
            if not _finite(ratio_sma) and self.store is not None and ctx.information_cutoff is not None:
                # The v3 FeatureStore uses a 20-session ratio SMA.  A variant
                # request is handled by a direct causal lookup when possible.
                try:
                    frame = self.store.pair(q, self.semiconductor)
                    values = frame.loc[: pd.Timestamp(ctx.information_cutoff), "ratio"].tail(ratio_period)
                    ratio_sma = float(values.mean()) if len(values) >= ratio_period else math.nan
                except (KeyError, TypeError, ValueError):
                    ratio_sma = math.nan
            sector_leads = _finite(s_trend) and _close(ctx, s) > s_trend and _finite(ratio) and _finite(ratio_sma) and ratio > ratio_sma and _feature(ctx, s, "r63") > _feature(ctx, q, "r63")
            broad_ok = _finite(q_trend) and _close(ctx, q) > q_trend
            self.state.selected = s if sector_leads else q if broad_ok else None
            self.state.target_weights = {self.state.selected: 0.99} if self.state.selected else {}
            scheduled = set(self.state.target_weights)
        else:
            scheduled = set()
        desired = {symbol: weight for symbol, weight in self.state.target_weights.items() if symbol not in exits}
        return desired, exits, scheduled

    def _portfolio_vol(self, ctx: DecisionContext, weights: Mapping[str, float]) -> float:
        if not weights:
            return math.nan
        if len(weights) == 1:
            symbol = next(iter(weights))
            volatility = _feature(ctx, symbol, "vol60")
            return abs(float(weights[symbol])) * volatility if _finite(volatility) else math.nan
        a, b = self.symbols
        pair = _ratio_row(self, ctx)
        va, vb, cov = (_number(pair.get(key)) for key in ("var_a60", "var_b60", "cov60"))
        if not all(_finite(x) for x in (va, vb, cov)):
            va, vb = _feature(ctx, a, "vol60") ** 2, _feature(ctx, b, "vol60") ** 2
            cov = _number(pair.get("cov60"))
        if not all(_finite(x) for x in (va, vb, cov)):
            return math.nan
        wa, wb = weights.get(a, 0.0), weights.get(b, 0.0)
        return math.sqrt(max(0.0, wa * wa * va + wb * wb * vb + 2 * wa * wb * cov))

    def _s07(self, ctx: DecisionContext) -> tuple[dict[str, float], set[str], set[str]]:
        target = self._variant_number("vol_target", 0.25) / (100.0 if self.variant != "primary" else 1.0)
        exits = {symbol for symbol in self.symbols if self._held_any(ctx, symbol) and _finite(_feature(ctx, symbol, "sma200")) and _close(ctx, symbol) < _feature(ctx, symbol, "sma200")}
        for symbol in exits:
            self.state.target_weights.pop(symbol, None)
        if _review(ctx, "weekly"):
            base = {"QQQM": 0.40, self.semiconductor: 0.60}
            eligible = {symbol: weight for symbol, weight in base.items() if _finite(_feature(ctx, symbol, "sma200")) and _close(ctx, symbol) > _feature(ctx, symbol, "sma200")}
            vol = self._portfolio_vol(ctx, eligible)
            if eligible and (not _finite(vol) or vol < 0):
                # An invalid covariance/volatility estimate blocks increases;
                # preserve only already established eligible targets.
                self.state.target_weights = {
                    symbol: min(self.state.target_weights.get(symbol, 0.0), 0.99 * weight)
                    for symbol, weight in eligible.items()
                    if self.state.target_weights.get(symbol, 0.0) > 0
                }
            else:
                multiplier = min(1.0, target / vol) if _finite(vol) and vol > 0 else 1.0
                self.state.target_weights = {symbol: 0.99 * weight * multiplier for symbol, weight in eligible.items()}
            scheduled = set(self.state.target_weights)
        else:
            scheduled = set()
        desired = {symbol: weight for symbol, weight in self.state.target_weights.items() if symbol not in exits}
        return desired, exits, scheduled

    def _s08(self, ctx: DecisionContext) -> tuple[dict[str, float], set[str], set[str]]:
        q, s = "QQQM", self.semiconductor
        desired: dict[str, float] = {}
        exits: set[str] = set()
        qtrend = _feature(ctx, q, "sma200")
        if self._held_any(ctx, q) and (not _finite(qtrend) or _close(ctx, q) < qtrend):
            exits.add(q)
        elif self._held_any(ctx, q) or (_finite(qtrend) and _close(ctx, q) > qtrend):
            desired[q] = 0.594
        breakout = int(self._variant_number("breakout", 20))
        shigh, sr63, qr63 = _feature(ctx, s, f"hh{breakout}"), _feature(ctx, s, "r63"), _feature(ctx, q, "r63")
        if self._held_any(ctx, s):
            age = _position_age(ctx, s, self.component_id)
            if (_finite(_feature(ctx, s, "ema20")) and _close(ctx, s) < _feature(ctx, s, "ema20")) or age >= 30:
                exits.add(s)
            else:
                desired[s] = 0.396
        elif _finite(shigh) and _finite(sr63) and _finite(qr63) and _close(ctx, s) > shigh and sr63 > qr63:
            desired[s] = 0.396
        return desired, exits, set()

    def _s09(self, ctx: DecisionContext) -> tuple[dict[str, float], set[str], set[str]]:
        desired: dict[str, float] = {}
        exits: set[str] = set()
        idx = _session_index(ctx)
        for symbol in self.symbols:
            close = _close(ctx, symbol)
            if self._held_any(ctx, symbol):
                self.state.armed.pop(symbol, None)
                high = max(self.state.highest.get(symbol, close), close)
                self.state.highest[symbol] = high
                atr = _feature(ctx, symbol, "atr14")
                age = _position_age(ctx, symbol, self.component_id)
                if (_finite(atr) and close < high - 3 * atr) or age >= 40:
                    exits.add(symbol)
                    self.state.highest.pop(symbol, None)
                else:
                    desired[symbol] = 0.495
                continue
            if bool(_feature(ctx, symbol, "contraction", 0.0)) or (_finite(_feature(ctx, symbol, "bandwidth")) and _finite(_feature(ctx, symbol, "bandwidth_p20")) and _feature(ctx, symbol, "bandwidth") < _feature(ctx, symbol, "bandwidth_p20")):
                self.state.armed[symbol] = idx
            arm = self.state.armed.get(symbol)
            high20, trend = _feature(ctx, symbol, "hh20"), _feature(ctx, symbol, "sma100")
            if arm is not None and idx - arm > 10:
                self.state.armed.pop(symbol, None)
            elif arm is not None and idx > arm and idx - arm <= 10 and _finite(high20) and _finite(trend) and close > high20 and close > trend:
                desired[symbol] = 0.495
                # Keep the setup until a buy is actually filled.  A pending
                # or rejected order must not consume the contraction setup.
        return desired, exits, set()

    # ---- additional A candidates ----------------------------------------

    def _a01(self, ctx: DecisionContext) -> tuple[dict[str, float], set[str], set[str]]:
        buffer_value = self._variant_number("buffer", 0.03)
        horizon = 126
        exits: set[str] = set()
        selected = self.state.selected
        if selected and self._held_any(ctx, selected):
            trend, momentum = _feature(ctx, selected, "sma200"), _feature(ctx, selected, f"r{horizon}")
            # Equality retains the incumbent.  Missing indicators also do not
            # manufacture a liquidation signal; they only block new buys.
            if (_finite(trend) and _close(ctx, selected) < trend) or (_finite(momentum) and momentum < 0):
                exits.add(selected)
                self.state.selected = None
                self.state.target_weights.pop(selected, None)
                selected = None
        if _review(ctx, "weekly"):
            candidates: list[tuple[float, str]] = []
            for symbol in self.symbols:
                trend, r63, rm = _feature(ctx, symbol, "sma200"), _feature(ctx, symbol, "r63"), _feature(ctx, symbol, f"r{horizon}")
                if _finite(trend) and _finite(r63) and _finite(rm) and _close(ctx, symbol) > trend and rm > 0:
                    candidates.append((0.5 * r63 + 0.5 * rm, symbol))
            winner = max(candidates, key=lambda item: (item[0], 1 if item[1] == "QQQM" else 0))[1] if candidates else None
            if selected and winner and selected != winner:
                selected_score = next((score for score, symbol in candidates if symbol == selected), None)
                winner_score = next(score for score, symbol in candidates if symbol == winner)
                if selected_score is not None and winner_score <= selected_score + buffer_value:
                    winner = selected
            if winner != selected:
                if selected:
                    exits.add(selected)
                self.state.selected = winner
            self.state.target_weights = {self.state.selected: 0.99} if self.state.selected else {}
            scheduled = set(self.state.target_weights)
        else:
            scheduled = set()
        desired = {s: w for s, w in self.state.target_weights.items() if s not in exits}
        return desired, exits, scheduled

    def _a02(self, ctx: DecisionContext) -> tuple[dict[str, float], set[str], set[str]]:
        shortest = int(self._variant_number("sma", 50))
        exits: set[str] = set()
        if _review(ctx, "weekly"):
            targets: dict[str, float] = {}
            for symbol in self.symbols:
                votes = sum(1 for period in (shortest, 100, 200) if _finite(_feature(ctx, symbol, f"sma{period}")) and _close(ctx, symbol) > _feature(ctx, symbol, f"sma{period}"))
                if votes:
                    targets[symbol] = 0.495 * votes / 3
            self.state.target_weights = targets
            scheduled = set(targets)
        else:
            scheduled = set()
        for symbol in self.symbols:
            if self._held_any(ctx, symbol) and _finite(_feature(ctx, symbol, "sma200")) and _finite(_feature(ctx, symbol, "r21")) and _close(ctx, symbol) < _feature(ctx, symbol, "sma200") and _feature(ctx, symbol, "r21") < 0:
                exits.add(symbol)
        return {s: w for s, w in self.state.target_weights.items() if s not in exits}, exits, scheduled

    def _a03(self, ctx: DecisionContext) -> tuple[dict[str, float], set[str], set[str]]:
        lookback = int(self._variant_number("vol", 63))
        exits = {s for s in self.symbols if self._held_any(ctx, s) and _finite(_feature(ctx, s, "sma200")) and _close(ctx, s) < _feature(ctx, s, "sma200")}
        for symbol in exits:
            self.state.target_weights.pop(symbol, None)
        if _review(ctx, "monthly"):
            scores: dict[str, float] = {}
            for symbol in self.symbols:
                trend, r63, r126 = (_feature(ctx, symbol, k) for k in ("sma200", "r63", "r126"))
                vol = _feature(ctx, symbol, f"vol{lookback}") if lookback in (60, 63) else self._volatility_from_store(ctx, symbol, lookback)
                score = max(0, 0.5 * r63 + 0.5 * r126) / vol if _finite(trend) and _finite(r63) and _finite(r126) and _finite(vol) and vol > 0 and _close(ctx, symbol) > trend and r126 > 0 else 0
                if score > 0:
                    scores[symbol] = score
            total = sum(scores.values())
            self.state.target_weights = {s: 0.99 * score / total for s, score in scores.items()} if total > 0 else {}
            scheduled = set(self.state.target_weights)
        else:
            scheduled = set()
        return {s: w for s, w in self.state.target_weights.items() if s not in exits}, exits, scheduled

    def _volatility_from_store(self, ctx: DecisionContext, symbol: str, lookback: int) -> float:
        if self.store is None or ctx.information_cutoff is None:
            return math.nan
        try:
            values = self.store.frames[symbol].loc[: pd.Timestamp(ctx.information_cutoff), "r1"].dropna().tail(lookback)
            return float(values.std(ddof=1) * math.sqrt(252)) if len(values) >= lookback else math.nan
        except (KeyError, TypeError, ValueError):
            return math.nan

    def _a04(self, ctx: DecisionContext) -> tuple[dict[str, float], set[str], set[str]]:
        buffer_value = self._variant_number("buffer", 0.01)
        desired: dict[str, float] = {}
        exits: set[str] = set()
        for symbol in self.symbols:
            trend, close = _feature(ctx, symbol, "sma200"), _close(ctx, symbol)
            held = self._held_any(ctx, symbol)
            if not _finite(trend):
                continue
            if held:
                count = self.state.cooldown_until.get(f"down:{symbol}", 0)
                count = count + 1 if close < (1 - buffer_value) * trend else 0
                self.state.cooldown_until[f"down:{symbol}"] = count
                if count >= 2:
                    exits.add(symbol)
                    self.state.cooldown_until[f"down:{symbol}"] = 0
                else:
                    desired[symbol] = 0.495
            else:
                count = self.state.cooldown_until.get(f"up:{symbol}", 0)
                count = count + 1 if close > (1 + buffer_value) * trend else 0
                self.state.cooldown_until[f"up:{symbol}"] = count
                self.state.cooldown_until[f"down:{symbol}"] = 0
                if count >= 3 and not _recent_exit(ctx, symbol, self.component_id):
                    desired[symbol] = 0.495
        return desired, exits, set()

    def _a05(self, ctx: DecisionContext) -> tuple[dict[str, float], set[str], set[str]]:
        multiple = self._variant_number("atr", 3.0)
        desired: dict[str, float] = {}
        exits: set[str] = set()
        for symbol in self.symbols:
            close, high, low, trend, atr = (_feature(ctx, symbol, key) for key in ("close", "hh55", "ll20", "sma200", "atr14"))
            held = self._held_any(ctx, symbol)
            if held:
                highwater = max(self.state.highest.get(symbol, close), close)
                self.state.highest[symbol] = highwater
                if (_finite(low) and close < low) or (_finite(atr) and close < highwater - multiple * atr):
                    exits.add(symbol)
                    self.state.highest.pop(symbol, None)
                else:
                    desired[symbol] = self.state.target_weights.get(symbol, min(0.495, 0.015 / (multiple * atr / close))) if _finite(atr) and atr > 0 and close > 0 else 0.495
            elif _finite(high) and _finite(trend) and _finite(atr) and atr > 0 and close > high and close > trend:
                weight = min(0.495, 0.015 / (multiple * atr / close))
                if weight > 0:
                    self.state.target_weights[symbol] = weight
                    desired[symbol] = weight
        return desired, exits, set()

    def _a06(self, ctx: DecisionContext) -> tuple[dict[str, float], set[str], set[str]]:
        window = int(self._variant_number("confirm", 3))
        desired: dict[str, float] = {}
        exits: set[str] = set()
        idx = _session_index(ctx)
        for symbol in self.symbols:
            close, trend, fast, rsi2, prev_high = (_feature(ctx, symbol, key) for key in ("close", "sma200", "sma50", "rsi2", "prev_high"))
            held = self._held_any(ctx, symbol)
            age = _position_age(ctx, symbol, self.component_id)
            if _recent_exit(ctx, symbol, self.component_id):
                self.state.cooldown_until[symbol] = max(self.state.cooldown_until.get(symbol, -1), idx)
            if held:
                if (_finite(rsi2) and rsi2 > 70) or (_finite(fast) and close < fast) or age >= 10:
                    exits.add(symbol)
                    self.state.armed.pop(symbol, None)
                else:
                    desired[symbol] = 0.495
                continue
            trend_ok = _finite(trend) and _finite(fast) and close > trend and fast > trend
            if idx <= self.state.cooldown_until.get(symbol, -1):
                continue
            if trend_ok and _finite(rsi2) and rsi2 < 10 and symbol not in self.state.armed:
                self.state.armed[symbol] = idx
            arm = self.state.armed.get(symbol)
            if arm is not None:
                if not trend_ok or idx - arm > window:
                    self.state.armed.pop(symbol, None)
                elif idx > arm and _finite(prev_high) and close > prev_high:
                    desired[symbol] = 0.495
                    self.state.armed.pop(symbol, None)
        return desired, exits, set()

    def _a07(self, ctx: DecisionContext) -> tuple[dict[str, float], set[str], set[str]]:
        threshold = self._variant_number("corr", 0.85)
        exits = {s for s in self.symbols if self._held_any(ctx, s) and _finite(_feature(ctx, s, "sma200")) and _close(ctx, s) < _feature(ctx, s, "sma200")}
        for symbol in exits:
            self.state.target_weights.pop(symbol, None)
        if _review(ctx, "weekly"):
            eligible = {s: 0.495 for s in self.symbols if _finite(_feature(ctx, s, "sma200")) and _finite(_feature(ctx, s, "r63")) and _close(ctx, s) > _feature(ctx, s, "sma200") and _feature(ctx, s, "r63") > 0}
            if len(eligible) == 2:
                pair = _ratio_row(self, ctx)
                corr = _number(pair.get("corr60"))
                if _finite(corr) and corr > threshold:
                    weaker = min(eligible, key=lambda s: (_feature(ctx, s, "r63"), 1 if s == "QQQM" else 0))
                    eligible[weaker] *= 0.5
            self.state.target_weights = eligible
            scheduled = set(eligible)
        else:
            scheduled = set()
        return {s: w for s, w in self.state.target_weights.items() if s not in exits}, exits, scheduled

    def _a08(self, ctx: DecisionContext) -> tuple[dict[str, float], set[str], set[str]]:
        threshold = self._variant_number("er", 0.30)
        exits = {s for s in self.symbols if self._held_any(ctx, s) and _finite(_feature(ctx, s, "sma200")) and _close(ctx, s) < _feature(ctx, s, "sma200")}
        for symbol in exits:
            self.state.target_weights.pop(symbol, None)
        if _review(ctx, "weekly"):
            weights = {}
            for symbol in self.symbols:
                trend, momentum, er = _feature(ctx, symbol, "sma200"), _feature(ctx, symbol, "r63"), _feature(ctx, symbol, "er63")
                if _finite(trend) and _finite(momentum) and _finite(er) and _close(ctx, symbol) > trend and momentum > 0 and threshold > 0:
                    weights[symbol] = 0.495 * min(1.0, er / threshold)
            self.state.target_weights = {s: w for s, w in weights.items() if w > 0}
            scheduled = set(self.state.target_weights)
        else:
            scheduled = set()
        return {s: w for s, w in self.state.target_weights.items() if s not in exits}, exits, scheduled

    def _ensemble(self, ctx: DecisionContext, trend_share: float, reason: str) -> tuple[dict[str, float], set[str], set[str]]:
        assert self._trend is not None and self._pullback is not None
        trend, trend_exits, trend_review = self._trend._base_plan(ctx)
        pullback, pullback_exits, pullback_review = self._pullback._base_plan(ctx)
        # Child plans return standalone weights.  Component budgets are
        # account weights: S01 0.70*0.99 and S04 0.30*(0.495+0.495).
        combined: dict[str, float] = {}
        for symbol, weight in trend.items():
            combined[f"S01:{symbol}"] = trend_share * weight
        for symbol, weight in pullback.items():
            combined[f"S04:{symbol}"] = (1.0 - trend_share) * weight
        exits = {f"S01:{symbol}" for symbol in trend_exits} | {f"S04:{symbol}" for symbol in pullback_exits}
        scheduled = {f"S01:{symbol}" for symbol in trend_review} | {f"S04:{symbol}" for symbol in pullback_review}
        # Use component-qualified keys internally; decide() maps them to their
        # component IDs and symbols.  This keeps shared cash and virtual
        # ownership explicit in the engine.
        self.state.target_weights = dict(combined)
        return combined, exits, scheduled

    def _a09(self, ctx: DecisionContext) -> tuple[dict[str, float], set[str], set[str]]:
        assert self._trend is not None and self._pullback is not None
        shadow_dd = self._shadow_drawdown(ctx)
        scale = 1.0 if shadow_dd < 0.08 else 0.75 if shadow_dd < 0.15 else 0.50 if shadow_dd < 0.25 else 0.25
        shadow_weights, shadow_exits = self._shadow_snapshot(ctx)
        if _review(ctx, "weekly"):
            self.state.scale = scale
            # Mirror the independently simulated shadow account's actual
            # component quantities at the prior-close cutoff.  Do not infer
            # shadow ownership from the traded A09 account's fills.
            self.state.target_weights = {key: scale * weight for key, weight in shadow_weights.items()}
            scheduled = set(self.state.target_weights)
        else:
            # Daily component exits are mirrored.  Increases and throttle
            # changes wait for the weekly rebalance.
            scheduled = set()
        for key in shadow_exits:
            self.state.target_weights.pop(key, None)
        exits = set(shadow_exits)
        return dict(self.state.target_weights), exits, scheduled

    def _shadow_equity(self, ctx: DecisionContext) -> float:
        if self.shadow_result is None or ctx.information_cutoff is None:
            return 0.0
        equity = self.shadow_result.equity.copy()
        equity["date"] = pd.to_datetime(equity["date"], utc=True)
        rows = equity[equity.date <= pd.Timestamp(ctx.information_cutoff)]
        return float(rows.equity.iloc[-1]) if not rows.empty else 0.0

    def _shadow_snapshot(self, ctx: DecisionContext) -> tuple[dict[str, float], set[str]]:
        """Return actual shadow component weights and explicit shadow exits.

        The component-position ledger is intentionally used only for the
        prior-close weights.  A shadow daily exit is a *decision made this
        morning* from the previous close, so waiting for a zero quantity in a
        later ledger row would lag the traded account by one session.  The
        engine records each intent in ``signals``; that is the preferred
        source for same-session exits.  Older/fixture results may not contain
        intent-level signal rows, so the decision-session sell order is a
        causal compatibility fallback.
        """

        if self.shadow_result is None or ctx.information_cutoff is None:
            return {}, set()
        try:
            ledger = self.shadow_result.component_ledger.copy()
            if ledger.empty:
                return {}, set()
            ledger["date"] = pd.to_datetime(ledger["date"], utc=True)
            rows = ledger[(ledger["kind"] == "component_position") & (ledger.date <= pd.Timestamp(ctx.information_cutoff))]
            if rows.empty:
                return {}, set()
            rows = rows[rows.date == rows.date.max()]
            shadow_equity = self._shadow_equity(ctx)
            if shadow_equity <= 0:
                return {}, set()
            all_keys = {f"{component}:{symbol}" for component in ("S01", "S04") for symbol in self.symbols}
            weights: dict[str, float] = {}
            exits: set[str] = set()
            for row in rows.to_dict("records"):
                component, symbol = str(row.get("component_id", "")), str(row.get("symbol", ""))
                key = f"{component}:{symbol}"
                if key not in all_keys:
                    continue
                quantity = _number(row.get("quantity"), 0.0)
                if quantity > 0 and _finite(ctx.prior_close.get(symbol)):
                    weights[key] = quantity * float(ctx.prior_close[symbol]) / shadow_equity
                else:
                    exits.add(key)
            # Missing rows for a component/symbol at the latest ledger date
            # mean that the shadow has no position there.
            seen = {f"{row.get('component_id')}:{row.get('symbol')}" for row in rows.to_dict("records")}
            exits.update(all_keys - seen)

            # Mirror an exit generated by the independent shadow on this
            # execution morning.  The signal's information_cutoff is the
            # preceding session by construction; no current/future bar is
            # consulted here.  Keep this independent of whether a delayed
            # order eventually fills in the shadow result.
            current = pd.Timestamp(ctx.execution_session)
            current_utc = current.tz_localize("UTC") if current.tzinfo is None else current.tz_convert("UTC")
            signal_frame = getattr(self.shadow_result, "signals", None)
            signal_exit_seen = False
            if isinstance(signal_frame, pd.DataFrame) and not signal_frame.empty:
                signal_rows = signal_frame.copy()
                session_column = next((column for column in ("decision_session", "execution_session", "date") if column in signal_rows.columns), None)
                action_column = next((column for column in ("action", "intent_mode", "mode") if column in signal_rows.columns), None)
                if session_column is not None and action_column is not None:
                    timestamps = pd.to_datetime(signal_rows[session_column], utc=True, errors="coerce")
                    actions = signal_rows[action_column].astype(str).str.lower()
                    current_rows = signal_rows[(timestamps == current_utc) & actions.isin({SizingMode.EXIT_FULLY.value, SizingMode.REDUCE.value, "exit", "reduce"})]
                    for row in current_rows.to_dict("records"):
                        component, symbol = str(row.get("component_id", "")), str(row.get("symbol", ""))
                        key = f"{component}:{symbol}"
                        if key in all_keys:
                            exits.add(key)
                            signal_exit_seen = True

            # Compatibility with result objects produced before intent-level
            # signal rows were added.  Orders are filtered by their original
            # decision session, rather than their eventual execution/fill
            # date, so execution delay cannot make an exit late.
            if not signal_exit_seen:
                order_frame = getattr(self.shadow_result, "orders", None)
                if isinstance(order_frame, pd.DataFrame) and not order_frame.empty:
                    order_rows = order_frame.copy()
                    session_column = next((column for column in ("decision_session", "date") if column in order_rows.columns), None)
                    if session_column is not None and "side" in order_rows.columns:
                        timestamps = pd.to_datetime(order_rows[session_column], utc=True, errors="coerce")
                        sell_rows = order_rows[(timestamps == current_utc) & (order_rows["side"].astype(str).str.lower() == "sell")]
                        for row in sell_rows.to_dict("records"):
                            component, symbol = str(row.get("component_id", "")), str(row.get("symbol", ""))
                            key = f"{component}:{symbol}"
                            status = str(row.get("status", "")).lower()
                            if key in all_keys and status not in {"cancelled", "rejected"}:
                                exits.add(key)
            return weights, exits
        except (AttributeError, KeyError, TypeError, ValueError):
            return {}, set()

    def _shadow_drawdown(self, ctx: DecisionContext) -> float:
        if self.shadow_result is None or ctx.information_cutoff is None:
            return 0.0
        try:
            equity = self.shadow_result.equity.copy()
            equity["date"] = pd.to_datetime(equity["date"], utc=True)
            rows = equity[equity.date <= pd.Timestamp(ctx.information_cutoff)]
            if rows.empty:
                return 0.0
            value = float(rows.equity.iloc[-1])
            peak = max(1000.0, float(rows.equity.cummax().max()))
            return max(0.0, 1.0 - value / peak) if peak > 0 else 0.0
        except (AttributeError, KeyError, TypeError, ValueError):
            return 0.0

    def _a10(self, ctx: DecisionContext) -> tuple[dict[str, float], set[str], set[str]]:
        assert self._trend is not None and self._pullback is not None
        trend, trend_exits, trend_review = self._trend._base_plan(ctx)
        pullback, pullback_exits, pullback_review = self._pullback._base_plan(ctx)
        configured = self._variant_number("trend_share", 0.80)
        budget_changed = False
        if _review(ctx, "weekly"):
            strong = all(_finite(_feature(ctx, s, "sma200")) and _finite(_feature(ctx, s, "sma50")) and _finite(_feature(ctx, s, "sma50_lag20")) and _close(ctx, s) > _feature(ctx, s, "sma200") and _feature(ctx, s, "sma50") > _feature(ctx, s, "sma50_lag20") for s in self.symbols)
            new_share = configured if strong else 0.50
            budget_changed = not math.isclose(new_share, self.state.trend_share, rel_tol=0.0, abs_tol=1e-12)
            self.state.trend_share = new_share
        trend_share = self.state.trend_share
        combined = {**{f"S01:{s}": trend_share * w for s, w in trend.items()}, **{f"S04:{s}": (1 - trend_share) * w for s, w in pullback.items()}}
        exits = {f"S01:{s}" for s in trend_exits} | {f"S04:{s}" for s in pullback_exits}
        scheduled = (
            (set(combined) if budget_changed else set())
            | {f"S01:{s}" for s in trend_review}
            | {f"S04:{s}" for s in pullback_review}
        )
        self.state.target_weights.update(combined)
        for key in exits:
            self.state.target_weights.pop(key, None)
        return dict(self.state.target_weights), exits, scheduled

    def _a11(self, ctx: DecisionContext) -> tuple[dict[str, float], set[str], set[str]]:
        q, s = "QQQM", "SMH"
        exits = {symbol for symbol in (q, s) if self._held_any(ctx, symbol) and _finite(_feature(ctx, symbol, "sma200")) and _close(ctx, symbol) < _feature(ctx, symbol, "sma200")}
        for symbol in exits:
            self.state.target_weights.pop(symbol, None)
        if _review(ctx, "weekly"):
            q_ok = _finite(_feature(ctx, q, "sma200")) and _close(ctx, q) > _feature(ctx, q, "sma200")
            pair = _ratio_row(self, ctx)
            ratio = _number(pair.get("ratio"), _close(ctx, s) / _close(ctx, q) if _close(ctx, q) > 0 else math.nan)
            ratio_sma = _number(pair.get("ratio_sma20"))
            s_ok = _finite(_feature(ctx, s, "sma200")) and _close(ctx, s) > _feature(ctx, s, "sma200") and _finite(ratio) and _finite(ratio_sma) and ratio > ratio_sma and _feature(ctx, s, "r63") > _feature(ctx, q, "r63")
            weights: dict[str, float] = {}
            if q_ok and s_ok:
                q_vol, s_vol = _feature(ctx, q, "vol63"), _feature(ctx, s, "vol63")
                if _finite(q_vol) and _finite(s_vol) and q_vol > 0 and s_vol > 0:
                    q_score = max(0.0, 0.5 * _feature(ctx, q, "r63") + 0.5 * _feature(ctx, q, "r126")) / q_vol
                    s_score = max(0.0, 0.5 * _feature(ctx, s, "r63") + 0.5 * _feature(ctx, s, "r126")) / s_vol
                    if q_score == 0 and s_score == 0:
                        weights = {q: 0.99}
                    else:
                        total = q_score + s_score
                        s_weight = min(0.495, 0.99 * s_score / total) if total > 0 else 0.0
                        weights = {q: 0.99 - s_weight, s: s_weight} if s_weight > 0 else {q: 0.99}
            elif q_ok:
                weights = {q: 0.99}
            elif s_ok:
                weights = {s: 0.495}
            self.state.target_weights = weights
            scheduled = set(weights)
        else:
            scheduled = set()
        return {s: w for s, w in self.state.target_weights.items() if s not in exits}, exits, scheduled

    def decide(self, context: DecisionContext) -> list[OrderIntent]:  # type: ignore[override]
        """Public decision method with ensemble key translation."""

        if context.information_cutoff is None or not context.prior_close:
            return []
        desired, exits, scheduled = self._base_plan(context)
        intents: list[OrderIntent] = []
        # For ensemble internals, keys are ``COMPONENT:SYMBOL``.  Ordinary
        # candidates use the configured account component.
        def decode(key: str) -> tuple[str, str]:
            if ":" in key:
                return tuple(key.split(":", 1))  # type: ignore[return-value]
            return self.component_id, key

        for key in sorted(exits):
            component, symbol = decode(key)
            if _quantity(context, symbol, component) or _has_pending(context, symbol, component):
                intents.append(_exit_intent(symbol, component, f"{self.strategy_id}_EXIT"))
        for key, weight in sorted(desired.items()):
            component, symbol = decode(key)
            if weight <= 0 or key in exits:
                continue
            is_scheduled = key in scheduled
            if is_scheduled:
                # A scheduled purchase may be rejected or expire.  It may be
                # reconsidered at the next scheduled review, but never turns
                # into an unscheduled daily retry.
                self.state.cooldown_until.pop(f"block:{key}", None)
                intents.append(_target_intent(symbol, weight, component, f"{self.strategy_id}_REVIEW", rebalance=True))
                if not _quantity(context, symbol, component) and not _has_pending(context, symbol, component, "buy"):
                    self.state.cooldown_until[f"block:{key}"] = _session_index(context)
            elif not _quantity(context, symbol, component) and not _has_pending(context, symbol, component, "buy") and f"block:{key}" not in self.state.cooldown_until:
                intents.append(_target_intent(symbol, weight, component, f"{self.strategy_id}_ENTRY"))
        return intents


def make_sa_strategy(candidate: Any, *, store: Any | None = None, data: Any | None = None, config: EngineConfig | None = None, start: str | date | pd.Timestamp | None = None, end: str | date | pd.Timestamp | None = None) -> SAEngineStrategy:
    """Build a strategy from a v3 ``CandidateSpec``.

    A09 prepares an independent unthrottled S10 shadow account using the same
    frozen period, data and execution settings.  The returned strategy exposes
    that ``shadow_result`` for the study writer; it never submits an account
    or broker order.
    """

    sid = str(candidate.strategy_id)
    symbols = tuple(candidate.universe.tradable_symbols)
    semiconductor = symbols[1] if sid.startswith("S") else "SMH"
    strategy = SAEngineStrategy(sid, semiconductor, str(getattr(candidate, "variant", "primary")), store=store)
    if sid == "A09" and data is not None and config is not None:
        from .engine_v3 import run_engine_v3

        shadow = SAEngineStrategy("S10", semiconductor, store=store)
        strategy.shadow_result = run_engine_v3(
            data.bars,
            strategy=shadow,
            config=config,
            calendar=getattr(data, "calendar", None),
            tradable_symbols=symbols,
            start=start,
            end=end,
            candidate_id=f"{candidate.candidate_id}__SHADOW_S10",
        )
    return strategy


# Short aliases make the module convenient for fixture tests and for the v3
# orchestration layer which imports a family-specific factory.
make_strategy = make_sa_strategy


__all__ = ["SAEngineStrategy", "make_sa_strategy", "make_strategy"]
