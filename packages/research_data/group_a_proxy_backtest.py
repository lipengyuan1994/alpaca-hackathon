"""Deterministic, non-executable Group A debit-spread proxy replay."""

from __future__ import annotations

import json
import math
from html import escape
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from packages.contracts.canonical import canonical_hash

from .artifacts import atomic_bytes, atomic_json, ensure_empty_output, file_hash, write_parquet


class GroupAReplayError(ValueError):
    pass


_SUPPORTED_EXIT_MINUTES = {45, 60, 90, 240}


def _cumulative_pnl_svg(curves: Mapping[str, list[dict[str, Any]]]) -> bytes:
    """Render a deterministic, dependency-free cumulative-P&L SVG artifact."""
    width, height = 960, 500
    left, right, top, bottom = 92, 30, 38, 72
    dates = sorted({str(row["date"]) for rows in curves.values() for row in rows})
    if not dates:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            'viewBox="0 0 960 500" role="img" aria-label="No option proxy fills">'
            '<text x="480" y="250" text-anchor="middle" font-family="sans-serif" font-size="20">'
            'No option-proxy fills</text></svg>'
        ).encode("utf-8")
    values: dict[str, list[float]] = {}
    for name, rows in sorted(curves.items()):
        by_date = {str(row["date"]): float(row["daily_pnl"]) for row in rows}
        running = 0.0
        series: list[float] = []
        for date in dates:
            running += by_date.get(date, 0.0)
            series.append(running)
        values[name] = series
    minimum = min(0.0, *(value for series in values.values() for value in series))
    maximum = max(0.0, *(value for series in values.values() for value in series))
    padding = max(1.0, (maximum - minimum) * 0.08)
    y_min, y_max = minimum - padding, maximum + padding
    chart_width, chart_height = width - left - right, height - top - bottom

    def x_value(index: int) -> float:
        return left + chart_width * index / max(1, len(dates) - 1)

    def y_value(value: float) -> float:
        return top + chart_height * (y_max - value) / (y_max - y_min)

    palette = ("#2563eb", "#dc2626", "#16a34a", "#9333ea", "#d97706", "#0891b2")
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="Cumulative option proxy P and L">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<text x="480" y="24" text-anchor="middle" font-family="sans-serif" font-size="18">Cumulative option-proxy P&amp;L</text>',
        f'<rect x="{left}" y="{top}" width="{chart_width}" height="{chart_height}" fill="none" stroke="#6b7280"/>',
    ]
    zero_y = y_value(0.0)
    parts.append(f'<line x1="{left}" y1="{zero_y:.2f}" x2="{width - right}" y2="{zero_y:.2f}" stroke="#9ca3af" stroke-dasharray="4 4"/>')
    for fraction in range(5):
        value = y_min + (y_max - y_min) * fraction / 4
        y = y_value(value)
        parts.append(f'<line x1="{left - 5}" y1="{y:.2f}" x2="{left}" y2="{y:.2f}" stroke="#374151"/>')
        parts.append(f'<text x="{left - 9}" y="{y + 4:.2f}" text-anchor="end" font-family="sans-serif" font-size="11">${value:,.0f}</text>')
    tick_indices = (
        [0]
        if len(dates) == 1
        else sorted(
            {
                round(index * (len(dates) - 1) / min(5, len(dates) - 1))
                for index in range(min(6, len(dates)))
            }
        )
    )
    for index in tick_indices:
        x = x_value(index)
        parts.append(f'<line x1="{x:.2f}" y1="{height - bottom}" x2="{x:.2f}" y2="{height - bottom + 5}" stroke="#374151"/>')
        parts.append(f'<text x="{x:.2f}" y="{height - bottom + 22}" text-anchor="middle" font-family="sans-serif" font-size="11">{escape(dates[index])}</text>')
    for position, (name, series) in enumerate(sorted(values.items())):
        color = palette[position % len(palette)]
        points = " ".join(f"{x_value(index):.2f},{y_value(value):.2f}" for index, value in enumerate(series))
        parts.append(f'<polyline fill="none" stroke="{color}" stroke-width="2.5" points="{points}"/>')
        parts.append(f'<text x="{left}" y="{top + 20 + 18 * position}" font-family="sans-serif" font-size="12" fill="{color}">{escape(name)}</text>')
    parts.append('</svg>')
    return "".join(parts).encode("utf-8")


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GroupAReplayError("REPLAY_MANIFEST_INVALID") from exc
    if value.get("manifest_hash") != canonical_hash({key: item for key, item in value.items() if key != "manifest_hash"}):
        raise GroupAReplayError("REPLAY_MANIFEST_HASH_MISMATCH")
    return value


def _dataset(manifest: dict[str, Any], dataset_id: str) -> dict[str, Any]:
    value = next((item for item in manifest["datasets"] if item.get("dataset_id") == dataset_id), None)
    if not isinstance(value, dict):
        raise GroupAReplayError(f"REPLAY_DATASET_MISSING_{dataset_id}")
    return value


def _bar(root: Path, dataset: dict[str, Any]) -> pd.DataFrame:
    artifact = dataset.get("artifact", {})
    path = root / str(artifact.get("path", ""))
    if not path.is_file() or file_hash(path) != artifact.get("sha256"):
        raise GroupAReplayError("REPLAY_DATASET_HASH_MISMATCH")
    result = pd.read_parquet(
        path,
        columns=["symbol", "event_time", "open", "high", "low", "close", "volume", "vwap"],
    )
    result["event_time"] = pd.to_datetime(result["event_time"], utc=True)
    for column in ("open", "high", "low", "close", "volume", "vwap"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    return result.dropna()


def _buffer(row: pd.Series, leg: str) -> float:
    return max(
        0.05,
        0.10 * float(row[f"open_{leg}"]),
        0.25 * (float(row[f"high_{leg}"]) - float(row[f"low_{leg}"])),
    )


def _completed_underlying_intervals(
    underlying_bars: pd.DataFrame,
) -> dict[tuple[str, object], pd.DataFrame]:
    """Precompute complete session VWAP intervals for policy-only lookups."""
    bars = underlying_bars.copy()
    bars["local_time"] = pd.to_datetime(bars["event_time"], utc=True).dt.tz_convert(
        "America/New_York"
    )
    bars = bars[
        (bars["local_time"].dt.weekday < 5)
        & (bars["local_time"].dt.time >= pd.Timestamp("09:30").time())
        & (bars["local_time"].dt.time < pd.Timestamp("16:00").time())
    ].sort_values(["symbol", "local_time"])
    bars["session"] = bars["local_time"].dt.date
    bars["bucket"] = bars["local_time"].dt.floor("15min") + pd.Timedelta(minutes=15)
    bars["pv"] = bars["vwap"] * bars["volume"]
    grouped = bars.groupby(["symbol", "session"], sort=False)
    bars["cum_pv"] = grouped["pv"].cumsum()
    bars["cum_volume"] = grouped["volume"].cumsum()
    completed = grouped.tail(1).copy()
    completed["session_vwap"] = completed["cum_pv"] / completed["cum_volume"]
    return {
        (symbol, session): values[["bucket", "close", "session_vwap"]].reset_index(drop=True)
        for (symbol, session), values in completed.groupby(["symbol", "session"], sort=False)
    }


def _complete_market_dates(underlying_bars: pd.DataFrame, *, start: str, end: str) -> list[str]:
    """Return every regular-session date in an inclusive UTC decision-date range."""
    timestamps = pd.to_datetime(underlying_bars["event_time"], utc=True).dt.tz_convert("America/New_York")
    dates = pd.Series(timestamps.dt.date.astype(str)).drop_duplicates().sort_values()
    return [date for date in dates if start <= date <= end]


def _session_exit_time(
    record: dict[str, Any],
    *,
    underlying_bars: pd.DataFrame | Mapping[str, pd.DataFrame] | None,
    fill_time: pd.Timestamp,
) -> pd.Timestamp | None:
    """Resolve a future regular-session exit from a completed-session count."""
    holding_sessions = record.get("holding_sessions")
    if holding_sessions is None or underlying_bars is None:
        return None
    holding = int(holding_sessions)
    if holding < 1 or holding > 5:
        raise GroupAReplayError("REPLAY_HOLDING_SESSIONS_INVALID")
    if isinstance(underlying_bars, Mapping):
        sessions = sorted(
            session for symbol, session in underlying_bars if symbol == record["underlying"]
        )
    else:
        local = pd.to_datetime(underlying_bars["event_time"], utc=True).dt.tz_convert("America/New_York")
        sessions = sorted(set(local[underlying_bars["symbol"] == record["underlying"]].dt.date))
    current_session = fill_time.tz_convert("America/New_York").date()
    if current_session not in sessions:
        return None
    target_index = sessions.index(current_session) + holding
    if target_index >= len(sessions):
        return None
    clock = str(record.get("session_exit_clock", "14:00"))
    return pd.Timestamp(f"{sessions[target_index]} {clock}:00", tz="America/New_York").tz_convert("UTC")


def _policy_exit(
    record: dict[str, Any],
    *,
    underlying_bars: pd.DataFrame | Mapping[str, pd.DataFrame] | None,
    exit_minutes: int,
    fill_time: pd.Timestamp | None = None,
) -> tuple[pd.Timestamp, str]:
    """Apply the frozen underlying VWAP position policy without option prices.

    The policy is evaluated only on completed 15-minute bars after entry.  It
    returns the evaluation timestamp; option execution is handled separately
    at the next available common option bar.
    """
    decision = pd.Timestamp(record["decision_time"])
    confirmed_fill = fill_time or decision
    hard_exit = confirmed_fill + pd.Timedelta(minutes=exit_minutes)
    confirmed_fill_text = confirmed_fill.isoformat().replace("+00:00", "Z")
    if (
        record.get("policy_entry_time") == confirmed_fill_text
        and "policy_exit_time" in record
        and "policy_exit_reason" in record
    ):
        return pd.Timestamp(record["policy_exit_time"]), str(record["policy_exit_reason"])
    session_exit = _session_exit_time(record, underlying_bars=underlying_bars, fill_time=confirmed_fill)
    strategy_family = record.get("strategy_family", record["strategy"])
    if record.get("holding_sessions") is not None:
        if session_exit is None:
            return hard_exit, "SESSION_EXIT_UNAVAILABLE"
        hard_exit = session_exit
        if strategy_family != "trend_vwap_or_session_exit_v8":
            return hard_exit, "SESSION_TIME_EXIT"
    if underlying_bars is None:
        return hard_exit, "TIME_EXIT"
    session = decision.tz_convert("America/New_York").date()
    if isinstance(underlying_bars, Mapping):
        if strategy_family == "trend_vwap_or_session_exit_v8":
            completed = pd.concat(
                [
                    values
                    for (symbol, candidate_session), values in underlying_bars.items()
                    if symbol == record["underlying"] and candidate_session >= session
                ],
                ignore_index=True,
            )
        else:
            completed = underlying_bars.get((record["underlying"], session))
        if completed is None or completed.empty:
            return hard_exit, "TIME_EXIT"
    else:
        bars = underlying_bars[underlying_bars["symbol"] == record["underlying"]].copy()
        bars["local_time"] = pd.to_datetime(bars["event_time"], utc=True).dt.tz_convert("America/New_York")
        bars = bars[bars["local_time"].dt.date == session].sort_values("local_time")
        if bars.empty:
            return hard_exit, "TIME_EXIT"
        bars["bucket"] = bars["local_time"].dt.floor("15min") + pd.Timedelta(minutes=15)
        bars["pv"] = bars["vwap"] * bars["volume"]
        bars["cum_pv"] = bars["pv"].cumsum()
        bars["cum_volume"] = bars["volume"].cumsum()
        completed = bars.groupby("bucket", sort=True).tail(1).copy()
        completed["session_vwap"] = completed["cum_pv"] / completed["cum_volume"]
    policy_rows = completed[
        (completed["bucket"].dt.tz_convert("UTC") > confirmed_fill)
        & (completed["bucket"].dt.tz_convert("UTC") <= hard_exit)
    ]
    for row in policy_rows.itertuples(index=False):
        adverse = (
            (strategy_family in {"intraday_continuation_v1", "trend_vwap_or_session_exit_v8"} and record["side"] == "CALL" and row.close <= row.session_vwap)
            or (strategy_family in {"intraday_continuation_v1", "trend_vwap_or_session_exit_v8"} and record["side"] == "PUT" and row.close >= row.session_vwap)
        )
        reversion_touch = (
            (strategy_family == "vwap_reversion_v1" and record["side"] == "CALL" and row.close >= row.session_vwap)
            or (strategy_family == "vwap_reversion_v1" and record["side"] == "PUT" and row.close <= row.session_vwap)
        )
        if adverse:
            return row.bucket.tz_convert("UTC"), "ADVERSE_VWAP_CROSS"
        if reversion_touch:
            return row.bucket.tz_convert("UTC"), "VWAP_REVERSION_TOUCH"
    return hard_exit, "TIME_EXIT"


def _trade(record: dict[str, Any], *, root: Path, option_manifest: dict[str, Any], fee: float, severe: bool, structure: str = "debit", execution_model: str = "buffered", exit_minutes: int = 60, override_exit_minutes: int | None = None, underlying_bars: pd.DataFrame | Mapping[str, pd.DataFrame] | None = None) -> dict[str, Any] | None:
    if structure not in {"debit", "credit", "single_long", "long_straddle", "calendar"}:
        raise GroupAReplayError("REPLAY_STRUCTURE_INVALID")
    if execution_model not in {"buffered", "bar_open"}:
        raise GroupAReplayError("REPLAY_EXECUTION_MODEL_INVALID")
    effective_exit_minutes = int(
        override_exit_minutes if override_exit_minutes is not None else record.get("time_exit_minutes", exit_minutes)
    )
    if record.get("holding_sessions") is None and effective_exit_minutes not in _SUPPORTED_EXIT_MINUTES:
        raise GroupAReplayError("REPLAY_EXIT_MINUTES_INVALID")
    contracts = sorted(record["symbols"])
    call = record["side"] == "CALL"
    long_symbol, short_symbol = (
        (str(record["long_symbol"]), str(record["short_symbol"]))
        if structure == "calendar"
        else (contracts[0], contracts[1])
        if structure == "long_straddle" or call
        else (contracts[1], contracts[0])
    )
    long_bars = _bar(root, _dataset(option_manifest, f"option_bars_{record['request_id']}"))
    long_bars = long_bars[long_bars["symbol"] == long_symbol].set_index("event_time")
    all_bars = _bar(root, _dataset(option_manifest, f"option_bars_{record['request_id']}"))
    short_bars = all_bars[all_bars["symbol"] == short_symbol].set_index("event_time")
    common = long_bars.join(short_bars, lsuffix="_long", rsuffix="_short", how="inner").sort_index()
    decision = pd.Timestamp(record["decision_time"])
    entry = common[(common.index > decision) & (common.index <= decision + pd.Timedelta(minutes=5))]
    if entry.empty:
        return None
    opened = entry.iloc[0]
    policy_exit, exit_reason = _policy_exit(
        record,
        underlying_bars=underlying_bars,
        exit_minutes=effective_exit_minutes,
        fill_time=opened.name,
    )
    exit_ = common[(common.index > policy_exit) & (common.index <= policy_exit + pd.Timedelta(minutes=5))]
    if execution_model == "bar_open":
        buy_long, sell_short = float(opened["open_long"]), float(opened["open_short"])
    else:
        buy_long = (
            float(opened["high_long"] + 0.05)
            if severe
            else float(opened["open_long"] + _buffer(opened, "long"))
        )
        sell_short = (
            float(opened["high_short"] + 0.05)
            if severe and structure == "long_straddle"
            else float(opened["open_short"] + _buffer(opened, "short"))
            if structure == "long_straddle"
            else max(0.0, float(opened["low_short"] - 0.05))
            if severe
            else max(0.0, float(opened["open_short"] - _buffer(opened, "short")))
        )
    debit = buy_long + sell_short if structure == "long_straddle" else buy_long - sell_short
    if structure != "single_long" and debit <= 0:
        return None
    fees = (2 if structure == "single_long" else 4) * fee * (2 if severe else 1)
    exit_value = 0.0
    long_exit_value = 0.0
    exit_time: str | None = None
    if exit_.empty and (not severe or structure == "credit"):
        # The central proxy may only substitute zero exit value in the named
        # severe stress for a long debit.  A zero close debit is favorable to
        # a short credit, so a missing credit exit always remains a no-fill.
        return None
    if not exit_.empty:
        closed = exit_.iloc[0]
        if execution_model == "bar_open":
            sell_long, buy_short = float(closed["open_long"]), float(closed["open_short"])
        else:
            sell_long = (
                max(0.0, float(closed["low_long"] - 0.05))
                if severe
                else max(0.0, float(closed["open_long"] - _buffer(closed, "long")))
            )
            buy_short = (
                max(0.0, float(closed["low_short"] - 0.05))
                if severe and structure == "long_straddle"
                else max(0.0, float(closed["open_short"] - _buffer(closed, "short")))
                if structure == "long_straddle"
                else float(closed["high_short"] + 0.05)
                if severe
                else float(closed["open_short"] + _buffer(closed, "short"))
            )
        long_exit_value = sell_long
        exit_value = (
            sell_long
            if structure == "single_long"
            else sell_long + buy_short
            if structure == "long_straddle"
            else sell_long - buy_short
        )
        exit_time = closed.name.isoformat().replace("+00:00", "Z")
    if structure in {"debit", "calendar"}:
        pnl = (exit_value - debit) * 100 - fees
        entry_value, return_denominator = debit, debit * 100 + fees
    elif structure == "single_long":
        if buy_long <= 0:
            return None
        pnl = (long_exit_value - buy_long) * 100 - fees
        entry_value, return_denominator = buy_long, buy_long * 100 + fees
    elif structure == "long_straddle":
        if debit <= 0:
            return None
        pnl = (exit_value - debit) * 100 - fees
        entry_value, return_denominator = debit, debit * 100 + fees
    else:
        if execution_model == "bar_open":
            entry_credit = float(opened["open_long"] - opened["open_short"])
        else:
            entry_credit = max(0.0, float(opened["open_long"] - _buffer(opened, "long"))) - float(
                opened["open_short"] + _buffer(opened, "short")
            )
        if severe:
            entry_credit = max(0.0, float(opened["low_long"] - 0.05)) - float(
                opened["high_short"] + 0.05
            )
        if entry_credit <= 0:
            return None
        if exit_.empty:
            close_debit = 0.0
        elif execution_model == "bar_open":
            close_debit = float(closed["open_long"] - closed["open_short"])
        else:
            close_debit = max(0.0, float(closed["open_long"] + _buffer(closed, "long"))) - max(
                0.0, float(closed["open_short"] - _buffer(closed, "short"))
            )
        if severe and not exit_.empty:
            close_debit = float(closed["high_long"] + 0.05) - max(
                0.0, float(closed["low_short"] - 0.05)
            )
        pnl = (entry_credit - close_debit) * 100 - fees
        entry_value, return_denominator = entry_credit, entry_credit * 100 + fees
    entry_time = opened.name
    realized_exit = closed.name if not exit_.empty else policy_exit
    return {"strategy": record["strategy"], "underlying": record["underlying"], "decision_time": record["decision_time"], "entry_time": entry_time.isoformat().replace("+00:00", "Z"), "exit_policy_time": policy_exit.isoformat().replace("+00:00", "Z"), "exit_reason": exit_reason, "exit_time": exit_time, "long_symbol": long_symbol, "short_symbol": short_symbol, "debit": entry_value, "exit_value": exit_value, "fees": fees, "pnl": pnl, "return_on_max_loss": pnl / return_denominator, "exposure_minutes": (realized_exit - entry_time).total_seconds() / 60, "missing_exit": exit_.empty, "structure": structure}


def run(*, option_manifest_path: Path, request_path: Path, output: Path, structure: str = "debit", execution_model: str = "buffered", exit_minutes: int = 60, force_exit_minutes: int | None = None, base_data_manifest_path: Path | None = None) -> Path:
    option_manifest = _load(option_manifest_path)
    requests = _load(request_path)
    if option_manifest.get("schema_version") != "option-observation-manifest/v1" or option_manifest.get("status") != "COLLECTED":
        raise GroupAReplayError("OPTION_OBSERVATION_NOT_READY")
    if requests.get("base_data_manifest_hash") != option_manifest.get("base_data_manifest_hash"):
        raise GroupAReplayError("REPLAY_BASE_MANIFEST_BINDING_MISMATCH")
    base_manifest_path = base_data_manifest_path or (
        option_manifest_path.resolve().parents[2] / "underlying" / "data_manifest.json"
    )
    base_manifest = _load(base_manifest_path)
    if base_manifest.get("schema_version") != "research-data-manifest/v1":
        raise GroupAReplayError("REPLAY_BASE_MANIFEST_INVALID")
    if base_manifest.get("manifest_hash") != option_manifest.get("base_data_manifest_hash"):
        raise GroupAReplayError("REPLAY_BASE_MANIFEST_BINDING_MISMATCH")
    request_rows = requests.get("requests", [])
    selection_rows = requests.get("selection_records", [])
    if len(request_rows) != len(selection_rows):
        raise GroupAReplayError("REPLAY_REQUEST_SELECTION_COUNT_MISMATCH")
    if force_exit_minutes is not None and force_exit_minutes not in _SUPPORTED_EXIT_MINUTES:
        raise GroupAReplayError("REPLAY_EXIT_MINUTES_INVALID")
    records = [{**selection, **request} for selection, request in zip(selection_rows, request_rows, strict=True)]
    root = option_manifest_path.resolve().parent
    underlying_bars = _bar(
        base_manifest_path.resolve().parent,
        _dataset(base_manifest, "stock_bars_split"),
    )
    underlying_intervals = _completed_underlying_intervals(underlying_bars)
    target = ensure_empty_output(output)
    selected_contracts: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    for record in records:
        record_exit_minutes = int(
            force_exit_minutes if force_exit_minutes is not None else record.get("time_exit_minutes", exit_minutes)
        )
        if record.get("holding_sessions") is None and record_exit_minutes not in _SUPPORTED_EXIT_MINUTES:
            raise GroupAReplayError("REPLAY_EXIT_MINUTES_INVALID")
        first, second = sorted(record["symbols"])
        long_symbol, short_symbol = (
            (str(record["long_symbol"]), str(record["short_symbol"]))
            if structure == "calendar"
            else (first, second)
            if structure == "long_straddle" or record["side"] == "CALL"
            else (second, first)
        )
        selected_contracts.append(
            {
                "request_id": record["request_id"], "strategy": record["strategy"],
                "underlying": record["underlying"], "decision_time": record["decision_time"],
                "side": record["side"], "long_symbol": long_symbol, "short_symbol": short_symbol,
            }
        )
        bars = _bar(root, _dataset(option_manifest, f"option_bars_{record['request_id']}"))
        common_times = set(bars[bars["symbol"] == long_symbol]["event_time"]) & set(
            bars[bars["symbol"] == short_symbol]["event_time"]
        )
        decision = pd.Timestamp(record["decision_time"])
        entry_times = sorted(
            value for value in common_times if decision < value <= decision + pd.Timedelta(minutes=5)
        )
        if entry_times:
            entry_time = entry_times[0]
            policy_exit, exit_reason = _policy_exit(
                record,
                underlying_bars=underlying_intervals,
                exit_minutes=record_exit_minutes,
                fill_time=entry_time,
            )
            record["policy_entry_time"] = entry_time.isoformat().replace("+00:00", "Z")
            record["policy_exit_time"] = policy_exit.isoformat().replace("+00:00", "Z")
            record["policy_exit_reason"] = exit_reason
        else:
            policy_exit = decision + pd.Timedelta(minutes=record_exit_minutes)
            exit_reason = "ENTRY_UNAVAILABLE"
        coverage.append(
            {
                "request_id": record["request_id"], "strategy": record["strategy"],
                "entry_common_minutes": len(entry_times),
                "exit_common_minutes": sum(
                    policy_exit < value <= policy_exit + pd.Timedelta(minutes=5)
                    for value in common_times
                ),
                "exit_policy_time": policy_exit.isoformat().replace("+00:00", "Z"),
                "exit_reason": exit_reason,
            }
        )
    all_trades: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {}
    stress: dict[str, dict[str, Any]] = {}
    curves: dict[str, list[dict[str, Any]]] = {}
    daily_artifacts: dict[str, dict[str, Any]] = {}
    strategies = sorted({item["strategy"] for item in records})
    for strategy in strategies:
        trades = [trade for record in records if record["strategy"] == strategy for trade in [_trade(record, root=root, option_manifest=option_manifest, fee=0.10, severe=False, structure=structure, execution_model=execution_model, exit_minutes=exit_minutes, override_exit_minutes=force_exit_minutes, underlying_bars=underlying_intervals)] if trade]
        all_trades.extend(trades)
        daily = pd.DataFrame(trades)
        if daily.empty:
            metrics[strategy] = {
                "requested_signals": sum(item["strategy"] == strategy for item in records),
                "trades": 0,
                "status": "NO_PROXY_FILLS",
            }
            continue
        daily["date"] = pd.to_datetime(daily["decision_time"], utc=True).dt.date.astype(str)
        start = min(
            pd.Timestamp(record["decision_time"]).tz_convert("America/New_York").date().isoformat()
            for record in records
            if record["strategy"] == strategy
        )
        end = max(
            pd.Timestamp(record["decision_time"]).tz_convert("America/New_York").date().isoformat()
            for record in records
            if record["strategy"] == strategy
        )
        returns = daily.groupby("date", as_index=False)["pnl"].sum().rename(columns={"pnl": "daily_pnl"})
        returns = (
            returns.set_index("date")
            .reindex(_complete_market_dates(underlying_bars, start=start, end=end), fill_value=0.0)
            .rename_axis("date")
            .reset_index()
        )
        returns["daily_return"] = returns["daily_pnl"] / 100000.0
        curves[strategy] = returns[["date", "daily_pnl"]].to_dict("records")
        equity = (1 + returns["daily_return"]).cumprod()
        drawdown = equity / equity.cummax() - 1
        std = float(returns["daily_return"].std(ddof=1))
        downside = float((returns["daily_return"].clip(upper=0).pow(2).mean()) ** 0.5)
        metrics[strategy] = {"requested_signals": sum(item["strategy"] == strategy for item in records), "trades": len(trades), "unfilled_requests": sum(item["strategy"] == strategy for item in records) - len(trades), "net_pnl": float(daily["pnl"].sum()), "net_return": float(equity.iloc[-1] - 1), "sharpe": None if not std else math.sqrt(252) * float(returns["daily_return"].mean()) / std, "sortino": None if not downside else math.sqrt(252) * float(returns["daily_return"].mean()) / downside, "max_drawdown": float(drawdown.min()), "turnover_contract_legs": 4 * len(trades), "exposure_minutes": float(daily["exposure_minutes"].sum()), "missing_exit_trades": int(daily["missing_exit"].sum())}
        daily_artifacts[strategy] = write_parquet(
            target,
            f"daily_returns_{strategy}",
            returns.to_dict("records"),
            ("date", "daily_pnl", "daily_return"),
        )
    for name, fee, severe in (("zero_fees", 0.0, False), ("base_fees", 0.10, False), ("high_fees", 0.25, False), ("severe", 0.10, True)):
        stress[name] = {}
        for strategy in strategies:
            trades = [
                trade
                for record in records
                if record["strategy"] == strategy
                for trade in [_trade(record, root=root, option_manifest=option_manifest, fee=fee, severe=severe, structure=structure, execution_model=execution_model, exit_minutes=exit_minutes, override_exit_minutes=force_exit_minutes, underlying_bars=underlying_intervals)]
                if trade
            ]
            stress[name][strategy] = {
                "requested_signals": sum(item["strategy"] == strategy for item in records),
                "trades": len(trades),
                "unfilled_requests": sum(item["strategy"] == strategy for item in records) - len(trades),
                "net_pnl": float(sum(item["pnl"] for item in trades)),
                "missing_exit_trades": sum(bool(item["missing_exit"]) for item in trades),
            }
    write_parquet(target, "trades", all_trades, ("strategy", "underlying", "decision_time", "entry_time", "exit_policy_time", "exit_reason", "exit_time", "long_symbol", "short_symbol", "debit", "exit_value", "fees", "pnl", "return_on_max_loss", "exposure_minutes", "missing_exit", "structure"))
    write_parquet(target, "selected_contracts", selected_contracts, ("request_id", "strategy", "underlying", "decision_time", "side", "long_symbol", "short_symbol"))
    write_parquet(target, "proxy_leg_observations", coverage, ("request_id", "strategy", "entry_common_minutes", "exit_common_minutes", "exit_policy_time", "exit_reason"))
    plot_relative = Path("plots") / "cumulative_pnl.svg"
    atomic_bytes(target / plot_relative, _cumulative_pnl_svg(curves))
    plot_spec_relative = Path("plots") / "cumulative_pnl_spec.json"
    atomic_json(
        target / plot_spec_relative,
        {
            "schema_version": "cumulative-pnl-plot/v1",
            "status": "NO_FILLED_TRADES" if not curves else "RESEARCH_ONLY_NON_EXECUTABLE",
            "plot": plot_relative.as_posix(),
            "series": [
                {"strategy": strategy, "daily_returns_artifact": daily_artifacts[strategy]}
                for strategy in sorted(daily_artifacts)
            ],
        },
    )
    report = {"schema_version": "group-a-option-proxy-replay/v1", "status": "RESEARCH_ONLY_NON_EXECUTABLE", "structure": structure, "execution_model": execution_model, "exit_minutes": exit_minutes, "force_exit_minutes": force_exit_minutes, "option_observation_manifest_hash": option_manifest["manifest_hash"], "option_request_manifest_hash": requests["manifest_hash"], "cumulative_pnl_plot": plot_relative.as_posix(), "cumulative_pnl_plot_spec": plot_spec_relative.as_posix(), "metrics": metrics, "limitations": ["historical bars are non-executable option proxies", "underlying VWAP exit policy is a deterministic replay proxy, not broker execution", "bar_open execution ignores bid-ask friction when selected", "no broker execution, sizing, or live option selection", "credit and O1 single-long counterparts are exploratory in-sample only"], "report_hash": None}
    report["report_hash"] = canonical_hash({key: value for key, value in report.items() if key != "report_hash"})
    atomic_json(target / "metrics.json", report)
    atomic_json(target / "cost_stress.json", {"schema_version": "group-a-cost-stress/v1", "status": "RESEARCH_ONLY_NON_EXECUTABLE", "scenarios": stress})
    return target / "metrics.json"
