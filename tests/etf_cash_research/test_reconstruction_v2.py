from __future__ import annotations

import pandas as pd
import pytest

from packages.etf_cash_research.reconstruction_v2 import (
    compare_saved_equity,
    reconstruct_daily_equity,
)


def _fixture_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    dates = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"], utc=True)
    raw = pd.DataFrame(
        [
            {"symbol": "TEST", "date": date, "open": close, "high": close + 1, "low": close - 1, "close": close}
            for date, close in zip(dates, (100.0, 51.0, 52.0), strict=True)
        ]
    )
    actions = pd.DataFrame(
        [
            {"symbol": "TEST", "ex_date": dates[1], "action_type": "forward_splits", "new_rate": 2.0, "old_rate": 1.0, "rate": None, "payable_date": None},
            {"symbol": "TEST", "ex_date": dates[1], "action_type": "cash_dividends", "new_rate": None, "old_rate": None, "rate": 1.0, "payable_date": dates[2]},
        ]
    )
    fills = pd.DataFrame(
        [{
            "candidate_id": "TEST__primary",
            "phase": "continuous",
            "cost_scenario": "base",
            "date": dates[0],
            "symbol": "TEST",
            "side": "buy",
            "quantity": 5.0,
        }]
    )
    ledger = pd.DataFrame(
        [
            {"candidate_id": "TEST__primary", "phase": "continuous", "cost_scenario": "base", "date": dates[0], "kind": "buy", "amount": -500.0, "symbol": "TEST", "payable_date": None},
            {"candidate_id": "TEST__primary", "phase": "continuous", "cost_scenario": "base", "date": dates[1], "kind": "dividend_receivable", "amount": 10.0, "symbol": "TEST", "payable_date": dates[2]},
            {"candidate_id": "TEST__primary", "phase": "continuous", "cost_scenario": "base", "date": dates[2], "kind": "dividend_paid", "amount": 10.0, "symbol": None, "payable_date": None},
        ]
    )
    saved = pd.DataFrame(
        [
            {"date": dates[0], "equity": 1000.0, "cash_settled": 500.0, "cash_pending": 0.0, "holdings_value": 500.0},
            {"date": dates[1], "equity": 1020.0, "cash_settled": 500.0, "cash_pending": 10.0, "holdings_value": 510.0},
            {"date": dates[2], "equity": 1030.0, "cash_settled": 510.0, "cash_pending": 0.0, "holdings_value": 520.0},
        ]
    )
    return fills, ledger, raw, actions, saved


def test_reconstruction_applies_split_before_fill_and_credits_dividend_once() -> None:
    fills, ledger, raw, actions, saved = _fixture_inputs()
    result = reconstruct_daily_equity(
        fills,
        ledger,
        raw,
        actions,
        candidate_id="TEST__primary",
        valuation_dates=saved["date"],
    )

    assert result.daily["equity_reconstructed"].tolist() == pytest.approx([1000.0, 1020.0, 1030.0])
    assert result.daily["holding_TEST"].tolist() == pytest.approx([5.0, 10.0, 10.0])
    assert result.summary["negative_holding_or_cash_events"] == 0
    assert result.summary["dividend_mismatch_count"] == 0
    assert result.dividend_checks.iloc[0]["status"] == "MATCHED"


def test_reconstruction_comparison_excludes_daily_profit_and_matches_saved_balances() -> None:
    fills, ledger, raw, actions, saved = _fixture_inputs()
    result = reconstruct_daily_equity(
        fills,
        ledger,
        raw,
        actions,
        candidate_id="TEST__primary",
        valuation_dates=saved["date"],
    )
    saved["daily_profit"] = [0.0, 20.0, 10.0]

    comparison = compare_saved_equity(result.daily, saved, tolerance=0.01)

    assert comparison["equity_within_tolerance"] is True
    assert comparison["max_abs_equity_difference"] == pytest.approx(0.0)
    assert comparison["daily_profit_used"] is False
    assert comparison["comparison_fields"] == ["equity", "cash_settled", "cash_pending", "holdings_value"]
