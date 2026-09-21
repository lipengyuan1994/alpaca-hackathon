"""Point-in-time indicator helpers used by the ten ETF strategies."""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(values: pd.Series, period: int) -> pd.Series:
    return values.rolling(period, min_periods=period).mean()


def ema(values: pd.Series, period: int) -> pd.Series:
    if len(values) < period:
        return pd.Series(np.nan, index=values.index, dtype=float)
    result = values.astype(float).ewm(alpha=2.0 / (period + 1.0), adjust=False, min_periods=period).mean()
    first = values.iloc[:period].mean()
    result.iloc[period - 1] = first
    for index in range(period, len(values)):
        result.iloc[index] = (values.iloc[index] * 2.0 + result.iloc[index - 1] * (period - 1)) / (period + 1)
    return result


def returns(values: pd.Series, period: int = 1) -> pd.Series:
    return values.astype(float).pct_change(periods=period)


def rsi(values: pd.Series, period: int = 14) -> pd.Series:
    change = values.astype(float).diff()
    gain = change.clip(lower=0.0)
    loss = -change.clip(upper=0.0)
    average_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    average_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    ratio = average_gain / average_loss.replace(0.0, np.nan)
    output = 100.0 - (100.0 / (1.0 + ratio))
    output[(average_loss == 0.0) & (average_gain > 0.0)] = 100.0
    output[(average_loss == 0.0) & (average_gain == 0.0)] = 50.0
    return output


def true_range(frame: pd.DataFrame) -> pd.Series:
    previous_close = frame["close"].shift(1)
    return pd.concat(
        [frame["high"] - frame["low"], (frame["high"] - previous_close).abs(), (frame["low"] - previous_close).abs()],
        axis=1,
    ).max(axis=1)


def atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = true_range(frame)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def rolling_percentile(values: pd.Series, period: int, percentile: float) -> pd.Series:
    return values.rolling(period, min_periods=period).apply(lambda window: float(np.percentile(window, percentile)), raw=True)
