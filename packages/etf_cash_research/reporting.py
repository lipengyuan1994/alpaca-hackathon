"""Artifact writers and deterministic candidate selection."""

from __future__ import annotations

import html as html_lib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from packages.contracts.canonical import canonical_hash
from packages.research_data.artifacts import atomic_bytes, atomic_json, file_hash

from .collector import load_manifest
from .metrics import add_benchmark_comparison, moving_block_bootstrap
from .protocol import DEFAULT_PROTOCOL, ResearchProtocol, candidate_registry
from .simulator import BacktestResult, run_backtest, run_benchmark, run_static_pair_benchmark


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("ETF_REPORT_JSON_INVALID") from exc
    if not isinstance(value, dict):
        raise ValueError("ETF_REPORT_JSON_INVALID")
    return value


def _svg_line(path: Path, title: str, series: list[tuple[str, list[float]]], *, y_label: str = "", dates: list[str] | None = None) -> None:
    width, height, pad = 920, 330, 48
    values = [item for _, points in series for item in points if pd.notna(item)]
    low, high = (min(values), max(values)) if values else (0.0, 1.0)
    if high <= low:
        high = low + 1.0
    colors = ("#2563eb", "#dc2626", "#059669", "#7c3aed", "#ea580c", "#0891b2")
    elements = [f"<rect width='{width}' height='{height}' fill='white'/><text x='{pad}' y='24' font-size='16' font-family='system-ui'>{html_lib.escape(title)}</text>"]
    for index, (label, points) in enumerate(series):
        if not points:
            continue
        coords = []
        denominator = max(1, len(points) - 1)
        for x_index, value in enumerate(points):
            x = pad + (width - 2 * pad) * x_index / denominator
            y = height - pad - (height - 2 * pad) * (float(value) - low) / (high - low)
            coords.append(f"{x:.2f},{y:.2f}")
        color = colors[index % len(colors)]
        elements.append(f"<polyline fill='none' stroke='{color}' stroke-width='2' points='{' '.join(coords)}'/>")
        elements.append(f"<text x='{pad + index * 145}' y='{height - 12}' fill='{color}' font-size='12' font-family='system-ui'>{html_lib.escape(label[:22])}</text>")
    elements.append(f"<text x='8' y='{pad}' font-size='11' font-family='system-ui'>{html_lib.escape(y_label)}</text>")
    for fraction in (0.0, 0.5, 1.0):
        value = low + fraction * (high - low)
        label = f"{value:.0%}" if "drawdown" in y_label else f"{value:,.0f}"
        y = height - pad - (height - 2 * pad) * fraction
        elements.append(f"<text x='2' y='{y:.1f}' font-size='10'>{label}</text>")
    if dates:
        for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
            index = round(fraction * (len(dates) - 1))
            x = pad + (width - 2 * pad) * fraction
            elements.append(f"<text x='{x:.1f}' y='{height - pad + 17}' text-anchor='middle' font-size='10'>{html_lib.escape(str(dates[index])[:10])}</text>")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_bytes(path, ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 %d %d'>%s</svg>" % (width, height, "".join(elements))).encode("utf-8"))


def _svg_bars(path: Path, title: str, labels: list[str], values: list[float], *, y_label: str = "") -> None:
    width, height, pad = 920, 330, 52
    low, high = min([0.0, *values]), max([0.0, *values])
    span = high - low or 1.0
    baseline = height - pad - (height - 2 * pad) * (0.0 - low) / span
    bar_width = max(8.0, (width - 2 * pad) / max(1, len(values)) * 0.68)
    elements = [f"<rect width='{width}' height='{height}' fill='white'/><text x='{pad}' y='24' font-size='16' font-family='system-ui'>{html_lib.escape(title)}</text>"]
    for index, value in enumerate(values):
        x = pad + (width - 2 * pad) * (index + 0.5) / max(1, len(values)) - bar_width / 2
        y = baseline - (height - 2 * pad) * value / span
        top = min(y, baseline)
        bar_height = abs(baseline - y)
        color = "#2563eb" if value >= 0 else "#dc2626"
        elements.append(f"<rect x='{x:.2f}' y='{top:.2f}' width='{bar_width:.2f}' height='{bar_height:.2f}' fill='{color}'/>")
        elements.append(f"<text x='{x:.2f}' y='{height - 20}' font-size='10' transform='rotate(-35 {x:.2f},{height - 20})' font-family='system-ui'>{html_lib.escape(labels[index][:16])}</text>")
        value_label = f"{value:.1%}" if y_label == "return" else f"{value:.3f}"
        elements.append(f"<text x='{x:.2f}' y='{max(38, top - 4):.2f}' font-size='10' font-family='system-ui'>{value_label}</text>")
    elements.append(f"<text x='8' y='{pad}' font-size='11' font-family='system-ui'>{html_lib.escape(y_label)}</text>")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_bytes(path, ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 %d %d'>%s</svg>" % (width, height, "".join(elements))).encode("utf-8"))


def _svg_scatter(path: Path, title: str, rows: pd.DataFrame) -> None:
    width, height, pad = 920, 330, 52
    x = pd.to_numeric(rows.get("max_drawdown", pd.Series(dtype=float)), errors="coerce")
    y = pd.to_numeric(rows.get("net_return", pd.Series(dtype=float)), errors="coerce")
    valid = pd.DataFrame({"x": x, "y": y, "label": rows.get("candidate_id", pd.Series(dtype=str))}).dropna()
    x_low, x_high = (float(valid.x.min()), float(valid.x.max())) if not valid.empty else (0.0, 1.0)
    y_low, y_high = (float(valid.y.min()), float(valid.y.max())) if not valid.empty else (0.0, 1.0)
    if x_high <= x_low:
        x_high = x_low + 1.0
    if y_high <= y_low:
        y_high = y_low + 1.0
    elements = [f"<rect width='{width}' height='{height}' fill='white'/><text x='{pad}' y='24' font-size='16' font-family='system-ui'>{html_lib.escape(title)}</text>"]
    for _, row in valid.iterrows():
        px = pad + (width - 2 * pad) * (float(row.x) - x_low) / (x_high - x_low)
        py = height - pad - (height - 2 * pad) * (float(row.y) - y_low) / (y_high - y_low)
        elements.append(f"<circle cx='{px:.2f}' cy='{py:.2f}' r='4' fill='#2563eb'><title>{html_lib.escape(str(row.label))}</title></circle>")
    elements.append(f"<text x='{width - 120}' y='{height - 12}' font-size='11' font-family='system-ui'>drawdown</text><text x='8' y='{pad}' font-size='11' font-family='system-ui'>return</text>")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_bytes(path, ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 %d %d'>%s</svg>" % (width, height, "".join(elements))).encode("utf-8"))


def _markdown_table(frame: pd.DataFrame) -> str:
    headers = [str(column) for column in frame.columns]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in frame.itertuples(index=False, name=None):
        cells = []
        for value in row:
            if isinstance(value, float):
                cells.append(f"{value:.6f}")
            else:
                cells.append(str(value).replace("|", "\\|"))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def load_bars_from_manifest(manifest_path: Path, *, adjustment: str = "split") -> pd.DataFrame:
    try:
        manifest = load_manifest(manifest_path)
    except ValueError as exc:
        raise ValueError("ETF_DATA_MANIFEST_HASH_MISMATCH") from exc
    dataset_id = "stock_bars_split" if adjustment == "split" else "stock_bars_raw"
    dataset = next((item for item in manifest.get("datasets", []) if item.get("dataset_id") == dataset_id), None)
    if not isinstance(dataset, dict):
        raise ValueError("ETF_DATASET_MISSING")
    artifact = dataset.get("artifact", {})
    path = (manifest_path.parent / str(artifact.get("path", ""))).resolve()
    if not path.is_file() or file_hash(path) != artifact.get("sha256"):
        raise ValueError("ETF_DATASET_HASH_MISMATCH")
    bars = pd.read_parquet(path)
    if "date" not in bars.columns and "event_time" in bars.columns:
        bars = bars.rename(columns={"event_time": "date"})
    # Split-adjusted prices already incorporate share-count changes.  Attach
    # cash dividends for the simulator's receivable ledger, while retaining
    # corporate-action rows as an auditable input rather than applying a split
    # a second time to adjusted prices.
    actions_dataset = next((item for item in manifest.get("datasets", []) if item.get("dataset_id") == "corporate_actions"), None)
    if isinstance(actions_dataset, dict):
        action_artifact = actions_dataset.get("artifact", {})
        action_path = (manifest_path.parent / str(action_artifact.get("path", ""))).resolve()
        if action_path.is_file() and file_hash(action_path) == action_artifact.get("sha256"):
            actions = pd.read_parquet(action_path)
            if not actions.empty and {"symbol", "ex_date"}.issubset(actions.columns):
                actions["symbol"] = actions["symbol"].astype(str).str.upper()
                actions["ex_date"] = pd.to_datetime(actions["ex_date"], utc=True, errors="coerce").dt.normalize()
                actions["dividend"] = pd.to_numeric(actions.get("rate", actions.get("value", 0.0)), errors="coerce").fillna(0.0)
                actions["payable_date"] = pd.to_datetime(actions.get("payable_date"), utc=True, errors="coerce").dt.normalize()
                dividends = actions[actions.get("action_type", pd.Series(index=actions.index, dtype=str)).astype(str).str.contains("dividend", case=False, na=False)][["symbol", "ex_date", "dividend", "payable_date"]]
                if not dividends.empty:
                    dividends = dividends.drop_duplicates(["symbol", "ex_date", "dividend"], keep="last")
                    bars["date"] = pd.to_datetime(bars["date"], utc=True).dt.normalize()
                    bars = bars.merge(dividends.rename(columns={"ex_date": "date"}), on=["symbol", "date"], how="left")
                    bars["dividend"] = bars["dividend"].fillna(0.0)
                    bars["dividend_payable_date"] = bars["payable_date"]
                    bars = bars.drop(columns=["payable_date"])
    return bars


def write_backtest_result(result: BacktestResult, output: Path) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    atomic_json(output / "metrics.json", result.metrics)
    atomic_json(output / "run_metadata.json", {
        "schema_version": "etf-cash-backtest-run/v1",
        "strategy_id": result.strategy_id,
        "semiconductor": result.semiconductor,
        "variant": result.variant,
        "cost_scenario": result.cost_scenario,
        "execution_delay_sessions": result.metrics.get("execution_delay_sessions", 0),
        "protocol_hash": result.protocol_hash,
        "status": "DETERMINISTIC_RESEARCH_ONLY",
        "llm_overlay": "NOT_PERFORMANCE_TESTED",
        "live_authority": False,
    })
    for name, frame in (("equity_daily", result.equity), ("signals", result.signals), ("orders", result.orders), ("fills", result.fills), ("cash_ledger", result.cash_ledger), ("trades", result.trades)):
        if frame.empty:
            frame = pd.DataFrame({"status": ["EMPTY"]})
        frame.to_parquet(output / f"{name}.parquet", index=False)
        if name == "trades":
            frame.to_csv(output / "trades.csv", index=False)
    return output


def run_suite(*, bars: pd.DataFrame, output: Path, phase: str, protocol: ResearchProtocol = DEFAULT_PROTOCOL, variants: bool = False, selection_path: Path | None = None) -> Path:
    if output.exists() and any(output.iterdir()):
        raise ValueError("ETF_OUTPUT_DIRECTORY_NOT_EMPTY")
    output.mkdir(parents=True, exist_ok=True)
    atomic_json(output / "protocol.json", {**protocol.as_dict(), "protocol_hash": protocol.protocol_hash})
    bars_snapshot = bars.copy()
    bars_snapshot["date"] = pd.to_datetime(bars_snapshot["date"], utc=True).astype(str)
    snapshot_records = json.loads(bars_snapshot.to_json(date_format="iso", orient="records"))
    atomic_json(output / "data_manifest.json", {
        "schema_version": "etf-cash-inline-data-manifest/v1",
        "status": "FROZEN_INLINE_INPUT",
        "rows": int(len(bars_snapshot)),
        "columns": list(bars_snapshot.columns),
        "data_hash": canonical_hash(snapshot_records),
    })
    registry = candidate_registry()
    atomic_json(output / "candidate_registry.json", {**registry, "registry_hash": canonical_hash(registry)})
    start_end = {
        "development": (protocol.development_start, protocol.development_end),
        "validation": (protocol.validation_start, protocol.validation_end),
        "holdout": (protocol.holdout_start, protocol.holdout_end),
        "continuous": (protocol.development_start, protocol.holdout_end),
    }
    if phase not in start_end:
        raise ValueError("ETF_PHASE_INVALID")
    if phase == "holdout":
        if selection_path is None:
            raise ValueError("ETF_HOLDOUT_SELECTION_REQUIRED")
        selection = _load_json(selection_path)
        expected_selection_hash = canonical_hash({key: value for key, value in selection.items() if key != "selection_hash"})
        if selection.get("schema_version") != "etf-cash-selection/v1" or selection.get("status") != "FROZEN" or selection.get("selection_hash") != expected_selection_hash or selection.get("protocol_hash") != protocol.protocol_hash:
            raise ValueError("ETF_HOLDOUT_SELECTION_INVALID")
    start, end = start_end[phase]
    rows: list[dict[str, Any]] = []
    all_signals: list[pd.DataFrame] = []
    all_orders: list[pd.DataFrame] = []
    all_fills: list[pd.DataFrame] = []
    all_cash: list[pd.DataFrame] = []
    all_equity: list[pd.DataFrame] = []
    all_trades: list[pd.DataFrame] = []
    benchmark_returns: dict[tuple[str, str], float] = {}
    benchmark_results: dict[tuple[str, str], BacktestResult] = {}
    candidate_results: list[tuple[BacktestResult, dict[str, Any], Path]] = []
    delay_rows: list[dict[str, Any]] = []
    for strategy_id in [f"S{index:02d}" for index in range(1, 11)]:
        for semiconductor in ("SOXX", "SMH"):
            variants_for_run = ("primary",) if not variants else {
                "S01": ("primary", "momentum_105", "momentum_147"), "S02": ("primary", "ema_80", "ema_120"), "S03": ("primary", "breakout_40", "breakout_70"), "S04": ("primary", "rsi_5", "rsi_15"), "S05": ("primary", "band_1.75", "band_2.25"), "S06": ("primary", "ratio_sma_15", "ratio_sma_25"), "S07": ("primary", "vol_target_20", "vol_target_30"), "S08": ("primary", "breakout_15", "breakout_25"), "S09": ("primary", "contraction_15", "contraction_25"), "S10": ("primary", "trend_share_60", "trend_share_80")
            }[strategy_id]
            for variant in variants_for_run:
                candidate_id = f"{strategy_id}__QQQM_{semiconductor}__{variant}"
                for cost in protocol.costs:
                    result = run_backtest(bars, strategy_id=strategy_id, semiconductor=semiconductor, variant=variant, cost=cost, protocol=protocol, start=start, end=end)
                    result_dir = output / candidate_id / cost.name
                    write_backtest_result(result, result_dir)
                    row = {"candidate_id": candidate_id, **result.metrics, "phase": phase}
                    rows.append(row)
                    candidate_results.append((result, row, result_dir))
                    for frame, destination in ((result.signals, all_signals), (result.orders, all_orders), (result.fills, all_fills), (result.cash_ledger, all_cash), (result.equity, all_equity), (result.trades, all_trades)):
                        if not frame.empty:
                            enriched = frame.copy()
                            enriched["candidate_id"] = candidate_id
                            enriched["cost_scenario"] = cost.name
                            destination.append(enriched)
                if variant == "primary":
                    delayed = run_backtest(bars, strategy_id=strategy_id, semiconductor=semiconductor, variant=variant, cost=protocol.costs[0], protocol=protocol, start=start, end=end, execution_delay_sessions=1)
                    delay_dir = output / candidate_id / "delay_1_session"
                    write_backtest_result(delayed, delay_dir)
                    delay_rows.append({"candidate_id": candidate_id, "phase": phase, **delayed.metrics})
    # Benchmarks are calculated once per symbol/cost and stored for reporting.
    for benchmark_symbol in ("QQQ", "SPY", "QQQM", "SOXX", "SMH"):
        benchmark_bars = bars[bars["symbol"].astype(str).str.upper() == benchmark_symbol]
        if benchmark_bars.empty:
            continue
        for cost in protocol.costs:
            benchmark = run_benchmark(benchmark_bars, symbol=benchmark_symbol, protocol=protocol, cost=cost, start=start, end=end)
            write_backtest_result(benchmark, output / "benchmarks" / benchmark_symbol / cost.name)
            benchmark_results[(benchmark_symbol, cost.name)] = benchmark
            benchmark_returns[(benchmark_symbol, cost.name)] = float(benchmark.metrics.get("net_return", 0.0))
    for semiconductor in ("SOXX", "SMH"):
        pair_bars = bars[bars["symbol"].astype(str).str.upper().isin({"QQQM", semiconductor})]
        if len(pair_bars):
            for cost in protocol.costs:
                pair = run_static_pair_benchmark(pair_bars, semiconductor=semiconductor, protocol=protocol, cost=cost, start=start, end=end)
                write_backtest_result(pair, output / "benchmarks" / f"QQQM_{semiconductor}_50_50" / cost.name)
                benchmark_results[(f"QQQM_{semiconductor}_50_50", cost.name)] = pair
                benchmark_returns[(f"QQQM_{semiconductor}_50_50", cost.name)] = float(pair.metrics.get("net_return", 0.0))
    for result, row, result_dir in candidate_results:
        for benchmark_symbol, prefix in (("QQQ", "qqq"), ("SPY", "spy"), ("QQQM", "qqqm"), ("SOXX", "soxx"), ("SMH", "smh"), (f"QQQM_{result.semiconductor}_50_50", "static_pair")):
            benchmark = benchmark_results.get((benchmark_symbol, result.cost_scenario))
            if benchmark is None:
                continue
            row.update(add_benchmark_comparison(result.equity, benchmark.equity, prefix=prefix))
            joined = result.equity[["date", "equity"]].merge(benchmark.equity[["date", "equity"]], on="date", suffixes=("_strategy", "_benchmark"))
            row[f"{prefix}_bootstrap"] = moving_block_bootstrap(joined["equity_strategy"].pct_change(), joined["equity_benchmark"].pct_change(), samples=protocol.bootstrap_samples, block_length=protocol.bootstrap_block_length, seed=protocol.bootstrap_seed)
        result.metrics.update({key: value for key, value in row.items() if key not in {"candidate_id", "phase"}})
        atomic_json(result_dir / "metrics.json", result.metrics)
    aggregate_frames = (("signals", all_signals), ("orders", all_orders), ("fills", all_fills), ("cash_ledger", all_cash), ("equity_daily", all_equity), ("trades", all_trades))
    for name, frames in aggregate_frames:
        if frames:
            aggregate = pd.concat(frames, ignore_index=True)
            aggregate.to_parquet(output / f"{name}.parquet", index=False)
            if name == "trades":
                aggregate.to_csv(output / "trades.csv", index=False)
        else:
            pd.DataFrame({"status": ["EMPTY"]}).to_parquet(output / f"{name}.parquet", index=False)
            if name == "trades":
                pd.DataFrame({"status": ["EMPTY"]}).to_csv(output / "trades.csv", index=False)
    leaderboard = pd.DataFrame(rows)
    for benchmark_symbol, column in (("QQQ", "qqq_return_gap"), ("SPY", "spy_return_gap"), ("QQQM", "qqqm_return_gap"), ("SOXX", "soxx_return_gap"), ("SMH", "smh_return_gap")):
        leaderboard[column] = leaderboard.apply(lambda row, symbol=benchmark_symbol: float(row["net_return"] - benchmark_returns.get((symbol, row["cost_scenario"]), 0.0)), axis=1)
    leaderboard["static_pair_return_gap"] = leaderboard.apply(lambda row: float(row["net_return"] - benchmark_returns.get((f"QQQM_{row['semiconductor']}_50_50", row["cost_scenario"]), 0.0)), axis=1)
    leaderboard.to_csv(output / "leaderboard.csv", index=False)
    metrics_rows = json.loads(pd.DataFrame(rows).to_json(orient="records"))
    atomic_json(output / "metrics.json", {"schema_version": "etf-cash-suite-metrics/v1", "phase": phase, "protocol_hash": protocol.protocol_hash, "rows": metrics_rows})
    pd.DataFrame(delay_rows).to_csv(output / "delay_stress.csv", index=False)
    atomic_json(output / "charts.json", {
        "schema_version": "etf-cash-charts/v1",
        "charts": [
            {"id": "growth_of_1000", "source": "equity_daily.parquet"},
            {"id": "cumulative_profit", "source": "equity_daily.parquet"},
            {"id": "drawdown", "source": "equity_daily.parquet"},
            {"id": "monthly_return_heatmap", "source": "equity_daily.parquet"},
            {"id": "return_vs_drawdown", "source": "leaderboard.csv"},
            {"id": "cost_scenarios", "source": "leaderboard.csv"},
            {"id": "soxx_vs_smh", "source": "leaderboard.csv"},
            {"id": "execution_delay", "source": "delay_stress.csv"},
        ],
    })
    atomic_json(output / "suite_metadata.json", {"schema_version": "etf-cash-suite/v1", "phase": phase, "protocol_hash": protocol.protocol_hash, "candidate_count": len(rows), "delay_stress_candidate_count": len(delay_rows), "status": "DETERMINISTIC_RESEARCH_ONLY"})
    return output


def freeze_selection(*, suite_dir: Path, output_path: Path, protocol: ResearchProtocol = DEFAULT_PROTOCOL) -> Path:
    leaderboard = pd.read_csv(suite_dir / "leaderboard.csv")
    base = leaderboard[(leaderboard["cost_scenario"] == "base") & (leaderboard["phase"] == "validation")].copy()
    # Sensitivity runs are diagnostics only; the frozen shortlist can contain
    # primary candidates from the ten-by-two registry only.
    base = base[base["candidate_id"].astype(str).str.endswith("__primary")].copy()
    stress = leaderboard[(leaderboard["cost_scenario"] == "stress") & (leaderboard["phase"] == "validation")][["candidate_id", "net_return", "max_drawdown"]].rename(columns={"net_return": "stress_net_return", "max_drawdown": "stress_max_drawdown"})
    base = base.merge(stress, on="candidate_id", how="left")
    base["qualifies"] = (base["net_return"] > 0) & (base["stress_net_return"] > 0) & (base["max_drawdown"] <= protocol.max_drawdown) & (base["stress_max_drawdown"] <= protocol.max_drawdown)
    ranked = base.sort_values(["qualifies", "net_return", "max_drawdown", "turnover", "candidate_id"], ascending=[False, False, True, True, True], kind="stable")
    shortlisted = ranked[ranked["qualifies"]].head(3)["candidate_id"].tolist()
    # Pandas represents undefined ratios as NaN; convert those to JSON null
    # before hashing so the selection envelope remains canonical and replayable.
    ranked_rows = json.loads(ranked.to_json(orient="records"))
    selection = {"schema_version": "etf-cash-selection/v1", "status": "FROZEN", "protocol_hash": protocol.protocol_hash, "source_leaderboard_hash": file_hash(suite_dir / "leaderboard.csv"), "shortlist": shortlisted, "rows": ranked_rows, "holdout_required": True, "selection_hash": None}
    selection["selection_hash"] = canonical_hash({key: value for key, value in selection.items() if key != "selection_hash"})
    atomic_json(output_path, selection)
    return output_path


def finalize_selection(*, selection_path: Path, holdout_suite: Path, continuous_suite: Path, output_path: Path, protocol: ResearchProtocol = DEFAULT_PROTOCOL) -> Path:
    """Apply the frozen shortlist to holdout and full-period evidence."""
    selection = _load_json(selection_path)
    expected_selection_hash = canonical_hash({key: value for key, value in selection.items() if key != "selection_hash"})
    if selection.get("schema_version") != "etf-cash-selection/v1" or selection.get("status") != "FROZEN" or selection.get("selection_hash") != expected_selection_hash or selection.get("protocol_hash") != protocol.protocol_hash:
        raise ValueError("ETF_SELECTION_INVALID")
    holdout = pd.read_csv(holdout_suite / "leaderboard.csv")
    continuous = pd.read_csv(continuous_suite / "leaderboard.csv")
    shortlist = list(selection.get("shortlist", []))
    rows: list[dict[str, Any]] = []
    nominated: str | None = None
    for candidate_id in shortlist:
        h_base = holdout[(holdout.candidate_id == candidate_id) & (holdout.cost_scenario == "base")].iloc[0]
        h_stress = holdout[(holdout.candidate_id == candidate_id) & (holdout.cost_scenario == "stress")].iloc[0]
        c_base = continuous[(continuous.candidate_id == candidate_id) & (continuous.cost_scenario == "base")].iloc[0]
        qualifies = bool(h_base.net_return > 0 and h_stress.net_return > 0 and h_base.max_drawdown <= protocol.max_drawdown and h_stress.max_drawdown <= protocol.max_drawdown and c_base.net_return > 0 and c_base.max_drawdown <= protocol.max_drawdown)
        row = {"candidate_id": candidate_id, "holdout_base": float(h_base.net_return), "holdout_stress": float(h_stress.net_return), "holdout_drawdown": float(h_base.max_drawdown), "continuous_return": float(c_base.net_return), "continuous_drawdown": float(c_base.max_drawdown), "qualifies": qualifies}
        rows.append(row)
        if qualifies and nominated is None:
            nominated = candidate_id
    result = {"schema_version": "etf-cash-final-selection/v1", "status": "QUALIFIED" if nominated else "NO_QUALIFYING_STRATEGY", "protocol_hash": protocol.protocol_hash, "source_selection_hash": selection["selection_hash"], "nominated_candidate": nominated, "rows": rows, "final_selection_hash": None}
    result["final_selection_hash"] = canonical_hash({key: value for key, value in result.items() if key != "final_selection_hash"})
    atomic_json(output_path, result)
    return output_path


def build_report(*, suite_dir: Path, output_path: Path) -> Path:
    output_path = output_path if output_path.suffix.lower() == ".html" else output_path.with_suffix(".html")
    leaderboard = pd.read_csv(suite_dir / "leaderboard.csv")
    columns = [column for column in ("candidate_id", "phase", "cost_scenario", "starting_equity", "ending_equity", "net_pnl", "net_return", "max_drawdown", "cagr", "sharpe_zero_rf", "sortino_zero_mar", "trading_costs", "turnover") if column in leaderboard.columns]
    table_frame = leaderboard[columns].sort_values(["phase", "cost_scenario", "net_return"], ascending=[True, True, False])
    table = table_frame.to_html(index=False, float_format=lambda value: f"{value:.6f}")
    chart_dir = output_path.parent / "charts"
    base = leaderboard[leaderboard["cost_scenario"] == "base"].copy()
    primary = base[base["candidate_id"].astype(str).str.endswith("__primary")].sort_values("net_return", ascending=False)
    selected_ids = primary.head(3)["candidate_id"].tolist()
    equity = pd.read_parquet(suite_dir / "equity_daily.parquet") if (suite_dir / "equity_daily.parquet").is_file() else pd.DataFrame()
    line_series: list[tuple[str, list[float]]] = []
    for candidate_id in selected_ids:
        subset = equity[(equity.get("candidate_id", "") == candidate_id) & (equity.get("cost_scenario", "") == "base")].sort_values("date")
        if not subset.empty:
            line_series.append((candidate_id, pd.to_numeric(subset["equity"], errors="coerce").tolist()))
    for benchmark_symbol in ("QQQ", "SPY"):
        metric_path = suite_dir / "benchmarks" / benchmark_symbol / "base" / "equity_daily.parquet"
        if metric_path.is_file():
            subset = pd.read_parquet(metric_path).sort_values("date")
            line_series.append((benchmark_symbol, pd.to_numeric(subset["equity"], errors="coerce").tolist()))
    _svg_line(chart_dir / "growth_of_1000.svg", "Growth of $1,000", line_series, y_label="equity")
    _svg_line(chart_dir / "cumulative_profit.svg", "Cumulative dollar profit", [(label, [value - 1000.0 for value in points]) for label, points in line_series], y_label="dollars")
    drawdown_series: list[tuple[str, list[float]]] = []
    for label, points in line_series:
        values = pd.Series(points, dtype=float)
        drawdown_series.append((label, (values / values.cummax() - 1.0).tolist()))
    _svg_line(chart_dir / "drawdown.svg", "Daily marked-to-market drawdown", drawdown_series, y_label="drawdown")
    _svg_scatter(chart_dir / "return_vs_drawdown.svg", "Return versus drawdown", base[base["candidate_id"].astype(str).str.endswith("__primary")])
    if selected_ids:
        cost_rows = leaderboard[leaderboard["candidate_id"] == selected_ids[0]].set_index("cost_scenario")
        _svg_bars(chart_dir / "cost_scenarios.svg", f"Base/stress/severe costs: {selected_ids[0]}", list(cost_rows.index), [float(item) for item in cost_rows["net_return"].tolist()], y_label="net return")
    pair_rows = base[base["candidate_id"].astype(str).str.endswith("__primary")].copy()
    pair_rows["strategy_id"] = pair_rows["candidate_id"].astype(str).str.split("__").str[0]
    grouped = pair_rows.groupby(["strategy_id", "semiconductor"], as_index=False)["net_return"].first()
    labels = [f"{row.strategy_id}-{row.semiconductor}" for row in grouped.itertuples()]
    _svg_bars(chart_dir / "soxx_vs_smh.svg", "SOXX versus SMH paired comparison", labels, grouped["net_return"].astype(float).tolist(), y_label="net return")
    monthly_path = chart_dir / "monthly_return_heatmap.svg"
    monthly_rows = []
    if selected_ids:
        metric_path = suite_dir / selected_ids[0] / "base" / "metrics.json"
        if metric_path.is_file():
            monthly = _load_json(metric_path).get("monthly_returns", {})
            monthly_rows = [(str(key), float(value)) for key, value in monthly.items()]
    heat_elements = ["<rect width='920' height='260' fill='white'/><text x='48' y='24' font-size='16' font-family='system-ui'>Monthly-return heatmap</text>"]
    for index, (label, value) in enumerate(monthly_rows):
        x = 48 + (index % 12) * 70
        y = 42 + (index // 12) * 36
        intensity = min(1.0, abs(value) / 0.15)
        color = f"rgb({int(220 if value < 0 else 34)},{int(80 + 150 * (1 - intensity))},{int(80 if value < 0 else 100)})"
        heat_elements.append(f"<rect x='{x}' y='{y}' width='64' height='30' fill='{color}'/><text x='{x + 2}' y='{y + 12}' font-size='9' fill='white' font-family='system-ui'>{html_lib.escape(label)}</text><text x='{x + 2}' y='{y + 24}' font-size='10' fill='white' font-family='system-ui'>{value:.1%}</text>")
    atomic_bytes(monthly_path, ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 920 260'>%s</svg>" % "".join(heat_elements)).encode("utf-8"))
    chart_links = ["growth_of_1000", "cumulative_profit", "drawdown", "monthly_return_heatmap", "return_vs_drawdown", "cost_scenarios", "soxx_vs_smh"]
    chart_html = "".join(f"<figure><img src='charts/{name}.svg' alt='{name}'><figcaption>{name}</figcaption></figure>" for name in chart_links)
    html_doc = """<!doctype html><html><head><meta charset='utf-8'><title>ETF cash research</title><style>body{font:14px system-ui;margin:2rem}table{border-collapse:collapse;display:block;overflow:auto}td,th{padding:.3rem;border:1px solid #ddd;white-space:nowrap}.notice{padding:1rem;background:#fff3cd}figure{display:inline-block;vertical-align:top;margin:1rem 1rem 1rem 0}img{width:460px;border:1px solid #ddd}</style></head><body><h1>QQQM + semiconductor ETF cash research</h1><div class='notice'>Deterministic research backtest. The Gemini LLM overlay was not performance-tested. This report grants no live-trading authority.</div><h2>Metrics</h2>""" + table + "<h2>Charts</h2>" + chart_html + "</body></html>"
    atomic_bytes(output_path, html_doc.encode("utf-8"))
    markdown = "# QQQM + semiconductor ETF cash research\n\nDeterministic research backtest; LLM overlay not performance-tested; no live authorization.\n\n" + _markdown_table(table_frame) + "\n\nCharts are in the sibling `charts/` directory.\n"
    atomic_bytes(output_path.with_suffix(".md"), markdown.encode("utf-8"))
    return output_path
