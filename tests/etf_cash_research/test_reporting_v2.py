from __future__ import annotations

import pandas as pd
import pytest

import packages.etf_cash_research.reporting_v2 as reporting_v2
from packages.etf_cash_research.reporting_v2 import (
    _cost_comparison_data,
    _evaluation_index_values,
    build_v2_report,
)


def test_evaluation_index_chains_first_day_pnl_across_windows() -> None:
    equity = pd.DataFrame(
        [
            # Deliberately out of order, with a stress row that must be ignored.
            {"candidate_id": "A01__primary", "phase": "evaluation", "cost_scenario": "base", "window_id": "W2", "date": "2024-03-20", "equity": 990.0},
            {"candidate_id": "A01__primary", "phase": "evaluation", "cost_scenario": "stress", "window_id": "W1", "date": "2024-01-02", "equity": 800.0},
            {"candidate_id": "A01__primary", "phase": "evaluation", "cost_scenario": "base", "window_id": "W1", "date": "2024-01-02", "equity": 1010.0},
            {"candidate_id": "A01__primary", "phase": "evaluation", "cost_scenario": "base", "window_id": "W2", "date": "2024-03-21", "equity": 1000.0},
            {"candidate_id": "A01__primary", "phase": "evaluation", "cost_scenario": "base", "window_id": "W1", "date": "2024-01-03", "equity": 1020.0},
        ]
    )

    values = _evaluation_index_values(equity, "A01__primary", ("W1", "W2"))

    # W1 ends at 1,020.  W2's first day is a -1% return from its own $1,000
    # start, so the chained index is 1,009.80 rather than restarting at 990.
    assert values == pytest.approx([1010.0, 1020.0, 1009.8, 1020.0])


def test_cost_comparison_reads_all_available_cost_scenarios() -> None:
    leaderboard = pd.DataFrame(
        [
            {"candidate_id": "A01__primary", "phase": "continuous", "cost_scenario": "base", "net_return": 0.30},
            {"candidate_id": "A01__primary", "phase": "continuous", "cost_scenario": "stress", "net_return": 0.25},
            {"candidate_id": "A01__primary", "phase": "continuous", "cost_scenario": "severe", "net_return": 0.18},
            {"candidate_id": "A02__primary", "phase": "continuous", "cost_scenario": "base", "net_return": 0.20},
            {"candidate_id": "A02__primary", "phase": "continuous", "cost_scenario": "stress", "net_return": 0.10},
            {"candidate_id": "A02__primary", "phase": "continuous", "cost_scenario": "severe", "net_return": -0.02},
            # Base-only ranking data must not cause stress/severe values to be
            # fabricated as zero in the chart.
            {"candidate_id": "A01__primary", "phase": "evaluation", "cost_scenario": "base", "net_return": 0.01},
        ]
    )

    labels, values = _cost_comparison_data(leaderboard, ["A01__primary", "A02__primary"])

    assert labels == [
        "A01__primary base",
        "A01__primary stress",
        "A01__primary severe",
        "A02__primary base",
        "A02__primary stress",
        "A02__primary severe",
    ]
    assert values == pytest.approx([0.30, 0.25, 0.18, 0.20, 0.10, -0.02])


def test_cost_comparison_omits_missing_scenario_instead_of_zero_filling() -> None:
    leaderboard = pd.DataFrame(
        [
            {"candidate_id": "A01__primary", "phase": "continuous", "cost_scenario": "base", "net_return": 0.30},
            {"candidate_id": "A01__primary", "phase": "continuous", "cost_scenario": "stress", "net_return": 0.25},
        ]
    )

    labels, values = _cost_comparison_data(leaderboard, ["A01__primary"])

    assert labels == ["A01__primary base", "A01__primary stress"]
    assert values == pytest.approx([0.30, 0.25])


def test_report_cost_chart_uses_full_continuous_cost_table(tmp_path, monkeypatch) -> None:
    study_dir = tmp_path / "study"
    (study_dir / "normalized").mkdir(parents=True)
    leaderboard = pd.DataFrame(
        [
            {"candidate_id": "A01__primary", "track_id": "a", "pair_id": "QQQM_SMH", "phase": "continuous", "window_id": "", "cost_scenario": "base", "starting_equity": 1000.0, "ending_equity": 1300.0, "net_pnl": 300.0, "net_return": 0.30, "max_drawdown": -0.05, "cagr": 0.10, "sharpe_zero_rf": 1.0, "turnover": 100.0},
            {"candidate_id": "A01__primary", "track_id": "a", "pair_id": "QQQM_SMH", "phase": "continuous", "window_id": "", "cost_scenario": "stress", "starting_equity": 1000.0, "ending_equity": 1250.0, "net_pnl": 250.0, "net_return": 0.25, "max_drawdown": -0.06, "cagr": 0.08, "sharpe_zero_rf": 0.9, "turnover": 100.0},
            {"candidate_id": "A01__primary", "track_id": "a", "pair_id": "QQQM_SMH", "phase": "continuous", "window_id": "", "cost_scenario": "severe", "starting_equity": 1000.0, "ending_equity": 1180.0, "net_pnl": 180.0, "net_return": 0.18, "max_drawdown": -0.08, "cagr": 0.06, "sharpe_zero_rf": 0.7, "turnover": 100.0},
        ]
    )
    leaderboard.to_csv(study_dir / "leaderboard.csv", index=False)
    (study_dir / "correction_audit.json").write_text(
        '{"status":"PROVISIONAL","blockers":["legacy migration differs"]}',
        encoding="utf-8",
    )
    equity = pd.DataFrame(
        [
            {"candidate_id": "A01__primary", "phase": "continuous", "cost_scenario": "base", "window_id": "", "date": "2024-01-02", "equity": 1000.0},
            {"candidate_id": "A01__primary", "phase": "continuous", "cost_scenario": "base", "window_id": "", "date": "2024-01-03", "equity": 1300.0},
        ]
    )
    equity.to_parquet(study_dir / "normalized" / "equity_daily.parquet", index=False)
    calls: list[tuple[str, list[str], list[float]]] = []

    def capture_bars(path, title, labels, values, *, y_label=""):
        if path.name == "cost_comparison.svg":
            calls.append((title, labels, values))

    monkeypatch.setattr(reporting_v2, "_svg_bars", capture_bars)
    report_path = build_v2_report(study_dir, tmp_path / "report.html")

    assert len(calls) == 1
    assert calls[0][1] == [
        "A01__primary base",
        "A01__primary stress",
        "A01__primary severe",
    ]
    assert calls[0][2] == pytest.approx([0.30, 0.25, 0.18])
    report_text = report_path.read_text(encoding="utf-8")
    assert "Correction audit status: PROVISIONAL" in report_text
    assert "legacy migration differs" in report_text
    assert "does not certify full compliance" in report_text
