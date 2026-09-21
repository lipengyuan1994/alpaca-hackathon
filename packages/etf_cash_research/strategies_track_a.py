"""Pure target-weight engines for the second QQQM + SMH study.

The module deliberately has no dependency on the simulator or on broker
clients.  A caller supplies point-in-time bars (normally through the prior
completed close), an index into the QQQM frame, and optional strategy state.
The returned :class:`TrackSignal` is an intent: the cash simulator remains
responsible for affordability, settlement, fills, and actual position state.

The public integration surface is:

``build_track_a(strategy_id, symbols=("QQQM", "SMH"), variant="primary")``

The resulting object exposes ``evaluate(frames, index, state=None)``.  Frames
are mappings from symbol to DataFrame with at least ``date``, ``high``,
``low`` and ``close`` columns.  Dates are normalized to UTC and only rows at
or before the QQQM row at ``index`` are used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .indicators import atr, returns, rsi, sma

_DEFAULT_SYMBOLS = ("QQQM", "SMH")
_STRATEGY_IDS = tuple(f"A{number:02d}" for number in range(1, 11))


@dataclass(frozen=True)
class TrackSignal:
    """A deterministic target-weight decision at one information cutoff."""

    asof: pd.Timestamp
    target_weights: dict[str, float]
    reason_code: str
    entries: tuple[str, ...] = ()
    exits: tuple[str, ...] = ()
    component_target_weights: dict[str, dict[str, float]] = field(default_factory=dict)
    shadow_target_weights: dict[str, float] = field(default_factory=dict)


@dataclass
class TrackAState:
    """Mutable per-candidate state supplied back on the next evaluation.

    The generic dictionaries keep the state serializable and allow the cash
    simulator to restore state without depending on implementation details.
    ``active`` means the strategy currently intends to hold a sleeve; it is
    not evidence that a broker fill occurred.
    """

    active: dict[str, bool] = field(default_factory=dict)
    entry_index: dict[str, int] = field(default_factory=dict)
    entry_weight: dict[str, float] = field(default_factory=dict)
    arm_index: dict[str, int] = field(default_factory=dict)
    highest_close: dict[str, float] = field(default_factory=dict)
    cooldown_until: dict[str, int] = field(default_factory=dict)
    streak_up: dict[str, int] = field(default_factory=dict)
    streak_down: dict[str, int] = field(default_factory=dict)
    selected: str | None = None
    previous_week: tuple[int, int] | None = None
    previous_month: tuple[int, int] | None = None
    last_weights: dict[str, float] = field(default_factory=dict)
    shadow_drawdown: float = 0.0
    component_states: dict[str, "TrackAState"] = field(default_factory=dict)


# Alias retained for callers that use the shorter state name.
StrategyState = TrackAState


class _BaselineStrategy:
    """Private implementation of the original S01/S04 components.

    A09 and A10 intentionally use the frozen first-study components rather
    than A01/A06.  Keeping these two small engines here avoids coupling Track
    A to the original simulator-facing strategy module while preserving the
    exact monthly S01 and daily S04 behavior.
    """

    def __init__(self, strategy_id: str, symbols: tuple[str, ...], state: TrackAState) -> None:
        self.strategy_id = strategy_id
        self.symbols = symbols
        self.state = state
        self._prepared_frames: dict[str, pd.DataFrame] | None = None
        for symbol in symbols:
            state.active.setdefault(symbol, False)

    def evaluate(
        self,
        frames: Mapping[str, pd.DataFrame],
        index: int,
        state: TrackAState | None = None,
        context=None,
    ) -> TrackSignal:
        if state is not None:
            self.state = state
        if self._prepared_frames is None or any(self._prepared_frames.get(symbol) is not frames.get(symbol) for symbol in self.symbols):
            self._prepared_frames = _clean_frames(frames, self.symbols)
        prepared = self._prepared_frames
        asof = _asof(prepared, index, self.symbols[0])
        if asof is None or any(symbol not in prepared for symbol in self.symbols):
            return TrackSignal(pd.Timestamp("1970-01-01", tz="UTC"), {}, "MISSING_SYMBOL")
        histories = {symbol: _history(prepared, symbol, asof) for symbol in self.symbols}
        if any(history.empty for history in histories.values()):
            return TrackSignal(asof, {}, "MISSING_SESSION")
        if self.strategy_id == "S01":
            return self._s01(histories, asof)
        return self._s04(histories, asof, index)

    def _s01(self, histories: Mapping[str, pd.DataFrame], asof: pd.Timestamp) -> TrackSignal:
        exits: list[str] = []
        selected = self.state.selected
        for symbol in self.symbols:
            history = histories[symbol]
            trend = sma(history["close"], 200).iloc[-1]
            if (
                self.state.active.get(symbol, False)
                and _valid(trend)
                and _close(history) < float(trend)
            ):
                self.state.active[symbol] = False
                if selected == symbol:
                    selected = None
                exits.append(symbol)
        if _is_review(histories[self.symbols[0]], "monthly"):
            candidates: list[tuple[float, str]] = []
            for symbol in self.symbols:
                history = histories[symbol]
                trend = sma(history["close"], 200).iloc[-1]
                r63 = returns(history["close"], 63).iloc[-1]
                r126 = returns(history["close"], 126).iloc[-1]
                if (
                    _valid(trend)
                    and _valid(r63)
                    and _valid(r126)
                    and _close(history) > float(trend)
                    and float(r126) > 0.0
                ):
                    candidates.append((0.5 * float(r63) + 0.5 * float(r126), symbol))
            winner = (
                max(candidates, key=lambda item: (item[0], 1 if item[1] == "QQQM" else 0))[1]
                if candidates
                else None
            )
            if winner != selected:
                if selected:
                    self.state.active[selected] = False
                    exits.append(selected)
                if winner:
                    self.state.active[winner] = True
                selected = winner
            self.state.selected = selected
        weights = {selected: 0.99} if selected and self.state.active.get(selected, False) else {}
        self.state.last_weights = dict(weights)
        return _signal(asof, self.symbols, weights, "S01_FROZEN_COMPONENT", list(weights), exits)

    def _s04(
        self, histories: Mapping[str, pd.DataFrame], asof: pd.Timestamp, index: int
    ) -> TrackSignal:
        entries: list[str] = []
        exits: list[str] = []
        weights: dict[str, float] = {}
        for symbol in self.symbols:
            history = histories[symbol]
            close = _close(history)
            trend = sma(history["close"], 200).iloc[-1]
            fast = sma(history["close"], 50).iloc[-1]
            value = rsi(history["close"], 2).iloc[-1]
            held = self.state.active.get(symbol, False)
            if held:
                age = index - self.state.entry_index.get(symbol, index) + 1
                if (
                    (_valid(value) and float(value) > 70.0)
                    or (_valid(trend) and close < float(trend))
                    or age >= 10
                ):
                    self.state.active[symbol] = False
                    self.state.cooldown_until[symbol] = index + 1
                    exits.append(symbol)
            else:
                ready = index > self.state.cooldown_until.get(symbol, -1)
                if (
                    ready
                    and _valid(trend)
                    and _valid(fast)
                    and _valid(value)
                    and close > float(trend)
                    and float(fast) > float(trend)
                    and float(value) < 10.0
                ):
                    self.state.active[symbol] = True
                    self.state.entry_index[symbol] = index
                    entries.append(symbol)
            if self.state.active.get(symbol, False):
                weights[symbol] = 0.495
        self.state.last_weights = dict(weights)
        return _signal(asof, self.symbols, weights, "S04_FROZEN_COMPONENT", entries, exits)


def _frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.attrs.get("track_a_normalized"):
        return frame
    result = frame.copy()
    if "date" not in result:
        raise ValueError("TRACK_A_DATE_COLUMN_MISSING")
    result["date"] = pd.to_datetime(result["date"], utc=True).dt.normalize()
    for column in ("open", "high", "low", "close", "volume"):
        if column in result.columns:
            result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.sort_values("date", kind="stable").reset_index(drop=True)
    result.attrs["track_a_normalized"] = True
    return result


def _valid(value: Any) -> bool:
    if value is None or pd.isna(value):
        return False
    try:
        return bool(np.isfinite(float(value)))
    except (TypeError, ValueError):
        return False


def _clean_frames(
    frames: Mapping[str, pd.DataFrame], symbols: tuple[str, ...]
) -> dict[str, pd.DataFrame]:
    return {symbol: _frame(frames[symbol]) for symbol in symbols if symbol in frames}


def _asof(frames: Mapping[str, pd.DataFrame], index: int, base_symbol: str) -> pd.Timestamp | None:
    if base_symbol not in frames:
        return None
    prepared = _frame(frames[base_symbol])
    if index < 0 or index >= len(prepared):
        return None
    return pd.Timestamp(prepared.iloc[index]["date"])


def _history(frames: Mapping[str, pd.DataFrame], symbol: str, asof: pd.Timestamp) -> pd.DataFrame:
    if symbol not in frames:
        return pd.DataFrame()
    prepared = _frame(frames[symbol])
    return prepared.loc[prepared["date"] <= asof].reset_index(drop=True)


def _is_review(history: pd.DataFrame, kind: str) -> bool:
    if history.empty:
        return False
    current = pd.Timestamp(history.iloc[-1]["date"])
    if len(history) == 1:
        return True
    previous = pd.Timestamp(history.iloc[-2]["date"])
    if kind == "monthly":
        return (current.year, current.month) != (previous.year, previous.month)
    iso_current = current.isocalendar()
    iso_previous = previous.isocalendar()
    return (iso_current.year, iso_current.week) != (iso_previous.year, iso_previous.week)


def _close(history: pd.DataFrame) -> float:
    return float(history.iloc[-1]["close"]) if not history.empty else float("nan")


def _signal(
    asof: pd.Timestamp,
    symbols: tuple[str, ...],
    weights: Mapping[str, float],
    reason: str,
    entries: list[str] | tuple[str, ...] = (),
    exits: list[str] | tuple[str, ...] = (),
    component_target_weights: Mapping[str, Mapping[str, float]] | None = None,
    shadow_target_weights: Mapping[str, float] | None = None,
) -> TrackSignal:
    clean = {
        symbol: min(0.99, max(0.0, float(weights[symbol])))
        for symbol in symbols
        if symbol in weights and _valid(weights[symbol]) and float(weights[symbol]) > 0.0
    }
    # Keep event order deterministic while suppressing duplicate component
    # events produced by an ensemble.
    unique_entries = tuple(dict.fromkeys(str(item) for item in entries))
    unique_exits = tuple(dict.fromkeys(str(item) for item in exits))
    components = {
        str(component): {
            str(symbol): min(0.99, max(0.0, float(weight)))
            for symbol, weight in weights.items()
            if _valid(weight) and float(weight) > 0.0
        }
        for component, weights in (component_target_weights or {}).items()
    }
    shadow = {str(symbol): min(0.99, max(0.0, float(weight))) for symbol, weight in (shadow_target_weights or {}).items() if _valid(weight) and float(weight) > 0.0}
    return TrackSignal(pd.Timestamp(asof), clean, reason, unique_entries, unique_exits, components, shadow)


def _parse_variant(variant: str, default: float, prefixes: tuple[str, ...], cast=float) -> float:
    if variant == "primary":
        return default
    for prefix in prefixes:
        if variant.startswith(prefix):
            value = variant[len(prefix) :].lstrip("_=")
            if value:
                return cast(value)
    raise ValueError(f"TRACK_A_VARIANT_INVALID:{variant}")


def _current_drawdown(value: Any) -> float:
    """Normalize either +10% or -10% drawdown conventions to 0.10."""

    if not _valid(value):
        return 0.0
    number = float(value)
    return abs(number) if number < 0.0 else number


class TrackAStrategy:
    """Evaluate one of A01--A10 on QQQM and SMH historical bars."""

    def __init__(
        self,
        strategy_id: str,
        symbols: tuple[str, ...] = _DEFAULT_SYMBOLS,
        variant: str = "primary",
        state: TrackAState | None = None,
    ) -> None:
        if strategy_id not in _STRATEGY_IDS:
            raise ValueError(f"TRACK_A_STRATEGY_UNKNOWN:{strategy_id}")
        if len(symbols) != 2 or symbols[0] != "QQQM":
            raise ValueError("TRACK_A_SYMBOLS_INVALID")
        if any(not str(symbol).strip() for symbol in symbols):
            raise ValueError("TRACK_A_SYMBOLS_INVALID")
        self.strategy_id = strategy_id
        self.symbols = tuple(str(symbol).upper() for symbol in symbols)
        self.variant = variant
        self.state = state or TrackAState(active={symbol: False for symbol in self.symbols})
        self._prepared_frames: dict[str, pd.DataFrame] | None = None
        for symbol in self.symbols:
            self.state.active.setdefault(symbol, False)
        self._components: dict[str, Any] = {}
        if strategy_id in {"A09", "A10"}:
            self._components["trend"] = _BaselineStrategy(
                "S01", self.symbols, self.state.component_states.setdefault("trend", TrackAState())
            )
            self._components["pullback"] = _BaselineStrategy(
                "S04",
                self.symbols,
                self.state.component_states.setdefault("pullback", TrackAState()),
            )

    def evaluate(
        self,
        frames: Mapping[str, pd.DataFrame],
        index: int,
        state: TrackAState | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> TrackSignal:
        """Return a target-weight signal using data through ``index`` only.

        ``state`` is optional.  When provided it becomes the active state for
        this call and is mutated in place, which lets a simulator own durable
        state.  ``context`` is used only by A09; it may include
        ``shadow_drawdown`` or ``shadow_equity`` and ``shadow_peak_equity``.
        """

        if state is not None and state is not self.state:
            self.state = state
            for symbol in self.symbols:
                self.state.active.setdefault(symbol, False)
            if self.strategy_id in {"A09", "A10"}:
                self._components["trend"] = _BaselineStrategy(
                    "S01",
                    self.symbols,
                    self.state.component_states.setdefault("trend", TrackAState()),
                )
                self._components["pullback"] = _BaselineStrategy(
                    "S04",
                    self.symbols,
                    self.state.component_states.setdefault("pullback", TrackAState()),
                )

        if self._prepared_frames is None or any(self._prepared_frames.get(symbol) is not frames.get(symbol) for symbol in self.symbols):
            self._prepared_frames = _clean_frames(frames, self.symbols)
        prepared = self._prepared_frames
        if any(symbol not in prepared for symbol in self.symbols):
            return TrackSignal(pd.Timestamp("1970-01-01", tz="UTC"), {}, "MISSING_SYMBOL")
        asof = _asof(prepared, index, self.symbols[0])
        if asof is None:
            return TrackSignal(pd.Timestamp("1970-01-01", tz="UTC"), {}, "MISSING_SESSION")
        histories = {symbol: _history(prepared, symbol, asof) for symbol in self.symbols}
        if any(history.empty for history in histories.values()):
            return TrackSignal(asof, {}, "MISSING_SESSION")
        context = context or {}
        method = getattr(self, f"_{self.strategy_id.lower()}")
        return method(prepared, histories, asof, index, context)

    def _a01(self, frames, histories, asof, index, context) -> TrackSignal:
        horizon = (
            int(_parse_variant(self.variant, 126, ("momentum", "horizon"), int))
            if self.variant != "primary" and self.variant.startswith(("momentum", "horizon"))
            else 126
        )
        switch_buffer = (
            _parse_variant(self.variant, 0.03, ("buffer", "switch"), float)
            if self.variant != "primary" and self.variant.startswith(("buffer", "switch"))
            else 0.03
        )
        weekly = _is_review(histories[self.symbols[0]], "weekly")
        entries: list[str] = []
        exits: list[str] = []
        selected = self.state.selected
        # A held incumbent can leave only when it ceases to be eligible.  A
        # replacement is considered at the next weekly review.
        if selected:
            history = histories[selected]
            trend = sma(history["close"], 200).iloc[-1]
            momentum = returns(history["close"], horizon).iloc[-1]
            eligible = (
                _valid(trend)
                and _valid(momentum)
                and _close(history) > float(trend)
                and float(momentum) > 0.0
            )
            if not eligible and self.state.active.get(selected, False):
                self.state.active[selected] = False
                self.state.selected = None
                exits.append(selected)
                selected = None
        if weekly:
            candidates: list[tuple[float, str]] = []
            for symbol in self.symbols:
                history = histories[symbol]
                trend = sma(history["close"], 200).iloc[-1]
                r63 = returns(history["close"], 63).iloc[-1]
                rm = returns(history["close"], horizon).iloc[-1]
                if (
                    _valid(trend)
                    and _valid(r63)
                    and _valid(rm)
                    and _close(history) > float(trend)
                    and float(rm) > 0.0
                ):
                    candidates.append((0.5 * float(r63) + 0.5 * float(rm), symbol))
            winner = (
                max(candidates, key=lambda item: (item[0], 1 if item[1] == "QQQM" else 0))[1]
                if candidates
                else None
            )
            if selected and winner and selected != winner:
                selected_score = next(
                    (score for score, symbol in candidates if symbol == selected), None
                )
                winner_score = next(score for score, symbol in candidates if symbol == winner)
                if selected_score is not None and winner_score <= selected_score + switch_buffer:
                    winner = selected
            if winner != selected:
                if selected:
                    self.state.active[selected] = False
                    exits.append(selected)
                if winner:
                    self.state.active[winner] = True
                    entries.append(winner)
                self.state.selected = winner
                selected = winner
        weights = {selected: 0.99} if selected and self.state.active.get(selected, False) else {}
        self.state.last_weights = dict(weights)
        return _signal(asof, self.symbols, weights, "A01_BUFFERED_MOMENTUM_WEEKLY", entries, exits)

    def _a02(self, frames, histories, asof, index, context) -> TrackSignal:
        shortest = int(_parse_variant(self.variant, 50, ("sma", "shortest"), int))
        weekly = _is_review(histories[self.symbols[0]], "weekly")
        entries: list[str] = []
        exits: list[str] = []
        if weekly:
            weights: dict[str, float] = {}
            for symbol in self.symbols:
                history = histories[symbol]
                votes = 0
                for period in (shortest, 100, 200):
                    average = sma(history["close"], period).iloc[-1]
                    if _valid(average) and _close(history) > float(average):
                        votes += 1
                target = 0.495 * votes / 3.0
                if target > 0.0:
                    weights[symbol] = target
                    if not self.state.active.get(symbol, False):
                        entries.append(symbol)
                    self.state.active[symbol] = True
                else:
                    if self.state.active.get(symbol, False):
                        exits.append(symbol)
                    self.state.active[symbol] = False
            self.state.last_weights = dict(weights)
        else:
            weights = dict(self.state.last_weights)
        for symbol in self.symbols:
            history = histories[symbol]
            trend = sma(history["close"], 200).iloc[-1]
            recent = returns(history["close"], 21).iloc[-1]
            if (
                self.state.active.get(symbol, False)
                and _valid(trend)
                and _valid(recent)
                and _close(history) < float(trend)
                and float(recent) < 0.0
            ):
                self.state.active[symbol] = False
                weights.pop(symbol, None)
                if symbol not in exits:
                    exits.append(symbol)
        self.state.last_weights = dict(weights)
        return _signal(asof, self.symbols, weights, "A02_TREND_VOTE_WEEKLY", entries, exits)

    def _a03(self, frames, histories, asof, index, context) -> TrackSignal:
        lookback = int(_parse_variant(self.variant, 63, ("vol", "volatility"), int))
        monthly = _is_review(histories[self.symbols[0]], "monthly")
        entries: list[str] = []
        exits: list[str] = []
        if monthly:
            scores: dict[str, float] = {}
            for symbol in self.symbols:
                history = histories[symbol]
                trend = sma(history["close"], 200).iloc[-1]
                momentum = returns(history["close"], 126).iloc[-1]
                volatility = returns(history["close"], 1).tail(lookback).std() * np.sqrt(252.0)
                score = (
                    max(
                        0.0,
                        0.5 * float(momentum) + 0.5 * float(returns(history["close"], 63).iloc[-1]),
                    )
                    / float(volatility)
                    if _valid(momentum)
                    and _valid(volatility)
                    and float(volatility) > 0.0
                    and _valid(returns(history["close"], 63).iloc[-1])
                    else 0.0
                )
                if (
                    _valid(trend)
                    and _close(history) > float(trend)
                    and _valid(momentum)
                    and float(momentum) > 0.0
                    and score > 0.0
                ):
                    scores[symbol] = score
            total = sum(scores.values())
            weights = (
                {symbol: 0.99 * score / total for symbol, score in scores.items()}
                if total > 0
                else {}
            )
            for symbol in self.symbols:
                was_active = self.state.active.get(symbol, False)
                self.state.active[symbol] = symbol in weights
                if symbol in weights and not was_active:
                    entries.append(symbol)
                if was_active and symbol not in weights:
                    exits.append(symbol)
            self.state.last_weights = dict(weights)
        else:
            weights = dict(self.state.last_weights)
        for symbol in self.symbols:
            history = histories[symbol]
            trend = sma(history["close"], 200).iloc[-1]
            if (
                self.state.active.get(symbol, False)
                and _valid(trend)
                and _close(history) < float(trend)
            ):
                self.state.active[symbol] = False
                weights.pop(symbol, None)
                if symbol not in exits:
                    exits.append(symbol)
        self.state.last_weights = dict(weights)
        return _signal(
            asof, self.symbols, weights, "A03_VOLATILITY_NORMALIZED_MONTHLY", entries, exits
        )

    def _a04(self, frames, histories, asof, index, context) -> TrackSignal:
        buffer_value = _parse_variant(self.variant, 0.01, ("buffer",), float)
        entries: list[str] = []
        exits: list[str] = []
        weights: dict[str, float] = {}
        for symbol in self.symbols:
            history = histories[symbol]
            trend = sma(history["close"], 200).iloc[-1]
            if not _valid(trend):
                continue
            close = _close(history)
            active = self.state.active.get(symbol, False)
            if active:
                if close < (1.0 - buffer_value) * float(trend):
                    self.state.streak_down[symbol] = self.state.streak_down.get(symbol, 0) + 1
                else:
                    self.state.streak_down[symbol] = 0
                self.state.streak_up[symbol] = 0
                if self.state.streak_down[symbol] >= 2:
                    self.state.active[symbol] = False
                    self.state.entry_weight.pop(symbol, None)
                    exits.append(symbol)
            else:
                if close > (1.0 + buffer_value) * float(trend):
                    self.state.streak_up[symbol] = self.state.streak_up.get(symbol, 0) + 1
                else:
                    self.state.streak_up[symbol] = 0
                self.state.streak_down[symbol] = 0
                if self.state.streak_up[symbol] >= 3:
                    self.state.active[symbol] = True
                    entries.append(symbol)
        for symbol in self.symbols:
            if self.state.active.get(symbol, False):
                weights[symbol] = 0.495
        self.state.last_weights = dict(weights)
        return _signal(asof, self.symbols, weights, "A04_HYSTERESIS_TREND", entries, exits)

    def _a05(self, frames, histories, asof, index, context) -> TrackSignal:
        multiple = _parse_variant(self.variant, 3.0, ("atr", "multiple"), float)
        entries: list[str] = []
        exits: list[str] = []
        weights: dict[str, float] = {}
        for symbol in self.symbols:
            history = histories[symbol]
            close = _close(history)
            prior_high = history["high"].shift(1).rolling(55, min_periods=55).max().iloc[-1]
            prior_low = history["low"].shift(1).rolling(20, min_periods=20).min().iloc[-1]
            trend = sma(history["close"], 200).iloc[-1]
            atr_value = atr(history, 14).iloc[-1]
            active = self.state.active.get(symbol, False)
            if active:
                self.state.highest_close[symbol] = max(
                    self.state.highest_close.get(symbol, close), close
                )
                trailing = (
                    self.state.highest_close[symbol] - multiple * float(atr_value)
                    if _valid(atr_value)
                    else float("nan")
                )
                if (_valid(prior_low) and close < float(prior_low)) or (
                    _valid(trailing) and close < trailing
                ):
                    self.state.active[symbol] = False
                    self.state.entry_weight.pop(symbol, None)
                    exits.append(symbol)
            elif (
                _valid(prior_high)
                and _valid(trend)
                and _valid(atr_value)
                and close > float(prior_high)
                and close > float(trend)
                and float(atr_value) > 0.0
            ):
                risk_denominator = (
                    multiple * float(atr_value) / close if close > 0.0 else float("nan")
                )
                weight = (
                    min(0.495, 0.015 / risk_denominator)
                    if _valid(risk_denominator) and risk_denominator > 0
                    else 0.0
                )
                if weight > 0.0:
                    self.state.active[symbol] = True
                    self.state.entry_weight[symbol] = weight
                    self.state.highest_close[symbol] = close
                    entries.append(symbol)
            if self.state.active.get(symbol, False):
                weights[symbol] = self.state.entry_weight.get(symbol, 0.495)
        self.state.last_weights = dict(weights)
        return _signal(asof, self.symbols, weights, "A05_RISK_SCALED_BREAKOUT", entries, exits)

    def _a06(self, frames, histories, asof, index, context) -> TrackSignal:
        window = int(_parse_variant(self.variant, 3, ("confirm", "window"), int))
        entries: list[str] = []
        exits: list[str] = []
        weights: dict[str, float] = {}
        for symbol in self.symbols:
            history = histories[symbol]
            close = _close(history)
            trend = sma(history["close"], 200).iloc[-1]
            fast = sma(history["close"], 50).iloc[-1]
            value = rsi(history["close"], 2).iloc[-1]
            active = self.state.active.get(symbol, False)
            if active:
                age = index - self.state.entry_index.get(symbol, index) + 1
                should_exit = (
                    (_valid(value) and float(value) > 70.0)
                    or (_valid(fast) and close < float(fast))
                    or age >= 10
                )
                if should_exit:
                    self.state.active[symbol] = False
                    self.state.cooldown_until[symbol] = index + 1
                    self.state.arm_index.pop(symbol, None)
                    exits.append(symbol)
            else:
                cooldown_ready = index > self.state.cooldown_until.get(symbol, -1)
                trend_ok = (
                    _valid(trend)
                    and _valid(fast)
                    and close > float(trend)
                    and float(fast) > float(trend)
                )
                if (
                    cooldown_ready
                    and trend_ok
                    and _valid(value)
                    and float(value) < 10.0
                    and symbol not in self.state.arm_index
                ):
                    self.state.arm_index[symbol] = index
                arm = self.state.arm_index.get(symbol)
                if arm is not None:
                    age = index - arm
                    previous_high = (
                        history["high"].shift(1).iloc[-1] if len(history) > 1 else float("nan")
                    )
                    if not trend_ok or age > window:
                        self.state.arm_index.pop(symbol, None)
                    elif age >= 1 and _valid(previous_high) and close > float(previous_high):
                        self.state.active[symbol] = True
                        self.state.entry_index[symbol] = index
                        self.state.arm_index.pop(symbol, None)
                        entries.append(symbol)
            if self.state.active.get(symbol, False):
                weights[symbol] = 0.495
        self.state.last_weights = dict(weights)
        return _signal(asof, self.symbols, weights, "A06_CONFIRMED_PULLBACK", entries, exits)

    def _a07(self, frames, histories, asof, index, context) -> TrackSignal:
        threshold = _parse_variant(self.variant, 0.85, ("corr", "correlation"), float)
        weekly = _is_review(histories[self.symbols[0]], "weekly")
        entries: list[str] = []
        exits: list[str] = []
        if weekly:
            eligible: list[str] = []
            momentum: dict[str, float] = {}
            for symbol in self.symbols:
                history = histories[symbol]
                trend = sma(history["close"], 200).iloc[-1]
                score = returns(history["close"], 63).iloc[-1]
                if (
                    _valid(trend)
                    and _valid(score)
                    and _close(history) > float(trend)
                    and float(score) > 0.0
                ):
                    eligible.append(symbol)
                    momentum[symbol] = float(score)
            weights = {symbol: 0.495 for symbol in eligible}
            if len(eligible) == 2:
                left = returns(histories[eligible[0]]["close"], 1).tail(60).reset_index(drop=True)
                right = returns(histories[eligible[1]]["close"], 1).tail(60).reset_index(drop=True)
                correlation = (
                    left.corr(right)
                    if len(left.dropna()) >= 2 and len(right.dropna()) >= 2
                    else float("nan")
                )
                if _valid(correlation) and float(correlation) > threshold:
                    weaker = min(
                        eligible,
                        key=lambda symbol: (momentum[symbol], 1 if symbol == "QQQM" else 0),
                    )
                    weights[weaker] *= 0.5
            for symbol in self.symbols:
                was_active = self.state.active.get(symbol, False)
                now_active = symbol in weights and weights[symbol] > 0.0
                self.state.active[symbol] = now_active
                if now_active and not was_active:
                    entries.append(symbol)
                if was_active and not now_active:
                    exits.append(symbol)
            self.state.last_weights = dict(weights)
        else:
            weights = dict(self.state.last_weights)
        for symbol in self.symbols:
            history = histories[symbol]
            trend = sma(history["close"], 200).iloc[-1]
            if (
                self.state.active.get(symbol, False)
                and _valid(trend)
                and _close(history) < float(trend)
            ):
                self.state.active[symbol] = False
                weights.pop(symbol, None)
                if symbol not in exits:
                    exits.append(symbol)
        self.state.last_weights = dict(weights)
        return _signal(
            asof, self.symbols, weights, "A07_CORRELATION_SENSITIVE_WEEKLY", entries, exits
        )

    def _a08(self, frames, histories, asof, index, context) -> TrackSignal:
        scale_threshold = _parse_variant(self.variant, 0.30, ("er", "threshold"), float)
        weekly = _is_review(histories[self.symbols[0]], "weekly")
        entries: list[str] = []
        exits: list[str] = []
        if weekly:
            weights: dict[str, float] = {}
            for symbol in self.symbols:
                history = histories[symbol]
                trend = sma(history["close"], 200).iloc[-1]
                momentum = returns(history["close"], 63).iloc[-1]
                if not (
                    _valid(trend)
                    and _valid(momentum)
                    and _close(history) > float(trend)
                    and float(momentum) > 0.0
                ):
                    continue
                numerator = (
                    abs(_close(history) - float(history["close"].iloc[-64]))
                    if len(history) >= 64
                    else float("nan")
                )
                denominator = (
                    history["close"].diff().abs().tail(63).sum()
                    if len(history) >= 64
                    else float("nan")
                )
                efficiency = (
                    numerator / float(denominator)
                    if _valid(denominator) and float(denominator) > 0.0
                    else 0.0
                )
                weight = (
                    0.495 * min(1.0, efficiency / scale_threshold) if scale_threshold > 0 else 0.0
                )
                if weight > 0.0:
                    weights[symbol] = weight
            for symbol in self.symbols:
                was_active = self.state.active.get(symbol, False)
                now_active = symbol in weights
                self.state.active[symbol] = now_active
                if now_active and not was_active:
                    entries.append(symbol)
                if was_active and not now_active:
                    exits.append(symbol)
            self.state.last_weights = dict(weights)
        else:
            weights = dict(self.state.last_weights)
        for symbol in self.symbols:
            history = histories[symbol]
            trend = sma(history["close"], 200).iloc[-1]
            if (
                self.state.active.get(symbol, False)
                and _valid(trend)
                and _close(history) < float(trend)
            ):
                self.state.active[symbol] = False
                weights.pop(symbol, None)
                if symbol not in exits:
                    exits.append(symbol)
        self.state.last_weights = dict(weights)
        return _signal(asof, self.symbols, weights, "A08_TREND_QUALITY_WEEKLY", entries, exits)

    def _component_signal(self, frames, histories, asof, index, context, name: str) -> TrackSignal:
        component = self._components[name]
        return component.evaluate(
            frames, index, state=self.state.component_states[name], context=context
        )

    @staticmethod
    def _combine_component_weights(
        first: TrackSignal,
        second: TrackSignal,
        first_share: float,
        second_share: float,
        symbols: tuple[str, ...],
    ) -> dict[str, float]:
        weights = {symbol: 0.0 for symbol in symbols}
        for symbol in symbols:
            weights[symbol] = first_share * first.target_weights.get(
                symbol, 0.0
            ) + second_share * second.target_weights.get(symbol, 0.0)
        return {symbol: weight for symbol, weight in weights.items() if weight > 0.0}

    def _a09(self, frames, histories, asof, index, context) -> TrackSignal:
        trend_signal = self._component_signal(frames, histories, asof, index, context, "trend")
        pullback_signal = self._component_signal(
            frames, histories, asof, index, context, "pullback"
        )
        raw_weights = self._combine_component_weights(
            trend_signal, pullback_signal, 0.693, 0.297, self.symbols
        )
        if "shadow_drawdown" in context:
            drawdown = _current_drawdown(context["shadow_drawdown"])
        elif (
            _valid(context.get("shadow_equity"))
            and _valid(context.get("shadow_peak_equity"))
            and float(context["shadow_peak_equity"]) > 0.0
        ):
            drawdown = max(
                0.0, 1.0 - float(context["shadow_equity"]) / float(context["shadow_peak_equity"])
            )
        else:
            drawdown = _current_drawdown(self.state.shadow_drawdown)
        self.state.shadow_drawdown = drawdown
        threshold_scale = _parse_variant(self.variant, 1.0, ("throttle", "threshold"), float)
        first_boundary = 0.08 * threshold_scale
        second_boundary = 0.15 * threshold_scale
        third_boundary = 0.25 * threshold_scale
        scale = (
            1.0
            if drawdown < first_boundary
            else 0.75
            if drawdown < second_boundary
            else 0.50
            if drawdown < third_boundary
            else 0.25
        )
        weekly = _is_review(histories[self.symbols[0]], "weekly")
        if weekly:
            self.state.last_weights = {
                symbol: scale * weight for symbol, weight in raw_weights.items()
            }
        weights = dict(self.state.last_weights)
        exits = list(trend_signal.exits + pullback_signal.exits)
        entries = list(trend_signal.entries + pullback_signal.entries)
        # Exits from either component take effect every session, while new
        # exposure waits for the weekly rebalance.
        for symbol in exits:
            weights.pop(symbol, None)
        self.state.last_weights = dict(weights)
        components = {
            "S01": {symbol: scale * 0.693 * weight for symbol, weight in trend_signal.target_weights.items()},
            "S04": {symbol: scale * 0.297 * weight for symbol, weight in pullback_signal.target_weights.items()},
        }
        return _signal(
            asof,
            self.symbols,
            weights,
            "A09_S10_DRAWDOWN_THROTTLE",
            entries,
            exits,
            components,
            raw_weights,
        )

    def _a10(self, frames, histories, asof, index, context) -> TrackSignal:
        trend_signal = self._component_signal(frames, histories, asof, index, context, "trend")
        pullback_signal = self._component_signal(
            frames, histories, asof, index, context, "pullback"
        )
        weekly = _is_review(histories[self.symbols[0]], "weekly")
        configured_share = _parse_variant(self.variant, 0.80, ("trend_share", "share"), float)
        if weekly:
            strong = True
            for symbol in self.symbols:
                history = histories[symbol]
                trend = sma(history["close"], 200).iloc[-1]
                current_fast = sma(history["close"], 50).iloc[-1]
                prior_fast = sma(history["close"], 50).shift(20).iloc[-1]
                if not (
                    _valid(trend)
                    and _valid(current_fast)
                    and _valid(prior_fast)
                    and _close(history) > float(trend)
                    and float(current_fast) > float(prior_fast)
                ):
                    strong = False
                    break
            self.state.last_weights["__trend_share__"] = configured_share if strong else 0.50
        trend_share = float(self.state.last_weights.get("__trend_share__", configured_share))
        pullback_share = 1.0 - trend_share
        weights = self._combine_component_weights(
            trend_signal, pullback_signal, trend_share, pullback_share, self.symbols
        )
        exits = list(trend_signal.exits + pullback_signal.exits)
        entries = list(trend_signal.entries + pullback_signal.entries)
        self.state.last_weights.update({symbol: weight for symbol, weight in weights.items()})
        components = {
            "S01": {symbol: trend_share * weight for symbol, weight in trend_signal.target_weights.items()},
            "S04": {symbol: pullback_share * weight for symbol, weight in pullback_signal.target_weights.items()},
        }
        return _signal(
            asof,
            self.symbols,
            weights,
            "A10_TREND_STRENGTH_ENSEMBLE",
            entries,
            exits,
            components,
        )


def build_track_a(
    strategy_id: str,
    symbols: tuple[str, ...] = _DEFAULT_SYMBOLS,
    variant: str = "primary",
    state: TrackAState | None = None,
) -> TrackAStrategy:
    """Build a Track A strategy using the stable integration interface."""

    return TrackAStrategy(strategy_id, symbols=symbols, variant=variant, state=state)


def strategy_variants(strategy_id: str) -> tuple[str, ...]:
    """Return the registered primary plus the two bounded diagnostics."""

    variants = {
        "A01": ("primary", "buffer_0.02", "buffer_0.04"),
        "A02": ("primary", "sma_40", "sma_60"),
        "A03": ("primary", "vol_42", "vol_84"),
        "A04": ("primary", "buffer_0.005", "buffer_0.015"),
        "A05": ("primary", "atr_2.5", "atr_3.5"),
        "A06": ("primary", "confirm_2", "confirm_4"),
        "A07": ("primary", "corr_0.80", "corr_0.90"),
        "A08": ("primary", "er_0.20", "er_0.40"),
        "A09": ("primary", "throttle_0.8", "throttle_1.2"),
        "A10": ("primary", "trend_share_0.70", "trend_share_0.90"),
    }
    if strategy_id not in variants:
        raise ValueError(f"TRACK_A_STRATEGY_UNKNOWN:{strategy_id}")
    return variants[strategy_id]


__all__ = [
    "TrackAState",
    "TrackAStrategy",
    "TrackSignal",
    "StrategyState",
    "build_track_a",
    "strategy_variants",
]
