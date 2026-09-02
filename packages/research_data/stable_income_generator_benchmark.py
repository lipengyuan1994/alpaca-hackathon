"""Build the public V13.5, SPY, and QQQ evidence comparison.

This module is research-only. It verifies the frozen V13.5 daily-return artifact
and the frozen Alpaca IEX split-adjusted bar artifact before creating a
price-only buy-and-hold comparison. It has no credential or broker dependency.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import date
from html import escape
from pathlib import Path
from typing import Any

import pandas as pd

from packages.contracts.canonical import canonical_hash

from .artifacts import atomic_bytes, atomic_json, file_hash
from .group_a_proxy_backtest import _dataset, _load

_INITIAL_BALANCE = 100_000.0
_STRATEGY = "qqq_wheel_v13_5"
_BENCHMARKS = ("SPY", "QQQ")
_EASTERN = "America/New_York"


class StableIncomeEvidenceError(ValueError):
    """Raised when an input cannot support the public evidence comparison."""


def _verified_strategy(path: Path, run_manifest_path: Path) -> pd.DataFrame:
    manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    expected_manifest_hash = canonical_hash(
        {key: value for key, value in manifest.items() if key != "run_manifest_hash"}
    )
    if manifest.get("run_manifest_hash") != expected_manifest_hash:
        raise StableIncomeEvidenceError("SITE_STRATEGY_RUN_MANIFEST_HASH_MISMATCH")
    expected_artifact_hash = manifest.get("artifacts", {}).get(
        "normalized/daily_returns.parquet"
    )
    if not path.is_file() or file_hash(path) != expected_artifact_hash:
        raise StableIncomeEvidenceError("SITE_STRATEGY_DAILY_RETURNS_HASH_MISMATCH")
    frame = pd.read_parquet(path)
    frame = frame[frame["strategy"] == _STRATEGY].copy()
    if frame.empty or frame["date"].duplicated().any():
        raise StableIncomeEvidenceError("SITE_STRATEGY_SERIES_INVALID")
    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    frame["equity"] = pd.to_numeric(frame["equity"], errors="raise")
    return frame.sort_values("date", kind="stable").reset_index(drop=True)


def _verified_daily_closes(
    data_manifest_path: Path, *, strategy_start: date, strategy_end: date
) -> tuple[pd.DataFrame, dict[str, Any]]:
    manifest = _load(data_manifest_path)
    dataset = _dataset(manifest, "stock_bars_split")
    artifact = dataset.get("artifact", {})
    path = data_manifest_path.resolve().parent / str(artifact.get("path", ""))
    if not path.is_file() or file_hash(path) != artifact.get("sha256"):
        raise StableIncomeEvidenceError("SITE_BENCHMARK_BAR_HASH_MISMATCH")
    bars = pd.read_parquet(
        path,
        columns=["symbol", "event_time", "close"],
        filters=[("symbol", "in", list(_BENCHMARKS))],
    )
    bars["timestamp"] = pd.to_datetime(bars["event_time"], utc=True).dt.tz_convert(_EASTERN)
    bars["close"] = pd.to_numeric(bars["close"], errors="coerce")
    bars = bars.dropna(subset=["close"])
    bars = bars[
        (bars["timestamp"].dt.weekday < 5)
        & (bars["timestamp"].dt.time >= pd.Timestamp("09:30").time())
        & (bars["timestamp"].dt.time < pd.Timestamp("16:00").time())
    ].copy()
    bars["date"] = bars["timestamp"].dt.date
    closes = (
        bars.sort_values(["symbol", "timestamp"], kind="stable")
        .groupby(["symbol", "date"], as_index=False)
        .tail(1)[["symbol", "date", "close"]]
        .pivot(index="date", columns="symbol", values="close")
        .sort_index()
    )
    eligible = closes.index[closes.index < strategy_start]
    if eligible.empty:
        raise StableIncomeEvidenceError("SITE_BENCHMARK_BASELINE_SESSION_MISSING")
    baseline = eligible[-1]
    aligned = closes.loc[(closes.index >= baseline) & (closes.index <= strategy_end)].copy()
    if not set(_BENCHMARKS).issubset(aligned.columns) or aligned.loc[baseline].isna().any():
        raise StableIncomeEvidenceError("SITE_BENCHMARK_SERIES_INVALID")
    source = {
        "base_data_manifest_hash": manifest["manifest_hash"],
        "dataset_id": dataset["dataset_id"],
        "dataset_hash": artifact["sha256"],
        "feed": dataset.get("feed"),
        "adjustment": dataset.get("adjustment"),
    }
    return aligned, source


def _metrics(values: pd.Series, dates: list[date]) -> dict[str, float | int]:
    returns = values.pct_change().dropna()
    drawdown = values / values.cummax() - 1.0
    standard_deviation = float(returns.std(ddof=1))
    downside = float((returns.clip(upper=0).pow(2).mean()) ** 0.5)
    years = max((dates[-1] - dates[0]).days / 365.2425, 1 / 365.2425)
    return {
        "starting_value": round(float(values.iloc[0]), 2),
        "ending_value": round(float(values.iloc[-1]), 2),
        "total_return": float(values.iloc[-1] / values.iloc[0] - 1.0),
        "cagr": float((values.iloc[-1] / values.iloc[0]) ** (1.0 / years) - 1.0),
        "sharpe": 0.0
        if not standard_deviation
        else math.sqrt(252) * float(returns.mean()) / standard_deviation,
        "sortino": 0.0
        if not downside
        else math.sqrt(252) * float(returns.mean()) / downside,
        "max_drawdown": float(drawdown.min()),
        "sessions": len(returns),
    }


def _regime_labels(closes: pd.DataFrame, dates: list[date]) -> list[str | None]:
    qqq = closes["QQQ"].copy()
    prior_close = qqq.shift(1)
    prior_sma_50 = qqq.shift(1).rolling(50, min_periods=50).mean()
    labels = pd.Series(
        [
            None
            if pd.isna(prior_sma_50.loc[item])
            else "UPTREND"
            if prior_close.loc[item] > prior_sma_50.loc[item]
            else "DOWNTREND"
            for item in dates
        ],
        index=dates,
    )
    return labels.tolist()


def _regime_summary(
    dates: list[date], labels: list[str | None], series: dict[str, pd.Series]
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    indexed_labels = pd.Series(labels, index=dates)
    for regime in ("UPTREND", "DOWNTREND"):
        selected = (indexed_labels == regime).to_numpy()
        metrics: dict[str, Any] = {"sessions": int(selected.sum())}
        for name, values in series.items():
            daily = values.pct_change().fillna(0.0)
            metrics[name] = {
                "conditional_compounded_return": float((1.0 + daily[selected]).prod() - 1.0),
                "booked_or_price_change": round(float(values.diff().fillna(0.0)[selected].sum()), 2),
            }
        result[regime.lower()] = metrics
    return result


def _drawdown_episodes(
    dates: list[date], series: dict[str, pd.Series], *, threshold: float = -0.05
) -> list[dict[str, Any]]:
    qqq = series["qqq"]
    episodes: list[tuple[int, int, float]] = []
    peak_index = 0
    trough_index = 0
    trough_drawdown = 0.0
    for index in range(1, len(qqq)):
        if qqq.iloc[index] >= qqq.iloc[peak_index]:
            if trough_drawdown <= threshold:
                episodes.append((peak_index, trough_index, trough_drawdown))
            peak_index = index
            trough_index = index
            trough_drawdown = 0.0
            continue
        drawdown = float(qqq.iloc[index] / qqq.iloc[peak_index] - 1.0)
        if drawdown < trough_drawdown:
            trough_drawdown = drawdown
            trough_index = index
    if trough_drawdown <= threshold:
        episodes.append((peak_index, trough_index, trough_drawdown))
    episodes.sort(key=lambda item: item[2])
    result: list[dict[str, Any]] = []
    for peak, trough, qqq_drawdown in episodes[:4]:
        result.append(
            {
                "peak_date": dates[peak].isoformat(),
                "trough_date": dates[trough].isoformat(),
                "qqq_return": qqq_drawdown,
                "spy_return": float(series["spy"].iloc[trough] / series["spy"].iloc[peak] - 1.0),
                "v13_5_return": float(
                    series["v13_5"].iloc[trough] / series["v13_5"].iloc[peak] - 1.0
                ),
            }
        )
    return result


def _json_safe(value: Any) -> Any:
    """Replace an unavailable numeric statistic with JSON null before hashing."""
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _chart_svg(
    dates: list[date], labels: list[str | None], series: dict[str, pd.Series]
) -> bytes:
    width, height = 1280, 660
    left, right, top, bottom = 92, 36, 104, 76
    plot_width, plot_height = width - left - right, height - top - bottom
    all_values = [float(value) for values in series.values() for value in values]
    minimum = math.floor(min(all_values) / 10_000) * 10_000
    maximum = math.ceil(max(all_values) / 10_000) * 10_000
    if minimum == maximum:
        maximum += 10_000

    def x(index: int) -> float:
        return left + index * plot_width / max(len(dates) - 1, 1)

    def y(value: float) -> float:
        return top + (maximum - value) * plot_height / (maximum - minimum)

    colors = {"v13_5": "#67e6a2", "spy": "#dce8df", "qqq": "#e8bd64"}
    names = {"v13_5": "V13.5 booked equity", "spy": "SPY price-only", "qqq": "QQQ price-only"}
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        '<title id="title">Growth of 100,000 dollars: V13.5 versus SPY and QQQ</title>',
        '<desc id="desc">A line chart comparing the V13.5 booked-equity research proxy with price-only SPY and QQQ buy-and-hold benchmarks from January 2024 through August 2026.</desc>',
        '<rect width="1280" height="660" rx="24" fill="#0b1a16"/>',
        '<text x="92" y="48" fill="#f1f6eb" font-family="Georgia,serif" font-size="28">Growth of $100,000</text>',
        f'<text x="92" y="77" fill="#9fb0a6" font-family="sans-serif" font-size="14">{dates[1].isoformat()} – {dates[-1].isoformat()} · frozen Alpaca IEX comparison</text>',
    ]
    start = None
    for index, label in enumerate(labels):
        if label == "DOWNTREND" and start is None:
            start = index
        if start is not None and (label != "DOWNTREND" or index == len(labels) - 1):
            end = index if label == "DOWNTREND" and index == len(labels) - 1 else index - 1
            parts.append(
                f'<rect x="{x(start):.2f}" y="{top}" width="{max(x(end) - x(start), 1):.2f}" height="{plot_height}" fill="#e8bd64" opacity="0.055"/>'
            )
            start = None
    steps = 5
    for step in range(steps + 1):
        value = minimum + step * (maximum - minimum) / steps
        y_value = y(value)
        parts.append(
            f'<line x1="{left}" y1="{y_value:.2f}" x2="{width-right}" y2="{y_value:.2f}" stroke="#c9ddd1" stroke-opacity="0.12"/>'
        )
        parts.append(
            f'<text x="{left-14}" y="{y_value+5:.2f}" text-anchor="end" fill="#9fb0a6" font-family="sans-serif" font-size="12">${value/1000:.0f}K</text>'
        )
    tick_indexes = [0, len(dates) // 3, 2 * len(dates) // 3, len(dates) - 1]
    for index in tick_indexes:
        parts.append(
            f'<text x="{x(index):.2f}" y="{height-36}" text-anchor="middle" fill="#9fb0a6" font-family="sans-serif" font-size="12">{dates[index].strftime("%b %Y")}</text>'
        )
    for name, values in series.items():
        points = " ".join(
            f"{x(index):.2f},{y(float(value)):.2f}" for index, value in enumerate(values)
        )
        parts.append(
            f'<polyline points="{points}" fill="none" stroke="{colors[name]}" stroke-width="{4 if name == "v13_5" else 2.5}" stroke-linejoin="round" stroke-linecap="round" opacity="{1 if name == "v13_5" else .88}"/>'
        )
    legend_x = 620
    for position, name in enumerate(("v13_5", "spy", "qqq")):
        item_x = legend_x + position * 202
        parts.append(
            f'<line x1="{item_x}" y1="50" x2="{item_x+28}" y2="50" stroke="{colors[name]}" stroke-width="4" stroke-linecap="round"/>'
        )
        parts.append(
            f'<text x="{item_x+38}" y="55" fill="#dce8df" font-family="sans-serif" font-size="13">{escape(names[name])}</text>'
        )
    parts.append(
        '<text x="92" y="635" fill="#7f9388" font-family="sans-serif" font-size="11">Amber bands: QQQ prior-close below its prior 50-session moving average. Benchmarks exclude dividends and fees.</text>'
    )
    parts.append("</svg>")
    return "".join(parts).encode("utf-8")


def build(
    *,
    strategy_path: Path,
    strategy_run_manifest_path: Path,
    data_manifest_path: Path,
    output_json: Path,
    output_svg: Path,
) -> dict[str, Any]:
    strategy = _verified_strategy(strategy_path, strategy_run_manifest_path)
    strategy_dates = strategy["date"].tolist()
    closes, benchmark_source = _verified_daily_closes(
        data_manifest_path,
        strategy_start=strategy_dates[0],
        strategy_end=strategy_dates[-1],
    )
    baseline_date = closes.index[closes.index < strategy_dates[0]][-1]
    dates = [baseline_date, *strategy_dates]
    aligned_closes = closes.reindex(dates)
    if aligned_closes.isna().any().any():
        raise StableIncomeEvidenceError("SITE_BENCHMARK_SESSION_ALIGNMENT_MISMATCH")
    series = {
        "v13_5": pd.Series([_INITIAL_BALANCE, *strategy["equity"].tolist()]),
        "spy": _INITIAL_BALANCE * aligned_closes["SPY"].reset_index(drop=True) / aligned_closes["SPY"].iloc[0],
        "qqq": _INITIAL_BALANCE * aligned_closes["QQQ"].reset_index(drop=True) / aligned_closes["QQQ"].iloc[0],
    }
    labels = [None, *_regime_labels(closes, strategy_dates)]
    regime = _regime_summary(dates, labels, series)
    payload: dict[str, Any] = {
        "schema_version": "stable-income-generator-benchmark/v1",
        "status": "RESEARCH_ONLY_EXPLORATORY_IN_SAMPLE",
        "period": {
            "baseline_date": baseline_date.isoformat(),
            "start_date": strategy_dates[0].isoformat(),
            "end_date": strategy_dates[-1].isoformat(),
            "strategy_sessions": len(strategy_dates),
        },
        "method": {
            "v13_5": "Frozen booked-equity proxy; open positions are not continuously marked to market.",
            "benchmarks": "Buy and hold from the prior-session close, using split-adjusted Alpaca IEX session closes; price-only, no dividends or fees.",
            "regime": "Exact V13.5 signal: prior QQQ close above its prior 50-session simple moving average is UPTREND; otherwise DOWNTREND.",
        },
        "metrics": {name: _metrics(values, dates) for name, values in series.items()},
        "regime_summary": regime,
        "drawdown_episodes": _drawdown_episodes(dates, series),
        "series": {
            "dates": [item.isoformat() for item in dates],
            "regime": labels,
            **{
                name: [round(float(value), 2) for value in values]
                for name, values in series.items()
            },
        },
        "sources": {
            "strategy_run_manifest_hash": json.loads(
                strategy_run_manifest_path.read_text(encoding="utf-8")
            )["run_manifest_hash"],
            "strategy_daily_returns_hash": file_hash(strategy_path),
            **benchmark_source,
        },
        "limitations": [
            "The V13.5 comparison is exploratory and in-sample, not an out-of-sample promotion result.",
            "Historical option bars are non-executable proxies and assignment is deterministic expiration accounting.",
            "V13.5 equity changes when the replay books lifecycle events; it is not continuous daily mark-to-market equity.",
            "SPY and QQQ benchmarks are split-adjusted IEX price returns and exclude dividends, fees, slippage, and taxes.",
            "The sample includes both V13.5 regime labels, but booked-equity performance was negative across downtrend-labeled sessions; individual drawdown episodes still show less severe declines than QQQ.",
            "Observed drawdown resilience is not proof of future bull/bear survival.",
        ],
        "artifact_hash": None,
    }
    payload = _json_safe(payload)
    payload["artifact_hash"] = canonical_hash(
        {key: value for key, value in payload.items() if key != "artifact_hash"}
    )
    atomic_json(output_json, payload)
    atomic_bytes(output_svg, _chart_svg(dates, labels, series))
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--strategy-daily-returns", required=True, type=Path)
    parser.add_argument("--strategy-run-manifest", required=True, type=Path)
    parser.add_argument("--data-manifest", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--output-svg", required=True, type=Path)
    args = parser.parse_args()
    result = build(
        strategy_path=args.strategy_daily_returns,
        strategy_run_manifest_path=args.strategy_run_manifest,
        data_manifest_path=args.data_manifest,
        output_json=args.output_json,
        output_svg=args.output_svg,
    )
    print(json.dumps({"status": result["status"], "artifact_hash": result["artifact_hash"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
