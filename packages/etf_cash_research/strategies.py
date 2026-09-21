"""Pure deterministic target-weight engines for the ten ETF candidates."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .indicators import atr, ema, returns, rolling_percentile, rsi, sma


@dataclass(frozen=True)
class Signal:
    asof: pd.Timestamp
    target_weights: dict[str, float]
    reason_code: str
    entries: tuple[str, ...] = ()
    exits: tuple[str, ...] = ()


@dataclass
class StrategyState:
    active: dict[str, bool] = field(default_factory=dict)
    entry_index: dict[str, int] = field(default_factory=dict)
    arm_index: dict[str, int] = field(default_factory=dict)
    highest_close: dict[str, float] = field(default_factory=dict)
    cooldown_until: dict[str, int] = field(default_factory=dict)
    selected: str | None = None
    previous_month: tuple[int, int] | None = None
    previous_week: tuple[int, int] | None = None
    last_weights: dict[str, float] = field(default_factory=dict)


def _clean_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["date"] = pd.to_datetime(result["date"], utc=True)
    for column in ("open", "high", "low", "close", "volume"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    return result.sort_values("date", kind="stable").reset_index(drop=True)


def _row(frames: Mapping[str, pd.DataFrame], symbol: str, index: int) -> pd.Series | None:
    frame = frames.get(symbol)
    if frame is None or index >= len(frame):
        return None
    return frame.iloc[index]


def _series(frames: Mapping[str, pd.DataFrame], symbol: str, index: int) -> pd.DataFrame:
    frame = frames[symbol]
    return frame.iloc[: index + 1]


def _valid(value: Any) -> bool:
    return value is not None and not pd.isna(value) and np.isfinite(float(value))


def _date_boundary(row: pd.Series, previous: pd.Series | None, kind: str) -> bool:
    current = pd.Timestamp(row["date"])
    if previous is None:
        return True
    prior = pd.Timestamp(previous["date"])
    if kind == "month":
        return (current.year, current.month) != (prior.year, prior.month)
    return current.isocalendar().week != prior.isocalendar().week or current.year != prior.year


class ETFStrategy:
    def __init__(self, strategy_id: str, semiconductor: str, variant: str = "primary") -> None:
        if strategy_id not in {f"S{index:02d}" for index in range(1, 11)}:
            raise ValueError("ETF_STRATEGY_UNKNOWN")
        if semiconductor not in {"SOXX", "SMH"}:
            raise ValueError("ETF_SEMICONDUCTOR_UNKNOWN")
        self.strategy_id = strategy_id
        self.semiconductor = semiconductor
        self.variant = variant
        self.state = StrategyState(active={"QQQM": False, semiconductor: False})
        self._trend_component = ETFStrategy("S01", semiconductor, "primary") if strategy_id == "S10" else None
        self._pullback_component = ETFStrategy("S04", semiconductor, "primary") if strategy_id == "S10" else None

    @property
    def symbols(self) -> tuple[str, str]:
        return ("QQQM", self.semiconductor)

    def evaluate(self, frames: Mapping[str, pd.DataFrame], index: int) -> Signal:
        if not all(symbol in frames for symbol in self.symbols):
            return Signal(pd.Timestamp("1970-01-01", tz="UTC"), {}, "MISSING_SYMBOL")
        qqqm = _row(frames, "QQQM", index)
        if qqqm is None:
            return Signal(pd.Timestamp("1970-01-01", tz="UTC"), {}, "MISSING_SESSION")
        if self.strategy_id == "S01":
            return self._s01(frames, index)
        if self.strategy_id == "S02":
            return self._s02(frames, index)
        if self.strategy_id == "S03":
            return self._s03(frames, index)
        if self.strategy_id == "S04":
            return self._s04(frames, index)
        if self.strategy_id == "S05":
            return self._s05(frames, index)
        if self.strategy_id == "S06":
            return self._s06(frames, index)
        if self.strategy_id == "S07":
            return self._s07(frames, index)
        if self.strategy_id == "S08":
            return self._s08(frames, index)
        if self.strategy_id == "S09":
            return self._s09(frames, index)
        return self._s10(frames, index)

    def _signal(self, frames: Mapping[str, pd.DataFrame], index: int, weights: dict[str, float], reason: str, entries: list[str], exits: list[str]) -> Signal:
        row = _row(frames, "QQQM", index)
        assert row is not None
        return Signal(pd.Timestamp(row["date"]), {symbol: min(0.99, max(0.0, float(weight))) for symbol, weight in weights.items() if weight > 0}, reason, tuple(entries), tuple(exits))

    def _monthly(self, frames: Mapping[str, pd.DataFrame], index: int) -> bool:
        row = _row(frames, "QQQM", index)
        previous = _row(frames, "QQQM", index - 1) if index else None
        return row is not None and _date_boundary(row, previous, "month")

    def _weekly(self, frames: Mapping[str, pd.DataFrame], index: int) -> bool:
        row = _row(frames, "QQQM", index)
        previous = _row(frames, "QQQM", index - 1) if index else None
        return row is not None and _date_boundary(row, previous, "week")

    def _s01(self, frames: Mapping[str, pd.DataFrame], index: int) -> Signal:
        momentum_period = 126 if self.variant == "primary" else int(self.variant.split("_")[-1])
        exits: list[str] = []
        weights: dict[str, float] = {}
        for symbol in self.symbols:
            history = _series(frames, symbol, index)
            close = float(history.iloc[-1]["close"])
            trend = sma(history["close"], 200).iloc[-1]
            if self.state.active.get(symbol, False) and _valid(trend) and close < float(trend):
                self.state.active[symbol] = False
                if symbol == self.state.selected:
                    self.state.selected = None
                exits.append(symbol)
        if self._monthly(frames, index):
            candidates: list[tuple[float, str]] = []
            for symbol in self.symbols:
                history = _series(frames, symbol, index)
                trend = sma(history["close"], 200).iloc[-1]
                r63 = returns(history["close"], 63).iloc[-1]
                r_momentum = returns(history["close"], momentum_period).iloc[-1]
                if _valid(trend) and _valid(r63) and _valid(r_momentum) and float(history.iloc[-1]["close"]) > float(trend) and float(r_momentum) > 0:
                    candidates.append((0.5 * float(r63) + 0.5 * float(r_momentum), symbol))
            winner = max(candidates, key=lambda item: (item[0], 1 if item[1] == "QQQM" else 0))[1] if candidates else None
            if winner != self.state.selected:
                if self.state.selected:
                    exits.append(self.state.selected)
                    self.state.active[self.state.selected] = False
                self.state.selected = winner
            # A daily trend exit can leave ``selected`` pointing at the same
            # symbol.  The next monthly review is the permitted re-entry
            # point, so explicitly reactivate a still-qualified winner here.
            if winner:
                self.state.active[winner] = True
            if winner:
                weights[winner] = 0.99
        elif self.state.selected and self.state.active.get(self.state.selected, False):
            weights[self.state.selected] = 0.99
        return self._signal(frames, index, weights, "S01_MONTHLY_MOMENTUM", list(weights), exits)

    def _s02(self, frames: Mapping[str, pd.DataFrame], index: int) -> Signal:
        slow_period = 100 if self.variant == "primary" else int(self.variant.split("_")[-1])
        weights: dict[str, float] = {}
        entries: list[str] = []
        exits: list[str] = []
        for symbol in self.symbols:
            history = _series(frames, symbol, index)
            close = float(history.iloc[-1]["close"])
            fast = ema(history["close"], 20).iloc[-1]
            slow = ema(history["close"], slow_period).iloc[-1]
            trend = sma(history["close"], 200).iloc[-1]
            qualifies = _valid(fast) and _valid(slow) and _valid(trend) and fast > slow and close > trend
            if qualifies and not self.state.active.get(symbol, False):
                self.state.active[symbol] = True
                entries.append(symbol)
            if not qualifies and self.state.active.get(symbol, False):
                self.state.active[symbol] = False
                exits.append(symbol)
            if self.state.active.get(symbol, False):
                weights[symbol] = 0.495
        return self._signal(frames, index, weights, "S02_DAILY_TREND", entries, exits)

    def _s03(self, frames: Mapping[str, pd.DataFrame], index: int) -> Signal:
        entry_period = 55 if self.variant == "primary" else int(self.variant.split("_")[-1])
        exits_period = 20
        weights: dict[str, float] = {}
        entries: list[str] = []
        exits: list[str] = []
        for symbol in self.symbols:
            history = _series(frames, symbol, index)
            close = float(history.iloc[-1]["close"])
            prior_high = history["high"].shift(1).rolling(entry_period, min_periods=entry_period).max().iloc[-1]
            prior_low = history["low"].shift(1).rolling(exits_period, min_periods=exits_period).min().iloc[-1]
            active = self.state.active.get(symbol, False)
            if not active and _valid(prior_high) and close > float(prior_high):
                self.state.active[symbol] = True
                entries.append(symbol)
            elif active and _valid(prior_low) and close < float(prior_low):
                self.state.active[symbol] = False
                exits.append(symbol)
            if self.state.active.get(symbol, False):
                weights[symbol] = 0.495
        return self._signal(frames, index, weights, "S03_DONCHIAN", entries, exits)

    def _s04(self, frames: Mapping[str, pd.DataFrame], index: int) -> Signal:
        threshold = 10 if self.variant == "primary" else float(self.variant.split("_")[-1])
        weights: dict[str, float] = {}
        entries: list[str] = []
        exits: list[str] = []
        for symbol in self.symbols:
            history = _series(frames, symbol, index)
            close = float(history.iloc[-1]["close"])
            trend = sma(history["close"], 200).iloc[-1]
            fast = sma(history["close"], 50).iloc[-1]
            slow = trend
            value = rsi(history["close"], 2).iloc[-1]
            held = self.state.active.get(symbol, False)
            if held:
                age = index - self.state.entry_index.get(symbol, index) + 1
                should_exit = (_valid(value) and value > 70) or (_valid(trend) and close < trend) or age >= 10
                if should_exit:
                    self.state.active[symbol] = False
                    self.state.cooldown_until[symbol] = index + 1
                    exits.append(symbol)
            else:
                ready = index > self.state.cooldown_until.get(symbol, -1)
                if ready and _valid(trend) and _valid(fast) and _valid(value) and close > trend and fast > slow and value < threshold:
                    self.state.active[symbol] = True
                    self.state.entry_index[symbol] = index
                    entries.append(symbol)
            if self.state.active.get(symbol, False):
                weights[symbol] = 0.495
        return self._signal(frames, index, weights, "S04_TREND_PULLBACK", entries, exits)

    def _s05(self, frames: Mapping[str, pd.DataFrame], index: int) -> Signal:
        width = 2.0 if self.variant == "primary" else float(self.variant.split("_")[-1])
        weights: dict[str, float] = {}
        entries: list[str] = []
        exits: list[str] = []
        for symbol in self.symbols:
            history = _series(frames, symbol, index)
            close = float(history.iloc[-1]["close"])
            middle = sma(history["close"], 20).iloc[-1]
            deviation = history["close"].rolling(20, min_periods=20).std().iloc[-1]
            lower = middle - width * deviation if _valid(middle) and _valid(deviation) else np.nan
            trend = sma(history["close"], 200).iloc[-1]
            held = self.state.active.get(symbol, False)
            if held:
                age = index - self.state.entry_index.get(symbol, index) + 1
                if (_valid(middle) and close > middle) or (_valid(trend) and close < trend) or age >= 15:
                    self.state.active[symbol] = False
                    exits.append(symbol)
            else:
                armed = symbol in self.state.arm_index
                if _valid(trend) and close < lower and close > trend:
                    self.state.arm_index[symbol] = index
                    armed = True
                arm_index = self.state.arm_index.get(symbol)
                if armed and arm_index is not None:
                    if index - arm_index > 5 or (_valid(trend) and close < trend):
                        self.state.arm_index.pop(symbol, None)
                    elif _valid(lower) and close > lower and index > arm_index:
                        self.state.active[symbol] = True
                        self.state.entry_index[symbol] = index
                        self.state.arm_index.pop(symbol, None)
                        entries.append(symbol)
            if self.state.active.get(symbol, False):
                weights[symbol] = 0.495
        return self._signal(frames, index, weights, "S05_BOLLINGER_RECOVERY", entries, exits)

    def _s06(self, frames: Mapping[str, pd.DataFrame], index: int) -> Signal:
        ratio_period = 20 if self.variant == "primary" else int(self.variant.split("_")[-1])
        weights: dict[str, float] = {}
        exits: list[str] = []
        selected = self.state.selected
        for symbol in self.symbols:
            history = _series(frames, symbol, index)
            trend = sma(history["close"], 100).iloc[-1]
            if selected == symbol and _valid(trend) and float(history.iloc[-1]["close"]) < trend:
                selected = None
                exits.append(symbol)
        if self._weekly(frames, index):
            s_history = _series(frames, self.semiconductor, index)
            q_history = _series(frames, "QQQM", index)
            ratio = s_history["close"].to_numpy() / q_history["close"].to_numpy()
            ratio_series = pd.Series(ratio)
            s_trend = sma(s_history["close"], 100).iloc[-1]
            ratio_trend = sma(ratio_series, ratio_period).iloc[-1]
            s_momentum = returns(s_history["close"], 63).iloc[-1]
            q_momentum = returns(q_history["close"], 63).iloc[-1]
            previous_selected = selected
            if _valid(s_trend) and _valid(ratio_trend) and _valid(s_momentum) and _valid(q_momentum) and float(s_history.iloc[-1]["close"]) > s_trend and ratio[-1] > ratio_trend and s_momentum > q_momentum:
                selected = self.semiconductor
            else:
                q_trend = sma(q_history["close"], 100).iloc[-1]
                selected = "QQQM" if _valid(q_trend) and float(q_history.iloc[-1]["close"]) > q_trend else None
            if previous_selected and previous_selected != selected:
                exits.append(previous_selected)
        self.state.selected = selected
        if selected:
            weights[selected] = 0.99
        return self._signal(frames, index, weights, "S06_LEADERSHIP_SWITCH", list(weights), exits)

    def _s07(self, frames: Mapping[str, pd.DataFrame], index: int) -> Signal:
        volatility_target = 0.25 if self.variant == "primary" else float(self.variant.split("_")[-1]) / 100.0
        weights: dict[str, float] = {}
        exits: list[str] = []
        if self._weekly(frames, index):
            eligible: dict[str, float] = {}
            for symbol, base in (("QQQM", 0.40), (self.semiconductor, 0.60)):
                history = _series(frames, symbol, index)
                trend = sma(history["close"], 200).iloc[-1]
                if _valid(trend) and float(history.iloc[-1]["close"]) > trend:
                    eligible[symbol] = base
            if eligible:
                joined = pd.DataFrame({symbol: _series(frames, symbol, index)["close"].pct_change() for symbol in eligible}).dropna().tail(60)
                volatility = float(joined.cov().to_numpy().dot(np.array(list(eligible.values()))).dot(np.array(list(eligible.values()))) ** 0.5 * np.sqrt(252)) if len(joined) >= 2 else np.nan
                multiplier = min(1.0, volatility_target / volatility) if _valid(volatility) and volatility > 0 else 1.0
                weights = {symbol: 0.99 * base * multiplier for symbol, base in eligible.items()}
            self.state.last_weights = dict(weights)
        else:
            weights = dict(self.state.last_weights)
        for symbol in self.symbols:
            history = _series(frames, symbol, index)
            trend = sma(history["close"], 200).iloc[-1]
            if self.state.active.get(symbol, False) and _valid(trend) and float(history.iloc[-1]["close"]) < trend:
                self.state.active[symbol] = False
                exits.append(symbol)
                weights.pop(symbol, None)
            self.state.active[symbol] = symbol in weights
        return self._signal(frames, index, weights, "S07_VOLATILITY_CONTROL", list(weights), exits)

    def _s08(self, frames: Mapping[str, pd.DataFrame], index: int) -> Signal:
        breakout_period = 20 if self.variant == "primary" else int(self.variant.split("_")[-1])
        q_history = _series(frames, "QQQM", index)
        s_history = _series(frames, self.semiconductor, index)
        weights: dict[str, float] = {}
        entries: list[str] = []
        exits: list[str] = []
        q_trend = sma(q_history["close"], 200).iloc[-1]
        if _valid(q_trend) and float(q_history.iloc[-1]["close"]) > q_trend:
            weights["QQQM"] = 0.594
            if not self.state.active.get("QQQM", False):
                entries.append("QQQM")
            self.state.active["QQQM"] = True
        elif self.state.active.get("QQQM", False):
            self.state.active["QQQM"] = False
            exits.append("QQQM")
        prior_high = s_history["high"].shift(1).rolling(breakout_period, min_periods=breakout_period).max().iloc[-1]
        s_momentum = returns(s_history["close"], 63).iloc[-1]
        q_momentum = returns(q_history["close"], 63).iloc[-1]
        s_entry = _valid(prior_high) and _valid(s_momentum) and _valid(q_momentum) and float(s_history.iloc[-1]["close"]) > prior_high and s_momentum > q_momentum
        if self.state.active.get(self.semiconductor, False):
            age = index - self.state.entry_index.get(self.semiconductor, index) + 1
            s_ema = ema(s_history["close"], 20).iloc[-1]
            if (_valid(s_ema) and float(s_history.iloc[-1]["close"]) < s_ema) or age >= 30:
                self.state.active[self.semiconductor] = False
                exits.append(self.semiconductor)
        elif s_entry:
            self.state.active[self.semiconductor] = True
            self.state.entry_index[self.semiconductor] = index
            entries.append(self.semiconductor)
        if self.state.active.get(self.semiconductor, False):
            weights[self.semiconductor] = 0.396
        return self._signal(frames, index, weights, "S08_CORE_BREAKOUT", entries, exits)

    def _s09(self, frames: Mapping[str, pd.DataFrame], index: int) -> Signal:
        percentile = 20.0 if self.variant == "primary" else float(self.variant.split("_")[-1])
        weights: dict[str, float] = {}
        entries: list[str] = []
        exits: list[str] = []
        for symbol in self.symbols:
            history = _series(frames, symbol, index)
            close = float(history.iloc[-1]["close"])
            middle = sma(history["close"], 20).iloc[-1]
            deviation = history["close"].rolling(20, min_periods=20).std().iloc[-1]
            bandwidth = 4.0 * deviation / middle if _valid(deviation) and _valid(middle) and middle else np.nan
            bandwidth_series = 4.0 * history["close"].rolling(20, min_periods=20).std() / sma(history["close"], 20)
            threshold = rolling_percentile(bandwidth_series.shift(1), 126, percentile).iloc[-1]
            contraction = _valid(bandwidth) and _valid(threshold) and bandwidth < threshold
            if contraction:
                self.state.arm_index[symbol] = index
            arm = self.state.arm_index.get(symbol)
            prior_high = history["high"].shift(1).rolling(20, min_periods=20).max().iloc[-1]
            trend = sma(history["close"], 100).iloc[-1]
            active = self.state.active.get(symbol, False)
            if active:
                self.state.highest_close[symbol] = max(self.state.highest_close.get(symbol, close), close)
                trailing = self.state.highest_close[symbol] - 3.0 * float(atr(history, 14).iloc[-1]) if _valid(atr(history, 14).iloc[-1]) else np.nan
                age = index - self.state.entry_index.get(symbol, index) + 1
                if (_valid(trailing) and close < trailing) or age >= 40:
                    self.state.active[symbol] = False
                    exits.append(symbol)
            elif arm is not None and index - arm <= 10 and _valid(prior_high) and _valid(trend) and close > prior_high and close > trend:
                self.state.active[symbol] = True
                self.state.entry_index[symbol] = index
                self.state.highest_close[symbol] = close
                self.state.arm_index.pop(symbol, None)
                entries.append(symbol)
            elif arm is not None and index - arm > 10:
                self.state.arm_index.pop(symbol, None)
            if self.state.active.get(symbol, False):
                weights[symbol] = 0.495
        return self._signal(frames, index, weights, "S09_CONTRACTION_BREAKOUT", entries, exits)

    def _s10(self, frames: Mapping[str, pd.DataFrame], index: int) -> Signal:
        assert self._trend_component is not None and self._pullback_component is not None
        trend_signal = self._trend_component.evaluate(frames, index)
        pullback_signal = self._pullback_component.evaluate(frames, index)
        weights: dict[str, float] = {}
        trend_share = 0.70 if self.variant == "primary" else float(self.variant.split("_")[-1]) / 100.0
        for symbol, weight in trend_signal.target_weights.items():
            weights[symbol] = weights.get(symbol, 0.0) + trend_share * weight
        for symbol, weight in pullback_signal.target_weights.items():
            weights[symbol] = weights.get(symbol, 0.0) + (1.0 - trend_share) * weight
        return self._signal(frames, index, weights, "S10_FIXED_ENSEMBLE", list(set(trend_signal.entries + pullback_signal.entries)), list(set(trend_signal.exits + pullback_signal.exits)))


def build_strategy(strategy_id: str, semiconductor: str, variant: str = "primary") -> ETFStrategy:
    return ETFStrategy(strategy_id, semiconductor, variant)


def strategy_variants(strategy_id: str) -> tuple[str, ...]:
    return {
        "S01": ("primary", "momentum_105", "momentum_147"),
        "S02": ("primary", "ema_80", "ema_120"),
        "S03": ("primary", "breakout_40", "breakout_70"),
        "S04": ("primary", "rsi_5", "rsi_15"),
        "S05": ("primary", "band_1.75", "band_2.25"),
        "S06": ("primary", "ratio_sma_15", "ratio_sma_25"),
        "S07": ("primary", "vol_target_20", "vol_target_30"),
        "S08": ("primary", "breakout_15", "breakout_25"),
        "S09": ("primary", "contraction_15", "contraction_25"),
        "S10": ("primary", "trend_share_60", "trend_share_80"),
    }[strategy_id]
