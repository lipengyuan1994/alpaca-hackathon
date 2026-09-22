"""Pure deterministic strategies for the leveraged ETF Track B study.

Track B trades one broad leveraged ETF (``B``) and ``SOXL`` (``S``).  The
corresponding unleveraged ETFs (``U`` and ``V``) are signal-only proxies:

* ``TQQQ`` uses ``QQQ`` as ``U`` and ``SOXX`` as ``V``;
* ``SPXL`` uses ``SPY`` as ``U`` and ``SOXX`` as ``V``.

The module deliberately has no broker, simulator, or account imports.  Each
strategy receives point-in-time daily frames and returns target weights for
the next execution session.  The caller may keep the strategy's internal
state, or pass a :class:`TrackBState` explicitly to ``evaluate`` when a
larger engine owns state persistence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .indicators import atr, ema, returns, rolling_percentile, rsi, sma

TRACK_B_STRATEGY_IDS = tuple(f"L{index:02d}" for index in range(1, 11))
_BROAD_SYMBOLS = {"TQQQ": "QQQ", "SPXL": "SPY"}


@dataclass(frozen=True)
class TrackSignal:
    """A deterministic point-in-time target decision.

    ``target_weights`` are fractions of total account equity.  An omitted
    tradable symbol means a zero target for that symbol.  ``entries`` and
    ``exits`` are transition diagnostics and are not execution instructions;
    the simulator remains responsible for sizing, affordability, settlement,
    and fills.
    """

    asof: pd.Timestamp
    target_weights: dict[str, float]
    reason_code: str
    entries: tuple[str, ...] = ()
    exits: tuple[str, ...] = ()
    component_target_weights: dict[str, dict[str, float]] = field(default_factory=dict)


# The existing ETF module calls this shape ``Signal``.  The alias makes Track
# B convenient to consume without coupling it to the QQQM-only implementation.
Signal = TrackSignal


@dataclass
class TrackBState:
    """Mutable strategy state required by stateful entry/exit rules."""

    active: dict[str, bool] = field(default_factory=dict)
    entry_index: dict[str, int] = field(default_factory=dict)
    arm_index: dict[str, int] = field(default_factory=dict)
    highest_close: dict[str, float] = field(default_factory=dict)
    cooldown_until: dict[str, int] = field(default_factory=dict)
    target_weight: dict[str, float] = field(default_factory=dict)
    selected: str | None = None
    last_weights: dict[str, float] = field(default_factory=dict)
    component_states: dict[str, "TrackBState"] = field(default_factory=dict)


def _clean_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    if "date" not in result.columns:
        raise ValueError("ETF_TRACK_B_DATE_MISSING")
    result["date"] = pd.to_datetime(result["date"], utc=True)
    for column in ("open", "high", "low", "close", "volume"):
        if column in result.columns:
            result[column] = pd.to_numeric(result[column], errors="coerce")
    return result.sort_values("date", kind="stable").reset_index(drop=True)


def _frame(frames: Mapping[str, pd.DataFrame], symbol: str) -> pd.DataFrame | None:
    value = frames.get(symbol)
    if value is None:
        return None
    # The simulator supplies normalized, sorted, numeric frames on every
    # call.  Avoid copying those large histories for every indicator request;
    # direct callers with raw/string frames still get the deterministic clean
    # path below.
    numeric_columns = ("open", "high", "low", "close")
    if (
        pd.api.types.is_datetime64_any_dtype(value["date"])
        and value["date"].is_monotonic_increasing
        and all(column in value.columns and pd.api.types.is_numeric_dtype(value[column]) for column in numeric_columns)
    ):
        return value
    return _clean_frame(value)


def _row(frames: Mapping[str, pd.DataFrame], symbol: str, index: int) -> pd.Series | None:
    frame = _frame(frames, symbol)
    if frame is None or index < 0 or index >= len(frame):
        return None
    return frame.iloc[index]


def _history(frames: Mapping[str, pd.DataFrame], symbol: str, index: int) -> pd.DataFrame:
    frame = _frame(frames, symbol)
    if frame is None:
        raise KeyError(symbol)
    return frame.iloc[: index + 1]


def _valid(value: Any) -> bool:
    try:
        return value is not None and not pd.isna(value) and np.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _date_boundary(row: pd.Series, previous: pd.Series | None, kind: str) -> bool:
    current = pd.Timestamp(row["date"])
    if previous is None:
        return True
    prior = pd.Timestamp(previous["date"])
    if kind == "month":
        return (current.year, current.month) != (prior.year, prior.month)
    current_week = current.isocalendar()
    prior_week = prior.isocalendar()
    return (current_week.year, current_week.week) != (prior_week.year, prior_week.week)


def _session_row(frames: Mapping[str, pd.DataFrame], symbol: str, index: int) -> pd.Series | None:
    """Return the reference row used for calendar boundaries."""

    return _row(frames, symbol, index)


def _past_return(history: pd.DataFrame, period: int) -> float:
    value = returns(history["close"], period).iloc[-1]
    return float(value) if _valid(value) else np.nan


def _annualized_volatility(history: pd.DataFrame, period: int = 60) -> float:
    """Annualized sample volatility of an *actual leveraged ETF* price path."""

    values = returns(history["close"], 1).dropna().tail(period)
    if len(values) < 2:
        return np.nan
    value = float(values.std(ddof=1) * np.sqrt(252.0))
    return value if np.isfinite(value) else np.nan


def _target_for_volatility(base_weight: float, volatility: float, target: float) -> float:
    if not _valid(volatility) or float(volatility) <= 0:
        return 0.0
    return min(float(base_weight), float(base_weight) * float(target) / float(volatility))


def _covariance_volatility(
    histories: Mapping[str, pd.DataFrame],
    weights: Mapping[str, float],
    period: int = 60,
) -> float:
    """Return annualized volatility using actual tradable ETF returns."""

    symbols = [symbol for symbol, weight in weights.items() if float(weight) > 0]
    if not symbols:
        return np.nan
    series = {symbol: returns(histories[symbol]["close"], 1) for symbol in symbols}
    joined = pd.DataFrame(series).dropna().tail(period)
    if len(joined) < 2:
        return np.nan
    covariance = joined.cov().to_numpy(dtype=float)
    vector = np.asarray([float(weights[symbol]) for symbol in symbols], dtype=float)
    variance = float(vector @ covariance @ vector * 252.0)
    if variance < 0 and variance > -1e-12:
        variance = 0.0
    return float(np.sqrt(variance)) if np.isfinite(variance) and variance >= 0 else np.nan


class LeveragedETFStrategy:
    """Stateful pure evaluator for L01-L10."""

    def __init__(
        self,
        strategy_id: str,
        broad_symbol: str,
        semiconductor: str = "SOXL",
        proxies: tuple[str, str] = ("QQQ", "SOXX"),
        variant: str = "primary",
    ) -> None:
        strategy_id = str(strategy_id).upper()
        broad_symbol = str(broad_symbol).upper()
        semiconductor = str(semiconductor).upper()
        proxies = tuple(str(item).upper() for item in proxies)
        if strategy_id not in TRACK_B_STRATEGY_IDS:
            raise ValueError("ETF_TRACK_B_STRATEGY_UNKNOWN")
        if broad_symbol not in _BROAD_SYMBOLS:
            raise ValueError("ETF_TRACK_B_BROAD_SYMBOL_UNKNOWN")
        # Keep the public default concise while making ``build_track_b(...,
        # "SPXL")`` select the documented SPY proxy automatically.  A caller
        # can still pass any explicit two-symbol tuple for a custom study.
        if broad_symbol == "SPXL" and proxies == ("QQQ", "SOXX"):
            proxies = ("SPY", "SOXX")
        if len(proxies) != 2 or proxies[0] != _BROAD_SYMBOLS[broad_symbol]:
            raise ValueError("ETF_TRACK_B_PROXY_CONFIGURATION_INVALID")
        if not proxies[1]:
            raise ValueError("ETF_TRACK_B_SECTOR_PROXY_MISSING")
        if not semiconductor:
            raise ValueError("ETF_TRACK_B_SEMICONDUCTOR_MISSING")

        self.strategy_id = strategy_id
        self.broad_symbol = broad_symbol
        self.semiconductor = semiconductor
        self.proxies = proxies
        self.broad_proxy, self.sector_proxy = proxies
        self.variant = str(variant)
        self.state = self._new_state()

        # L10 owns child state explicitly so a caller-provided TrackBState can
        # be serialized/replayed without relying on hidden child objects.
        self._trend_component: LeveragedETFStrategy | None = None
        self._pullback_component: LeveragedETFStrategy | None = None
        if strategy_id == "L10":
            self._trend_component = LeveragedETFStrategy("L02", broad_symbol, semiconductor, proxies, "primary")
            self._pullback_component = LeveragedETFStrategy("L06", broad_symbol, semiconductor, proxies, "primary")

    def _new_state(self) -> TrackBState:
        return TrackBState(active={self.broad_symbol: False, self.semiconductor: False})

    @property
    def symbols(self) -> tuple[str, str]:
        return self.broad_symbol, self.semiconductor

    @property
    def B(self) -> str:
        """Broad leveraged tradable symbol (the plan's ``B``)."""

        return self.broad_symbol

    @property
    def S(self) -> str:
        """Semiconductor leveraged tradable symbol (the plan's ``S``)."""

        return self.semiconductor

    @property
    def signal_symbols(self) -> tuple[str, str]:
        return self.proxies

    @property
    def U(self) -> str:
        """Broad unleveraged signal proxy (the plan's ``U``)."""

        return self.broad_proxy

    @property
    def V(self) -> str:
        """Semiconductor signal proxy (the plan's ``V``)."""

        return self.sector_proxy

    @property
    def all_symbols(self) -> tuple[str, ...]:
        return self.symbols + self.signal_symbols

    def _reference_row(self, frames: Mapping[str, pd.DataFrame], index: int) -> pd.Series | None:
        return _session_row(frames, self.broad_proxy, index)

    def _weekly(self, frames: Mapping[str, pd.DataFrame], index: int) -> bool:
        row = self._reference_row(frames, index)
        previous = _session_row(frames, self.broad_proxy, index - 1) if index else None
        return row is not None and _date_boundary(row, previous, "week")

    def _monthly(self, frames: Mapping[str, pd.DataFrame], index: int) -> bool:
        row = self._reference_row(frames, index)
        previous = _session_row(frames, self.broad_proxy, index - 1) if index else None
        return row is not None and _date_boundary(row, previous, "month")

    def _signal(
        self,
        frames: Mapping[str, pd.DataFrame],
        index: int,
        weights: Mapping[str, float],
        reason: str,
        entries: list[str],
        exits: list[str],
        component_target_weights: Mapping[str, Mapping[str, float]] | None = None,
    ) -> TrackSignal:
        row = self._reference_row(frames, index)
        asof = pd.Timestamp(row["date"]) if row is not None else pd.Timestamp("1970-01-01", tz="UTC")
        normalized = {
            symbol: min(0.99, max(0.0, float(weight)))
            for symbol, weight in weights.items()
            if _valid(weight) and float(weight) > 0
        }
        # Stable ordering makes serialized signals reproducible and easy to
        # compare in independent reconstruction tests.
        components = {
            str(component): {
                str(symbol): min(0.99, max(0.0, float(weight)))
                for symbol, weight in weights.items()
                if _valid(weight) and float(weight) > 0.0
            }
            for component, weights in (component_target_weights or {}).items()
        }
        return TrackSignal(
            asof,
            dict(sorted(normalized.items())),
            reason,
            tuple(sorted(set(entries))),
            tuple(sorted(set(exits))),
            components,
        )

    def _missing_signal(self, frames: Mapping[str, pd.DataFrame], index: int) -> TrackSignal:
        row = self._reference_row(frames, index)
        asof = pd.Timestamp(row["date"]) if row is not None else pd.Timestamp("1970-01-01", tz="UTC")
        return TrackSignal(asof, {}, "MISSING_SYMBOL")

    def evaluate(self, frames: Mapping[str, pd.DataFrame], index: int, state: TrackBState | None = None) -> TrackSignal:
        """Evaluate the strategy using bars through ``index`` only.

        In normal use ``index`` points to the last completed session before the
        next execution.  ``state`` is optional: if supplied, it becomes the
        state owner for this call and subsequent calls may pass the same object.
        """

        if state is not None:
            self.state = state
        if index < 0 or any(_row(frames, symbol, index) is None for symbol in self.all_symbols):
            return self._missing_signal(frames, index)
        dispatch = {
            "L01": self._l01,
            "L02": self._l02,
            "L03": self._l03,
            "L04": self._l04,
            "L05": self._l05,
            "L06": self._l06,
            "L07": self._l07,
            "L08": self._l08,
            "L09": self._l09,
            "L10": self._l10,
        }
        return dispatch[self.strategy_id](frames, index)

    def _proxy_history(self, frames: Mapping[str, pd.DataFrame], fund: str, index: int) -> pd.DataFrame:
        return _history(frames, self.broad_proxy if fund == self.broad_symbol else self.sector_proxy, index)

    def _fund_history(self, frames: Mapping[str, pd.DataFrame], fund: str, index: int) -> pd.DataFrame:
        return _history(frames, fund, index)

    def _eligibility(self, frames: Mapping[str, pd.DataFrame], fund: str, index: int) -> tuple[bool, float, float]:
        history = self._proxy_history(frames, fund, index)
        trend = sma(history["close"], 200).iloc[-1]
        r63 = _past_return(history, 63)
        close = float(history.iloc[-1]["close"])
        eligible = _valid(trend) and _valid(r63) and close > float(trend) and r63 > 0
        return bool(eligible), r63, float(trend) if _valid(trend) else np.nan

    def _choose_momentum(self, frames: Mapping[str, pd.DataFrame], index: int, horizon: int = 126) -> str | None:
        candidates: list[tuple[float, str]] = []
        for fund in self.symbols:
            eligible, r63, _ = self._eligibility(frames, fund, index)
            long_return = _past_return(self._proxy_history(frames, fund, index), horizon)
            if eligible and _valid(long_return):
                # Tie order intentionally favors the broad leveraged ETF.
                score = 0.5 * float(r63) + 0.5 * float(long_return)
                candidates.append((score, fund))
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item[0], 1 if item[1] == self.broad_symbol else 0))[1]

    def _drop_active(self, fund: str, exits: list[str]) -> None:
        if self.state.active.get(fund, False):
            self.state.active[fund] = False
            self.state.target_weight.pop(fund, None)
            exits.append(fund)

    def _select_on_review(
        self,
        frames: Mapping[str, pd.DataFrame],
        index: int,
        review: bool,
        horizon: int,
        target_builder,
        reason: str,
    ) -> TrackSignal | None:
        """Shared monthly/weekly rotation path used by L01, L02 and L08."""

        entries: list[str] = []
        exits: list[str] = []
        weights: dict[str, float] = {}
        # Daily proxy trend exits are evaluated regardless of review cadence.
        selected = self.state.selected
        if selected:
            eligible, _, trend = self._eligibility(frames, selected, index)
            proxy_history = self._proxy_history(frames, selected, index)
            close = float(proxy_history.iloc[-1]["close"])
            if _valid(trend) and close < float(trend):
                self._drop_active(selected, exits)
                selected = None
                self.state.selected = None

        if review:
            winner = self._choose_momentum(frames, index, horizon)
            if winner != selected:
                if selected:
                    self._drop_active(selected, exits)
                self.state.selected = winner
                selected = winner
            if selected:
                was_active = self.state.active.get(selected, False)
                proposed = float(target_builder(selected, frames, index))
                if proposed > 0:
                    self.state.active[selected] = True
                    self.state.target_weight[selected] = proposed
                    if not was_active:
                        entries.append(selected)
                else:
                    # Invalid volatility blocks an increase while leaving the
                    # selection observable for the next scheduled review.
                    self._drop_active(selected, exits)
                self.state.last_weights = {selected: proposed} if proposed > 0 else {}
        if selected and self.state.active.get(selected, False):
            weights[selected] = self.state.target_weight.get(selected, self.state.last_weights.get(selected, 0.0))
        return self._signal(frames, index, weights, reason, entries, exits)

    def _l01(self, frames: Mapping[str, pd.DataFrame], index: int) -> TrackSignal:
        return self._select_on_review(
            frames,
            index,
            self._monthly(frames, index),
            126 if self.variant == "primary" else _variant_int(self.variant, "long_"),
            lambda _fund, _frames, _index: 0.99,
            "L01_MONTHLY_PROXY_MOMENTUM",
        )

    def _l02(self, frames: Mapping[str, pd.DataFrame], index: int) -> TrackSignal:
        target = 0.40 if self.variant == "primary" else _variant_percent(self.variant, "vol_target_")

        def target_builder(fund: str, source: Mapping[str, pd.DataFrame], position: int) -> float:
            volatility = _annualized_volatility(self._fund_history(source, fund, position), 60)
            return _target_for_volatility(0.99, volatility, target)

        return self._select_on_review(
            frames,
            index,
            self._weekly(frames, index),
            126,
            target_builder,
            "L02_WEEKLY_VOLATILITY_TARGETED_MOMENTUM",
        )

    def _l03(self, frames: Mapping[str, pd.DataFrame], index: int) -> TrackSignal:
        slow_period = 100 if self.variant == "primary" else _variant_int(self.variant, "ema_")
        entries: list[str] = []
        exits: list[str] = []
        weights: dict[str, float] = {}
        for fund in self.symbols:
            history = self._proxy_history(frames, fund, index)
            close = float(history.iloc[-1]["close"])
            fast = ema(history["close"], 20).iloc[-1]
            slow = ema(history["close"], slow_period).iloc[-1]
            trend = sma(history["close"], 200).iloc[-1]
            qualifies = _valid(fast) and _valid(slow) and _valid(trend) and fast > slow and close > trend
            active = self.state.active.get(fund, False)
            if qualifies and not active:
                self.state.active[fund] = True
                self.state.target_weight[fund] = 0.495
                entries.append(fund)
            elif not qualifies and active:
                self._drop_active(fund, exits)
            if self.state.active.get(fund, False):
                weights[fund] = self.state.target_weight.get(fund, 0.495)
        return self._signal(frames, index, weights, "L03_DUAL_PROXY_TREND_SLEEVES", entries, exits)

    def _l04(self, frames: Mapping[str, pd.DataFrame], index: int) -> TrackSignal:
        target = 0.40 if self.variant == "primary" else _variant_percent(self.variant, "vol_target_")
        entries: list[str] = []
        exits: list[str] = []
        weights = dict(self.state.last_weights)
        if self._weekly(frames, index):
            nominal = {self.broad_symbol: 0.60, self.semiconductor: 0.40}
            eligible: dict[str, float] = {}
            for fund, base in nominal.items():
                ok, _, _ = self._eligibility(frames, fund, index)
                if ok:
                    eligible[fund] = base
            history_map = {fund: self._fund_history(frames, fund, index) for fund in self.symbols}
            volatility = _covariance_volatility(history_map, eligible, 60)
            if eligible and _valid(volatility):
                multiplier = min(1.0, target / float(volatility)) if volatility > 0 else 1.0
                weights = {fund: 0.99 * base * multiplier for fund, base in eligible.items()}
                self.state.last_weights = dict(weights)
            elif not eligible:
                # A proxy trend exit is an intentional scheduled reduction.
                weights = {}
                self.state.last_weights = {}
            # Invalid covariance blocks increases and retains the prior target.
            previous_active = set(self.state.active)
            for fund in self.symbols:
                was_active = self.state.active.get(fund, False)
                now_active = float(weights.get(fund, 0.0)) > 0
                if was_active and not now_active:
                    self._drop_active(fund, exits)
                elif now_active and not was_active:
                    self.state.active[fund] = True
                    self.state.target_weight[fund] = float(weights[fund])
                    entries.append(fund)
                elif now_active:
                    self.state.target_weight[fund] = float(weights[fund])
            del previous_active  # keeps the review branch explicit and lint-clean
        # Daily proxy exits apply between weekly reviews too.
        for fund in self.symbols:
            if not self.state.active.get(fund, False):
                continue
            history = self._proxy_history(frames, fund, index)
            trend = sma(history["close"], 200).iloc[-1]
            if _valid(trend) and float(history.iloc[-1]["close"]) < float(trend):
                self._drop_active(fund, exits)
                weights.pop(fund, None)
        for fund in self.symbols:
            if self.state.active.get(fund, False):
                weights[fund] = self.state.target_weight.get(fund, weights.get(fund, 0.0))
        return self._signal(frames, index, weights, "L04_COVARIANCE_CONTROLLED_GROWTH", entries, exits)

    def _l05(self, frames: Mapping[str, pd.DataFrame], index: int) -> TrackSignal:
        atr_multiple = 3.0 if self.variant == "primary" else _variant_float(self.variant, "atr_")
        entries: list[str] = []
        exits: list[str] = []
        weights: dict[str, float] = {}
        for fund in self.symbols:
            proxy = self._proxy_history(frames, fund, index)
            actual = self._fund_history(frames, fund, index)
            proxy_close = float(proxy.iloc[-1]["close"])
            actual_close = float(actual.iloc[-1]["close"])
            prior_high = proxy["high"].shift(1).rolling(55, min_periods=55).max().iloc[-1]
            prior_low = proxy["low"].shift(1).rolling(20, min_periods=20).min().iloc[-1]
            actual_atr = atr(actual, 14).iloc[-1]
            active = self.state.active.get(fund, False)
            if active:
                self.state.highest_close[fund] = max(self.state.highest_close.get(fund, actual_close), actual_close)
                trailing = self.state.highest_close[fund] - atr_multiple * float(actual_atr) if _valid(actual_atr) else np.nan
                should_exit = (_valid(prior_low) and proxy_close < float(prior_low)) or (_valid(trailing) and actual_close < trailing)
                if should_exit:
                    self._drop_active(fund, exits)
                    self.state.highest_close.pop(fund, None)
            elif _valid(prior_high) and _valid(actual_atr) and actual_atr > 0 and _valid(sma(proxy["close"], 200).iloc[-1]) and proxy_close > float(prior_high) and proxy_close > float(sma(proxy["close"], 200).iloc[-1]):
                risk_fraction = float(atr_multiple * actual_atr / actual_close)
                entry_weight = min(0.495, 0.02 / risk_fraction) if risk_fraction > 0 else 0.0
                if entry_weight > 0:
                    self.state.active[fund] = True
                    self.state.entry_index[fund] = index
                    self.state.highest_close[fund] = actual_close
                    self.state.target_weight[fund] = entry_weight
                    entries.append(fund)
            if self.state.active.get(fund, False):
                weights[fund] = self.state.target_weight.get(fund, 0.0)
        return self._signal(frames, index, weights, "L05_LEVERAGED_BREAKOUT_RISK_BUDGET", entries, exits)

    def _l06(self, frames: Mapping[str, pd.DataFrame], index: int) -> TrackSignal:
        threshold = 10.0 if self.variant == "primary" else _variant_float(self.variant, "rsi_")
        entries: list[str] = []
        exits: list[str] = []
        weights: dict[str, float] = {}
        for fund in self.symbols:
            history = self._proxy_history(frames, fund, index)
            close = float(history.iloc[-1]["close"])
            trend = sma(history["close"], 200).iloc[-1]
            fast = sma(history["close"], 50).iloc[-1]
            value = rsi(history["close"], 2).iloc[-1]
            active = self.state.active.get(fund, False)
            if active:
                age = index - self.state.entry_index.get(fund, index) + 1
                if (_valid(value) and value > 70) or (_valid(fast) and close < float(fast)) or age >= 5:
                    self._drop_active(fund, exits)
                    self.state.cooldown_until[fund] = index + 2
            else:
                ready = index > self.state.cooldown_until.get(fund, -1)
                qualifies = ready and _valid(trend) and _valid(fast) and _valid(value) and close > float(trend) and fast > float(trend) and value < threshold
                if qualifies:
                    self.state.active[fund] = True
                    self.state.entry_index[fund] = index
                    self.state.target_weight[fund] = 0.33
                    entries.append(fund)
            if self.state.active.get(fund, False):
                weights[fund] = self.state.target_weight.get(fund, 0.33)
        return self._signal(frames, index, weights, "L06_TACTICAL_OVERSOLD_REBOUND", entries, exits)

    def _l07(self, frames: Mapping[str, pd.DataFrame], index: int) -> TrackSignal:
        percentile = 20.0 if self.variant == "primary" else _variant_float(self.variant, "contraction_")
        entries: list[str] = []
        exits: list[str] = []
        weights: dict[str, float] = {}
        for fund in self.symbols:
            history = self._proxy_history(frames, fund, index)
            close = float(history.iloc[-1]["close"])
            middle = sma(history["close"], 20).iloc[-1]
            deviation = history["close"].rolling(20, min_periods=20).std().iloc[-1]
            bandwidth = 4.0 * deviation / middle if _valid(deviation) and _valid(middle) and float(middle) != 0 else np.nan
            bandwidth_series = 4.0 * history["close"].rolling(20, min_periods=20).std() / sma(history["close"], 20)
            threshold = rolling_percentile(bandwidth_series.shift(1), 126, percentile).iloc[-1]
            contraction = _valid(bandwidth) and _valid(threshold) and float(bandwidth) < float(threshold)
            active = self.state.active.get(fund, False)
            if not active and contraction:
                self.state.arm_index[fund] = index
            prior_high = history["high"].shift(1).rolling(20, min_periods=20).max().iloc[-1]
            trend = sma(history["close"], 100).iloc[-1]
            if active:
                age = index - self.state.entry_index.get(fund, index) + 1
                fast = ema(history["close"], 20).iloc[-1]
                if (_valid(fast) and close < float(fast)) or age >= 20:
                    self._drop_active(fund, exits)
            else:
                arm = self.state.arm_index.get(fund)
                if arm is not None and index - arm <= 10 and _valid(prior_high) and _valid(trend) and close > float(prior_high) and close > float(trend):
                    self.state.active[fund] = True
                    self.state.entry_index[fund] = index
                    self.state.target_weight[fund] = 0.495
                    self.state.arm_index.pop(fund, None)
                    entries.append(fund)
                elif arm is not None and index - arm > 10:
                    self.state.arm_index.pop(fund, None)
            if self.state.active.get(fund, False):
                weights[fund] = self.state.target_weight.get(fund, 0.495)
        return self._signal(frames, index, weights, "L07_VOLATILITY_CONTRACTION_BREAKOUT", entries, exits)

    def _l08(self, frames: Mapping[str, pd.DataFrame], index: int) -> TrackSignal:
        target = 0.50 if self.variant == "primary" else _variant_percent(self.variant, "vol_target_")
        entries: list[str] = []
        exits: list[str] = []
        weights: dict[str, float] = {}
        selected = self.state.selected
        if selected:
            proxy = self._proxy_history(frames, selected, index)
            trend = sma(proxy["close"], 100).iloc[-1]
            if _valid(trend) and float(proxy.iloc[-1]["close"]) < float(trend):
                self._drop_active(selected, exits)
                selected = None
                self.state.selected = None
        if self._weekly(frames, index):
            broad = _history(frames, self.broad_proxy, index)
            sector = _history(frames, self.sector_proxy, index)
            ratio = sector["close"].to_numpy(dtype=float) / broad["close"].to_numpy(dtype=float)
            ratio_series = pd.Series(ratio, index=sector.index)
            sector_trend = sma(sector["close"], 100).iloc[-1]
            ratio_trend = sma(ratio_series, 20).iloc[-1]
            sector_momentum = _past_return(sector, 63)
            broad_momentum = _past_return(broad, 63)
            if _valid(sector_trend) and _valid(ratio_trend) and _valid(sector_momentum) and _valid(broad_momentum) and float(sector.iloc[-1]["close"]) > float(sector_trend) and float(ratio[-1]) > float(ratio_trend) and sector_momentum > broad_momentum:
                winner = self.semiconductor
            else:
                broad_trend = sma(broad["close"], 100).iloc[-1]
                winner = self.broad_symbol if _valid(broad_trend) and float(broad.iloc[-1]["close"]) > float(broad_trend) else None
            if winner != selected:
                if selected:
                    self._drop_active(selected, exits)
                self.state.selected = winner
                selected = winner
            if selected:
                volatility = _annualized_volatility(self._fund_history(frames, selected, index), 60)
                proposed = _target_for_volatility(0.99, volatility, target)
                was_active = self.state.active.get(selected, False)
                if proposed > 0:
                    self.state.active[selected] = True
                    self.state.target_weight[selected] = proposed
                    if not was_active:
                        entries.append(selected)
                    self.state.last_weights = {selected: proposed}
                else:
                    self._drop_active(selected, exits)
                    self.state.last_weights = {}
        if selected and self.state.active.get(selected, False):
            weights[selected] = self.state.target_weight.get(selected, self.state.last_weights.get(selected, 0.0))
        return self._signal(frames, index, weights, "L08_SEMICONDUCTOR_LEADERSHIP_FALLBACK", entries, exits)

    def _l09(self, frames: Mapping[str, pd.DataFrame], index: int) -> TrackSignal:
        cooldown_sessions = 10 if self.variant == "primary" else _variant_int(self.variant, "cooldown_")
        entries: list[str] = []
        exits: list[str] = []
        weights = dict(self.state.last_weights)
        nominal = {self.broad_symbol: 0.60, self.semiconductor: 0.40}
        # Shock and trend exits are checked each completed session.
        for fund in self.symbols:
            proxy = self._proxy_history(frames, fund, index)
            one_day = _past_return(proxy, 1)
            five_day = _past_return(proxy, 5)
            trend = sma(proxy["close"], 200).iloc[-1]
            shock = (_valid(one_day) and one_day <= -0.04) or (_valid(five_day) and five_day <= -0.08)
            trend_exit = _valid(trend) and float(proxy.iloc[-1]["close"]) < float(trend)
            if self.state.active.get(fund, False) and (shock or trend_exit):
                self._drop_active(fund, exits)
                weights.pop(fund, None)
                if shock:
                    self.state.cooldown_until[fund] = index + cooldown_sessions
        if self._weekly(frames, index):
            proposed: dict[str, float] = {}
            for fund, base in nominal.items():
                cooldown_ready = index > self.state.cooldown_until.get(fund, -1)
                if not cooldown_ready:
                    continue
                proxy = self._proxy_history(frames, fund, index)
                close = float(proxy.iloc[-1]["close"])
                trend = sma(proxy["close"], 200).iloc[-1]
                fast = ema(proxy["close"], 20).iloc[-1]
                long_return = _past_return(proxy, 63)
                points = sum(
                    [
                        bool(_valid(trend) and close > float(trend)),
                        bool(_valid(fast) and _valid(ema(proxy["close"], 100).iloc[-1]) and fast > float(ema(proxy["close"], 100).iloc[-1])),
                        bool(_valid(long_return) and long_return > 0),
                    ]
                )
                proposed[fund] = 0.99 * base * points / 3.0
            # Zero-point funds intentionally receive a zero target at review.
            new_weights = {fund: value for fund, value in proposed.items() if value > 0}
            for fund in self.symbols:
                was_active = self.state.active.get(fund, False)
                now_active = float(new_weights.get(fund, 0.0)) > 0
                if was_active and not now_active:
                    self._drop_active(fund, exits)
                elif now_active and not was_active:
                    self.state.active[fund] = True
                    entries.append(fund)
                if now_active:
                    self.state.target_weight[fund] = float(new_weights[fund])
            weights = new_weights
            self.state.last_weights = dict(weights)
        for fund in self.symbols:
            if self.state.active.get(fund, False):
                weights[fund] = self.state.target_weight.get(fund, weights.get(fund, 0.0))
        return self._signal(frames, index, weights, "L09_EXPOSURE_LADDER_SHOCK_COOLDOWN", entries, exits)

    def _l10(self, frames: Mapping[str, pd.DataFrame], index: int) -> TrackSignal:
        assert self._trend_component is not None and self._pullback_component is not None
        share = 0.70 if self.variant == "primary" else _variant_percent(self.variant, "trend_share_")
        trend_state = self.state.component_states.setdefault("L02", self._trend_component._new_state())
        pullback_state = self.state.component_states.setdefault("L06", self._pullback_component._new_state())
        trend_signal = self._trend_component.evaluate(frames, index, trend_state)
        pullback_signal = self._pullback_component.evaluate(frames, index, pullback_state)
        weights: dict[str, float] = {}
        for symbol, weight in trend_signal.target_weights.items():
            weights[symbol] = weights.get(symbol, 0.0) + share * float(weight)
        for symbol, weight in pullback_signal.target_weights.items():
            weights[symbol] = weights.get(symbol, 0.0) + (1.0 - share) * float(weight)
        return self._signal(
            frames,
            index,
            weights,
            "L10_LEVERAGED_TREND_PULLBACK_ENSEMBLE",
            list(trend_signal.entries) + list(pullback_signal.entries),
            list(trend_signal.exits) + list(pullback_signal.exits),
            {
                "L02": {symbol: share * weight for symbol, weight in trend_signal.target_weights.items()},
                "L06": {symbol: (1.0 - share) * weight for symbol, weight in pullback_signal.target_weights.items()},
            },
        )


def _variant_int(variant: str, prefix: str) -> int:
    if not str(variant).startswith(prefix):
        raise ValueError(f"ETF_TRACK_B_VARIANT_INVALID:{variant}")
    return int(str(variant)[len(prefix) :])


def _variant_float(variant: str, prefix: str) -> float:
    if not str(variant).startswith(prefix):
        raise ValueError(f"ETF_TRACK_B_VARIANT_INVALID:{variant}")
    return float(str(variant)[len(prefix) :])


def _variant_percent(variant: str, prefix: str) -> float:
    return _variant_float(variant, prefix) / 100.0


def build_track_b(
    strategy_id: str,
    broad_symbol: str,
    semiconductor: str = "SOXL",
    proxies: tuple[str, str] = ("QQQ", "SOXX"),
    variant: str = "primary",
) -> LeveragedETFStrategy:
    """Build one Track B strategy for TQQQ+SOXL or SPXL+SOXL."""

    return LeveragedETFStrategy(strategy_id, broad_symbol, semiconductor, proxies, variant)


def strategy_variants(strategy_id: str) -> tuple[str, ...]:
    """Return the frozen primary plus two diagnostic variant identifiers."""

    return {
        "L01": ("primary", "long_105", "long_147"),
        "L02": ("primary", "vol_target_30", "vol_target_50"),
        "L03": ("primary", "ema_80", "ema_120"),
        "L04": ("primary", "vol_target_30", "vol_target_50"),
        "L05": ("primary", "atr_2.5", "atr_3.5"),
        "L06": ("primary", "rsi_5", "rsi_15"),
        "L07": ("primary", "contraction_15", "contraction_25"),
        "L08": ("primary", "vol_target_40", "vol_target_60"),
        "L09": ("primary", "cooldown_5", "cooldown_15"),
        "L10": ("primary", "trend_share_60", "trend_share_80"),
    }[str(strategy_id).upper()]


__all__ = [
    "TRACK_B_STRATEGY_IDS",
    "LeveragedETFStrategy",
    "Signal",
    "TrackBState",
    "TrackSignal",
    "build_track_b",
    "strategy_variants",
]
