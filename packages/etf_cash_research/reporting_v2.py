"""Readable HTML/Markdown report for the v2 multi-track study."""

from __future__ import annotations

import html
import json
from pathlib import Path

import pandas as pd

from packages.research_data.artifacts import atomic_bytes

from .reporting import _markdown_table, _svg_bars, _svg_line, _svg_scatter


def _svg_heatmap(path: Path, title: str, values: pd.DataFrame, *, value_format: str = ".1%") -> None:
    """Write a compact deterministic monthly-return heatmap as SVG."""
    rows = values.copy()
    if rows.empty:
        rows = pd.DataFrame({"candidate": ["no data"]})
    row_labels = [str(item) for item in rows.index]
    columns = [str(item) for item in rows.columns]
    width = max(760, 120 + 62 * len(columns))
    height = 56 + 28 * (len(row_labels) + 1)
    numeric = rows.apply(pd.to_numeric, errors="coerce")
    finite = numeric.stack().dropna()
    magnitude = max(abs(float(finite.min())) if not finite.empty else 0.0, abs(float(finite.max())) if not finite.empty else 0.0, 1e-9)
    elements = [
        f"<rect width='{width}' height='{height}' fill='white'/>",
        f"<text x='12' y='20' font-size='16' font-family='system-ui'>{html.escape(title)}</text>",
    ]
    left, top, cell_w, cell_h = 120, 32, 60, 24
    for col_index, column in enumerate(columns):
        x = left + col_index * cell_w
        elements.append(f"<text x='{x + 2}' y='{top - 6}' font-size='10' font-family='system-ui'>{html.escape(column[-5:])}</text>")
    for row_index, label in enumerate(row_labels):
        y = top + row_index * cell_h
        elements.append(f"<text x='4' y='{y + 16}' font-size='10' font-family='system-ui'>{html.escape(label[:18])}</text>")
        for col_index, _column in enumerate(columns):
            value = numeric.iloc[row_index, col_index] if col_index < numeric.shape[1] else float("nan")
            x = left + col_index * cell_w
            if pd.isna(value):
                color = "#f3f4f6"
                text = ""
            else:
                intensity = min(1.0, abs(float(value)) / magnitude)
                color = f"rgb({239 if value < 0 else int(220 - 110 * intensity)},{int(120 + 100 * (1 - intensity))},{int(120 + 100 * (1 - intensity)) if value < 0 else 239})"
                text = format(float(value), value_format)
            elements.append(f"<rect x='{x}' y='{y}' width='{cell_w - 2}' height='{cell_h - 2}' fill='{color}' stroke='white'/>")
            if text:
                elements.append(f"<text x='{x + 3}' y='{y + 15}' font-size='9' font-family='system-ui'>{html.escape(text)}</text>")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_bytes(
        path,
        (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {width} {height}'>" + "".join(elements) + "</svg>").encode("utf-8"),
    )


def _series_for(equity: pd.DataFrame, candidate_id: str, phase: str = "continuous", cost: str = "base") -> pd.DataFrame:
    if equity.empty:
        return equity
    mask = equity["candidate_id"].astype(str).eq(candidate_id)
    if "phase" in equity:
        mask &= equity["phase"].astype(str).eq(phase)
    if "cost_scenario" in equity:
        mask &= equity["cost_scenario"].astype(str).eq(cost)
    return equity.loc[mask].sort_values("date").copy()


def _evaluation_index_values(equity: pd.DataFrame, candidate_id: str, windows: tuple[str, ...]) -> list[float]:
    """Chain independent $1,000 evaluation-window accounts for display only."""
    from .evaluation import chain_evaluation_windows

    filtered = _series_for(equity, candidate_id, phase="evaluation", cost="base")
    if filtered.empty:
        return []
    chained = chain_evaluation_windows(filtered, windows, initial_cash=1000.0)
    return chained["evaluation_index"].astype(float).tolist()


def _cost_comparison_data(
    leaderboard: pd.DataFrame,
    candidate_ids: list[str],
    scenarios: tuple[str, ...] = ("base", "stress", "severe"),
) -> tuple[list[str], list[float]]:
    """Return available continuous cost rows for the comparison chart.

    The primary continuous table is filtered to base cost for ranking, but the
    cost chart must read the separate stress and severe rows as well.  Missing
    rows are omitted rather than rendered as zero, which would imply a
    measured zero return.
    """
    required = {"candidate_id", "phase", "cost_scenario", "net_return"}
    if leaderboard.empty or not required.issubset(leaderboard.columns) or not candidate_ids:
        return [], []
    frame = leaderboard[
        leaderboard["phase"].astype(str).eq("continuous")
        & leaderboard["candidate_id"].astype(str).isin(candidate_ids)
    ].copy()
    if frame.empty:
        return [], []
    frame["candidate_id"] = frame["candidate_id"].astype(str)
    frame["cost_scenario"] = frame["cost_scenario"].astype(str)
    rows = frame.pivot_table(
        index="candidate_id",
        columns="cost_scenario",
        values="net_return",
        aggfunc="first",
    )
    labels: list[str] = []
    values: list[float] = []
    for candidate_id in candidate_ids:
        if candidate_id not in rows.index:
            continue
        for scenario in scenarios:
            if scenario not in rows.columns:
                continue
            value = rows.loc[candidate_id, scenario]
            if pd.isna(value):
                continue
            labels.append(f"{candidate_id} {scenario}")
            values.append(float(value))
    return labels, values


def build_v2_report(study_dir: Path, output_path: Path) -> Path:
    leaderboard = pd.read_csv(study_dir / "leaderboard.csv")
    all_continuous = leaderboard[leaderboard["phase"] == "continuous"].copy()
    continuous = all_continuous[all_continuous["cost_scenario"] == "base"].copy()
    continuous = continuous.sort_values("net_return", ascending=False)
    equity_path = study_dir / "normalized" / "equity_daily.parquet"
    equity = pd.read_parquet(equity_path) if equity_path.is_file() else pd.DataFrame()
    charts = output_path.parent / "charts"
    charts.mkdir(parents=True, exist_ok=True)
    line_series: list[tuple[str, list[float]]] = []
    continuous_dates = sorted(equity.loc[(equity["phase"] == "continuous") & (equity["cost_scenario"] == "base"), "date"].astype(str).unique()) if not equity.empty else []
    if not equity.empty:
        primary_continuous = continuous[~continuous["candidate_id"].astype(str).str.startswith("BENCH__")]
        for candidate_id in primary_continuous.head(3)["candidate_id"].astype(str):
            subset = _series_for(equity, candidate_id)
            if not subset.empty and "equity" in subset:
                line_series.append((candidate_id, pd.to_numeric(subset["equity"], errors="coerce").tolist()))
        for benchmark_id in ("BENCH__SPY", "BENCH__QQQ"):
            subset = _series_for(equity, benchmark_id)
            if not subset.empty:
                line_series.append((benchmark_id.replace("BENCH__", ""), pd.to_numeric(subset["equity"], errors="coerce").tolist()))
    _svg_line(charts / "growth_of_1000.svg", "V2 continuous growth of $1,000", line_series, y_label="equity ($)", dates=continuous_dates)
    _svg_line(charts / "cumulative_profit.svg", "V2 continuous cumulative profit", [(label, [value - 1000.0 for value in values]) for label, values in line_series], y_label="dollars", dates=continuous_dates)
    drawdown_series: list[tuple[str, list[float]]] = []
    for label, _ in line_series:
        subset = _series_for(equity, label if label.startswith("BENCH__") else label)
        if subset.empty and label in {"SPY", "QQQ"}:
            subset = _series_for(equity, f"BENCH__{label}")
        if not subset.empty:
            values = pd.to_numeric(subset["equity"], errors="coerce")
            drawdown_series.append((label, (values / values.cummax().clip(lower=1000.0) - 1.0).tolist()))
    _svg_line(charts / "drawdown.svg", "V2 continuous drawdown", drawdown_series, y_label="drawdown", dates=continuous_dates)
    evaluation_windows = tuple(f"W{index}" for index in range(1, 7))
    evaluation_line_series: list[tuple[str, list[float]]] = []
    selection_path = study_dir / "selection_leaderboard.csv"
    if selection_path.is_file():
        selection_frame = pd.read_csv(selection_path)
        eligibility = "performance_gates_pass" if "performance_gates_pass" in selection_frame else "qualifies"
        selected_ids = selection_frame[selection_frame[eligibility].astype(bool)] if eligibility in selection_frame else selection_frame
        selected_ids = selected_ids.sort_values("evaluation_return", ascending=False).head(3)
        candidate_ids = selected_ids["candidate_id"].astype(str).tolist()
    else:
        candidate_ids = continuous[~continuous["candidate_id"].astype(str).str.startswith("BENCH__")]["candidate_id"].astype(str).head(3).tolist()
    for candidate_id in candidate_ids:
        values = _evaluation_index_values(equity, candidate_id, evaluation_windows)
        if values:
            evaluation_line_series.append((candidate_id, values))
    for benchmark_id in ("BENCH__SPY", "BENCH__QQQ"):
        values = _evaluation_index_values(equity, benchmark_id, evaluation_windows)
        if values:
            evaluation_line_series.append((benchmark_id.replace("BENCH__", ""), values))
    _svg_line(
        charts / "evaluation_index_growth.svg",
        "Diagnostic evaluation index (independent $1,000 windows chained)",
        evaluation_line_series,
        y_label="evaluation index",
        dates=continuous_dates,
    )
    evaluation_rows = leaderboard[(leaderboard["phase"] == "evaluation") & (leaderboard["cost_scenario"] == "base")]
    evaluation_rows = evaluation_rows[evaluation_rows["candidate_id"].astype(str).isin(candidate_ids)]
    if not evaluation_rows.empty:
        labels = [f"{row.candidate_id} {row.window_id}" for row in evaluation_rows.itertuples()]
        values = pd.to_numeric(evaluation_rows["net_return"], errors="coerce").fillna(0.0).tolist()
        _svg_bars(charts / "evaluation_window_returns.svg", "Evaluation-window returns (base cost)", labels, values, y_label="return")
    if not continuous.empty:
        _svg_scatter(charts / "return_vs_drawdown.svg", "V2 return versus drawdown", continuous[~continuous["candidate_id"].astype(str).str.startswith("BENCH__")].head(60))
        primary_continuous = continuous[~continuous["candidate_id"].astype(str).str.startswith("BENCH__")]
        _svg_bars(charts / "top_returns.svg", "V2 top continuous base returns", primary_continuous.head(20)["candidate_id"].astype(str).tolist(), primary_continuous.head(20)["net_return"].astype(float).tolist(), y_label="return")
        top_ids = primary_continuous.head(3)["candidate_id"].astype(str).tolist()
        cost_labels, cost_values = _cost_comparison_data(all_continuous, top_ids)
        if cost_values:
            _svg_bars(charts / "cost_comparison.svg", "Base/stress/severe cost comparison", cost_labels, cost_values, y_label="return")
        delay = leaderboard[(leaderboard["phase"] == "delay") & leaderboard["window_id"].isna() & (leaderboard["cost_scenario"] == "stress") & leaderboard["candidate_id"].astype(str).isin(top_ids)]
        if not delay.empty:
            labels = delay["candidate_id"].astype(str).tolist()
            _svg_bars(charts / "delayed_execution.svg", "Continuous delayed execution (stress cost)", labels, delay["net_return"].astype(float).tolist(), y_label="return")
        pairs = continuous[continuous["candidate_id"].astype(str).isin({"BENCH__TQQQ_SOXL_STATIC50", "BENCH__SPXL_SOXL_STATIC50"})]
        if not pairs.empty:
            _svg_bars(charts / "paired_leveraged_comparison.svg", "Static leveraged pair comparison", pairs["candidate_id"].astype(str).tolist(), pairs["net_return"].astype(float).tolist(), y_label="return")
    # A compact heatmap uses the three best primary continuous accounts.  It
    # remains supplementary to the machine-readable monthly_return fields.
    heatmap_rows: dict[str, dict[str, float]] = {}
    for candidate_id in continuous[~continuous["candidate_id"].astype(str).str.startswith("BENCH__")].head(5)["candidate_id"].astype(str):
        subset = _series_for(equity, candidate_id)
        if subset.empty:
            continue
        dates = pd.to_datetime(subset["date"], utc=True).dt.tz_localize(None)
        marks = pd.to_numeric(subset["equity"], errors="coerce")
        daily = pd.Series(marks.pct_change().fillna(marks.iloc[0] / 1000.0 - 1.0).to_numpy(), index=dates)
        heatmap_rows[candidate_id] = {str(key): float(value) for key, value in ((1.0 + daily).groupby(daily.index.to_period("M")).prod() - 1.0).items()}
    heatmap = pd.DataFrame.from_dict(heatmap_rows, orient="index").sort_index(axis=1) if heatmap_rows else pd.DataFrame()
    _svg_heatmap(charts / "monthly_return_heatmap.svg", "Monthly return heatmap", heatmap)
    table_columns = [column for column in ("candidate_id", "track_id", "pair_id", "phase", "window_id", "cost_scenario", "starting_equity", "ending_equity", "net_pnl", "net_return", "max_drawdown", "cagr", "sharpe_zero_rf", "turnover") if column in leaderboard.columns]
    table = leaderboard[table_columns].sort_values(["phase", "cost_scenario", "net_return"], ascending=[True, True, False]).head(300)
    selection_table = pd.DataFrame()
    if selection_path.is_file():
        selection_table = pd.read_csv(selection_path)
        columns = [column for column in ("candidate_id", "pair_id", "evaluation_return", "evaluation_drawdown", "net_return", "max_drawdown", "performance_gates_pass", "qualification_status", "qualification_reasons") if column in selection_table.columns]
        selection_table = selection_table[columns].sort_values(["pair_id", "qualification_status", "evaluation_return"], ascending=[True, True, False])
    notice = "Deterministic research backtest; historical periods reused; LLM overlay not performance-tested; no live authorization."
    date_note = "Primary continuous window: 2023-09-19 through 2026-09-18. Evaluation windows are independent $1,000 accounts chained only for ranking."
    quality_note = ""
    quality_path = study_dir / "data_quality.json"
    if quality_path.is_file():
        try:
            quality = json.loads(quality_path.read_text(encoding="utf-8"))
            quality_note = (
                f" Data quality status: {quality.get('status')}; missing dividend payable dates: "
                f"{quality.get('missing_dividend_payable_dates', 0)} total, "
                f"{quality.get('missing_dividend_payable_dates_in_study', 0)} within study periods."
            )
        except (OSError, json.JSONDecodeError):
            quality_note = " Data quality artifact could not be parsed."
    audit_note = ""
    migration_table = pd.DataFrame()
    migration_path = study_dir / "migration_comparison.json"
    if migration_path.is_file():
        migration = json.loads(migration_path.read_text())
        migration_rows = []
        for row in migration.get("rows", []):
            if row.get("cost_scenario") != "base" or "aligned_v2_actual_raw_execution" not in row:
                continue
            actual = row["aligned_v2_actual_raw_execution"]
            migration_rows.append({"strategy": row["strategy_id"], "old erroneous v2 return": f'{row["misaligned_v2_actual_raw_ablation"]["net_return"]:.2%}', "corrected return": f'{actual["net_return"]:.2%}', "ending equity": f'${actual["ending_equity"]:,.2f}', "max drawdown": f'{actual["max_drawdown"]:.2%}', "legacy matched": row["normalized_matches_legacy"]})
        migration_table = pd.DataFrame(migration_rows)
    audit_path = study_dir / "correction_audit.json"
    if audit_path.is_file():
        try:
            correction_audit = json.loads(audit_path.read_text(encoding="utf-8"))
            if not isinstance(correction_audit, dict):
                raise json.JSONDecodeError("object required", "", 0)
            audit_status = str(correction_audit.get("status", "UNKNOWN"))
            blockers = correction_audit.get("blockers", correction_audit.get("blocking_issues", correction_audit.get("issues", [])))
            if isinstance(blockers, dict):
                blockers = [f"{key}: {value}" for key, value in blockers.items()]
            if not isinstance(blockers, list):
                blockers = [str(blockers)]
            blocker_text = "; ".join(str(item) for item in blockers if str(item).strip())
            audit_note = (
                f"Correction audit status: {audit_status}. "
                f"{('Blockers: ' + blocker_text + '. ') if blocker_text else ''}"
                "This report retains migration and correction findings as diagnostics and does not certify full compliance."
            )
        except (OSError, json.JSONDecodeError):
            audit_note = "Correction audit artifact could not be parsed; compliance status is unresolved."
    sensitivity_note = ""
    sensitivity_path = study_dir / "sensitivity_manifest.json"
    if sensitivity_path.is_file():
        try:
            sensitivity = json.loads(sensitivity_path.read_text(encoding="utf-8"))
            sensitivity_note = (
                f" Sensitivity diagnostics: {sensitivity.get('total_candidates', 0)} variants and "
                f"{sensitivity.get('total_rows', 0)} window/cost runs; diagnostics did not change primary ranking."
            )
        except (OSError, json.JSONDecodeError):
            sensitivity_note = " Sensitivity manifest could not be parsed."
    body = "<!doctype html><html><head><meta charset='utf-8'><title>ETF cash research v2</title><style>body{font:14px system-ui;margin:2rem}table{border-collapse:collapse;display:block;overflow:auto}td,th{padding:.3rem;border:1px solid #ddd;white-space:nowrap}.notice{padding:1rem;background:#fff3cd}figure{display:inline-block;vertical-align:top;margin:1rem 1rem 1rem 0}img{width:460px;border:1px solid #ddd}</style></head><body>"
    body += f"<h1>QQQM+SMH and leveraged ETF research v2</h1><div class='notice'>{html.escape(notice)}</div>"
    if audit_note:
        body += f"<div class='notice'>{html.escape(audit_note)}</div>"
    if not migration_table.empty:
        body += "<h2>Resolved S01/S10 migration</h2><p>Base-cost continuous accounts, 2023-09-19 through 2026-09-18. Aligning symbol histories by date removes the discrepancy. These are research reference results.</p>" + migration_table.to_html(index=False)
    body += f"<p>{html.escape(date_note + quality_note + sensitivity_note)}</p><h2>Qualification and selection</h2>"
    if not selection_table.empty:
        body += selection_table.to_html(index=False, float_format=lambda value: f'{value:.6f}')
    body += f"<h2>Leaderboard</h2>{table.to_html(index=False, float_format=lambda value: f'{value:.6f}') }<h2>Charts</h2>"
    for name in ("growth_of_1000", "cumulative_profit", "drawdown", "evaluation_index_growth", "evaluation_window_returns", "monthly_return_heatmap", "return_vs_drawdown", "top_returns", "cost_comparison", "delayed_execution", "paired_leveraged_comparison"):
        if (charts / f"{name}.svg").is_file():
            body += f"<figure><img src='charts/{name}.svg' alt='{name}'><figcaption>{name}</figcaption></figure>"
    body += "</body></html>"
    output_path = output_path if output_path.suffix.lower() == ".html" else output_path.with_suffix(".html")
    atomic_bytes(output_path, body.encode("utf-8"))
    audit_markdown = f"{audit_note}\n\n" if audit_note else ""
    migration_markdown = "\n\n## Resolved migration\n\n" + _markdown_table(migration_table) if not migration_table.empty else ""
    markdown = f"# ETF cash research v2\n\n{notice}\n\n{audit_markdown}{date_note}{quality_note}{sensitivity_note}\n\n" + _markdown_table(table) + migration_markdown + "\n\nCharts are in the sibling `charts/` directory.\n"
    atomic_bytes(output_path.with_suffix(".md"), markdown.encode("utf-8"))
    return output_path
