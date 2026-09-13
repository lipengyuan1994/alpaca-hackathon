"""Deterministic feature layer for Group B pair-cell replays.

This module computes the frozen delivered features of both Group B candidates
(``opening_range_breakout__all_feasible__o2_v1`` and
``gap_continuation__all_feasible__o2_v1``) from immutable data-steward minute
bars.  It is pure arithmetic over normalized frames: no network, no clock, no
randomness, no vendor data.  Every formula mirrors the frozen definitions in
``docs/research/GROUP_B_SEMICONDUCTOR_PLAN.md`` sections 5-7:

- one-minute IEX bars aggregate into ET half-open 15-minute intervals labeled
  by interval end with availability ``end + 1 second``;
- a missing minute or zero cumulative volume invalidates the interval;
- the opening range is the half-open ``[09:30, 10:00)`` ET minute window;
- gap features are computed on the split-adjusted continuous series with a
  60-full-session lookback and the frozen ``1e-6`` floors.

The module never claims an outcome: it produces feature dictionaries that the
frozen pure signals consume, plus session validity flags.  Missing history
produces ``None`` features (a declared ``NO_TRADE`` upstream), never an
improvised value.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping

import pandas as pd

_ET = "America/New_York"
_INTERVAL_MINUTES = 15
_FLOOR = Decimal("0.000001")
_GAP_LOOKBACK_SESSIONS = 60
_VOLUME_LOOKBACK_SESSIONS = 20
_FULL_SESSION_MINUTES = 390


def decimal_feature(value: float | None) -> Decimal | None:
    """Render one float feature as a deterministic ``Decimal`` or ``None``."""
    if value is None or not math.isfinite(value):
        return None
    return Decimal(f"{value:.10f}")


def feature_dictionary(values: Mapping[str, float | None]) -> dict[str, Decimal] | None:
    """Build a delivered-feature dict, or ``None`` when any value is missing."""
    rendered = {key: decimal_feature(value) for key, value in values.items()}
    if any(item is None for item in rendered.values()):
        return None
    return {key: value for key, value in rendered.items() if value is not None}  # type: ignore[misc]


@dataclass(frozen=True)
class SessionBars:
    """One symbol's valid minute bars for one regular session."""

    symbol: str
    date: str
    frame: pd.DataFrame  # ET-indexed one-minute bars: open/high/low/close/volume/vwap
    early_close: bool

    @property
    def valid(self) -> bool:
        return not self.early_close and not self.frame.empty


def split_sessions(bars: pd.DataFrame, *, symbol: str) -> dict[str, SessionBars]:
    """Group one symbol's minute bars into ET sessions with validity flags.

    The caller selects the adjustment basis of ``bars`` (split-adjusted for
    continuous features, raw for audit joins); grouping is basis-agnostic.
    """
    expected = bars[bars["symbol"] == symbol].copy()
    if expected.empty:
        return {}
    expected["event_time"] = pd.to_datetime(expected["event_time"], utc=True)
    expected = expected.sort_values("event_time", kind="stable")
    local = expected["event_time"].dt.tz_convert(_ET)
    expected["et"] = local
    expected["date"] = local.dt.date.astype(str)
    sessions: dict[str, SessionBars] = {}
    for date, day in expected.groupby("date", sort=True):
        minutes = len(day)
        sessions[str(date)] = SessionBars(
            symbol=symbol,
            date=str(date),
            frame=day.reset_index(drop=True),
            early_close=minutes < _FULL_SESSION_MINUTES,
        )
    return sessions


def aggregate_intervals(session: SessionBars) -> pd.DataFrame:
    """Aggregate one session's minutes into half-open 15-minute intervals.

    The returned frame is labeled by interval end (ET) with columns
    ``interval_end``, ``open``, ``high``, ``low``, ``close``, ``volume``,
    ``interval_vwap``, ``session_vwap``, ``complete``.  An interval is complete
    only when all fifteen minutes are present with positive volume and positive
    cumulative session volume.
    """
    rows: list[dict[str, Any]] = []
    cumulative_value = 0.0
    cumulative_volume = 0.0
    for _, minute in session.frame.iterrows():
        minute_volume = float(minute["volume"])
        minute_vwap = float(minute["vwap"])
        minute_ok = (
            math.isfinite(minute_volume)
            and math.isfinite(minute_vwap)
            and minute_volume > 0
        )
        contribution = minute_vwap * minute_volume if minute_ok else 0.0
        if minute_ok:
            cumulative_value += contribution
            cumulative_volume += minute_volume
        rows.append(
            {
                "et": minute["et"],
                "open": float(minute["open"]),
                "high": float(minute["high"]),
                "low": float(minute["low"]),
                "close": float(minute["close"]),
                "volume": minute_volume if math.isfinite(minute_volume) else 0.0,
                "value": contribution,
                "minute_ok": minute_ok,
                "cum_value": cumulative_value,
                "cum_volume": cumulative_volume,
            }
        )
    minutes = pd.DataFrame(rows)
    if minutes.empty:
        return pd.DataFrame(
            columns=[
                "bucket", "interval_end", "open", "high", "low", "close", "volume",
                "interval_vwap", "session_vwap", "complete",
            ]
        )
    position = (minutes["et"].dt.hour * 60 + minutes["et"].dt.minute - 570) // _INTERVAL_MINUTES
    minutes["bucket"] = position
    records: list[dict[str, Any]] = []
    for bucket, group in minutes.groupby("bucket", sort=True):
        end = group["et"].max() + pd.Timedelta(minutes=1)
        end_volume = float(group["cum_volume"].iloc[-1])
        end_value = float(group["cum_value"].iloc[-1])
        interval_volume = float(group["volume"].sum())
        interval_value = float(group["value"].sum())
        interval_vwap = interval_value / interval_volume if interval_volume > 0 else float("nan")
        records.append(
            {
                "bucket": int(bucket),
                "interval_end": end,
                "open": float(group["open"].iloc[0]),
                "high": float(group["high"].max()),
                "low": float(group["low"].min()),
                "close": float(group["close"].iloc[-1]),
                "volume": interval_volume,
                "interval_vwap": interval_vwap,
                "session_vwap": end_value / end_volume if end_volume > 0 else float("nan"),
                "complete": bool(
                    len(group) == _INTERVAL_MINUTES
                    and bool(group["minute_ok"].all())
                    and end_volume > 0
                ),
            }
        )
    return pd.DataFrame(records)


def last_completed_interval(intervals: pd.DataFrame, decision: pd.Timestamp) -> pd.Series | None:
    """Return the newest interval completed and available at ``decision`` (ET)."""
    if intervals.empty:
        return None
    usable = intervals[intervals["interval_end"] + pd.Timedelta(seconds=1) <= decision]
    usable = usable[usable["complete"]]
    if usable.empty:
        return None
    return usable.iloc[-1]


def opening_range(session: SessionBars) -> tuple[float, float, float] | None:
    """Return ``(or_high, or_low, or_width_log)`` for ``[09:30, 10:00)`` ET."""
    window = session.frame[
        (session.frame["et"].dt.hour == 9) & (session.frame["et"].dt.minute >= 30)
    ]
    if len(window) != 30:
        return None
    high = float(window["high"].max())
    low = float(window["low"].min())
    if low <= 0 or high < low:
        return None
    return high, low, max(math.log(high / low), 1e-6)


def session_gap(sessions: Mapping[str, SessionBars], ordered_dates: list[str], date: str) -> float | None:
    """Log gap of the split-adjusted open against the prior regular close."""
    index = ordered_dates.index(date)
    if index == 0:
        return None
    prior = sessions.get(ordered_dates[index - 1])
    current = sessions.get(date)
    if prior is None or current is None or not prior.valid or not current.valid:
        return None
    prior_close = float(prior.frame["close"].iloc[-1])
    open_price = float(current.frame["open"].iloc[0])
    if prior_close <= 0 or open_price <= 0:
        return None
    return math.log(open_price / prior_close)


def sigma_gap_60(sessions: Mapping[str, SessionBars], ordered_dates: list[str], date: str) -> float | None:
    """Sample standard deviation of the prior 60 full-session gap returns."""
    index = ordered_dates.index(date)
    if index < _GAP_LOOKBACK_SESSIONS:
        return None
    gaps: list[float] = []
    for position in range(index - _GAP_LOOKBACK_SESSIONS, index):
        gap = session_gap(sessions, ordered_dates, ordered_dates[position])
        if gap is None:
            return None
        gaps.append(gap)
    mean = sum(gaps) / len(gaps)
    variance = sum((gap - mean) ** 2 for gap in gaps) / (len(gaps) - 1)
    return math.sqrt(variance)


def same_time_volume_median(
    sessions: Mapping[str, SessionBars],
    ordered_dates: list[str],
    date: str,
    interval_index: int,
    prior_intervals: Mapping[str, pd.DataFrame] | None = None,
) -> float | None:
    """Median same-time-interval volume over the prior 20 full sessions.

    ``prior_intervals`` supplies precomputed per-date interval frames so the
    replay engine can reuse its session cache instead of re-aggregating the
    same twenty sessions at every decision; when omitted the frames are
    aggregated on demand.
    """
    index = ordered_dates.index(date)
    if index < _VOLUME_LOOKBACK_SESSIONS:
        return None
    volumes: list[float] = []
    for position in range(index - _VOLUME_LOOKBACK_SESSIONS, index):
        prior_date = ordered_dates[position]
        prior = sessions.get(prior_date)
        if prior is None or not prior.valid:
            return None
        frame = prior_intervals.get(prior_date) if prior_intervals is not None else None
        if frame is None:
            frame = aggregate_intervals(prior)
        row = frame[frame["bucket"] == interval_index]
        if len(row) != 1 or not bool(row.iloc[0]["complete"]):
            return None
        volumes.append(float(row.iloc[0]["volume"]))
    ordered = sorted(volumes)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else 0.5 * (ordered[middle - 1] + ordered[middle])


def gap_features(
    sessions: Mapping[str, SessionBars],
    ordered_dates: list[str],
    date: str,
    interval: pd.Series,
) -> dict[str, float | None] | None:
    """Delivered features for the gap-continuation signal at 10:30:01 ET."""
    current = sessions.get(date)
    if current is None or not current.valid:
        return None
    gap = session_gap(sessions, ordered_dates, date)
    sigma = sigma_gap_60(sessions, ordered_dates, date)
    if gap is None or sigma is None:
        return None
    open_price = float(current.frame["open"].iloc[0])
    close = float(interval["close"])
    if open_price <= 0 or close <= 0:
        return None
    first_hour = math.log(close / open_price)
    return {
        "close_completed_15m_v1": close,
        "session_iex_vwap_v1": float(interval["session_vwap"]),
        "gap_log_adjusted_v1": gap,
        "sigma_gap_60_v1": sigma,
        "gap_z_60_v1": gap / max(sigma, 1e-6),
        "continuation_ratio_v1": (1.0 if gap >= 0 else -1.0) * first_hour / max(abs(gap), 1e-6),
    }


def breakout_features(
    session: SessionBars,
    interval: pd.Series,
    interval_index: int,
    sessions: Mapping[str, SessionBars],
    ordered_dates: list[str],
    date: str,
    prior_intervals: Mapping[str, pd.DataFrame] | None = None,
) -> dict[str, float | None] | None:
    """Delivered features for the opening-range-breakout signal."""
    if not session.valid:
        return None
    opening = opening_range(session)
    if opening is None:
        return None
    or_high, or_low, or_width = opening
    close = float(interval["close"])
    if close <= 0 or or_low <= 0:
        return None
    median = same_time_volume_median(
        sessions, ordered_dates, date, interval_index, prior_intervals=prior_intervals
    )
    if median is None or median <= 0:
        return None
    return {
        "close_completed_15m_v1": close,
        "session_iex_vwap_v1": float(interval["session_vwap"]),
        "opening_range_high_0930_1000_adjusted_v1": or_high,
        "opening_range_low_0930_1000_adjusted_v1": or_low,
        "opening_range_width_log_v1": or_width,
        "up_break_fraction_or30_v1": math.log(close / or_high) / or_width,
        "down_break_fraction_or30_v1": math.log(or_low / close) / or_width,
        "volume_ratio_same_time_20_v1": float(interval["volume"]) / median,
    }
