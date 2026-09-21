from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from packages.etf_cash_research.metrics import moving_block_bootstrap
from packages.etf_cash_research.news_gate import (
    NewsItem,
    NewsVetoAssessment,
    build_news_context,
    evaluate_news_gate,
    model_input_hash,
)
from packages.etf_cash_research.protocol import DEFAULT_PROTOCOL
from packages.etf_cash_research.simulator import _settlement_date, run_backtest


def _bars(periods: int = 430) -> pd.DataFrame:
    dates = pd.bdate_range("2022-01-03", periods=periods, tz="UTC")
    rows: list[dict[str, object]] = []
    for symbol, base, drift in (("QQQM", 100.0, 0.0004), ("SOXX", 150.0, 0.0006)):
        for index, date in enumerate(dates):
            close = base * (1.0 + drift * index)
            rows.append({"date": date, "symbol": symbol, "open": close, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": 1000})
    return pd.DataFrame(rows)


def test_settlement_is_business_day_and_switches_at_t1_date() -> None:
    assert _settlement_date(pd.Timestamp("2024-05-24", tz="UTC"), DEFAULT_PROTOCOL.settlement_change).date().isoformat() == "2024-05-28"
    assert _settlement_date(pd.Timestamp("2024-05-28", tz="UTC"), DEFAULT_PROTOCOL.settlement_change).date().isoformat() == "2024-05-29"


def test_signal_cannot_use_current_session_close_for_target_quantity() -> None:
    first = _bars()
    second = first.copy()
    target_date = second.loc[second["date"] == pd.Timestamp("2023-03-01", tz="UTC"), "date"].iloc[0]
    second.loc[(second["date"] == target_date) & (second["symbol"] == "SOXX"), "close"] *= 3.0
    second.loc[(second["date"] == target_date) & (second["symbol"] == "SOXX"), "high"] *= 3.0
    left = run_backtest(first, strategy_id="S02", semiconductor="SOXX", start="2023-03-01", end="2023-05-01")
    right = run_backtest(second, strategy_id="S02", semiconductor="SOXX", start="2023-03-01", end="2023-05-01")
    left_buy = left.fills[left.fills["side"] == "buy"].iloc[0]
    right_buy = right.fills[right.fills["side"] == "buy"].iloc[0]
    assert left_buy["quantity"] == right_buy["quantity"]


def test_segment_uses_prior_close_on_first_session_and_holds_sleeve_shares() -> None:
    result = run_backtest(_bars(), strategy_id="S02", semiconductor="SOXX", start="2023-03-01", end="2023-05-01")
    assert len(result.fills[result.fills["side"] == "buy"]) == 2
    assert set(result.fills["date"]) == {"2023-03-01T00:00:00+00:00"}


def test_execution_delay_moves_fill_without_moving_signal_cutoff() -> None:
    result = run_backtest(_bars(), strategy_id="S02", semiconductor="SOXX", start="2023-03-01", end="2023-05-01", execution_delay_sessions=1)
    assert set(result.fills["date"]) == {"2023-03-02T00:00:00+00:00"}
    assert result.signals.iloc[0]["information_cutoff"] == "2023-02-28T00:00:00+00:00"


def test_news_veto_preserves_exits_and_never_redirects_allocation() -> None:
    frozen = datetime(2026, 9, 19, 13, 15, tzinfo=timezone.utc)
    context = build_news_context(
        trading_date="2026-09-19",
        frozen_at=frozen,
        items=[NewsItem("n1", datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc), "headline", "source")],
        market_snapshot={"QQQM": {"close": 100}},
    )
    proposed = {"QQQM": 0.40, "SOXX": 0.50}
    assessment = NewsVetoAssessment("VETO", "MACRO_RISK", ("n1",), "bad", "gemini-test", "test", model_input_hash(context, proposed), None, frozen.replace(hour=14))
    decision = evaluate_news_gate(context=context, proposed_increases=proposed, assessment=assessment, now=frozen)
    assert decision["status"] == "VETO"
    assert decision["approved_increases"] == {}
    assert decision["approved_exits"] is True


def test_bootstrap_is_reproducible_and_paired() -> None:
    values = pd.Series([0.01, -0.005, 0.002, 0.01] * 10)
    result = moving_block_bootstrap(values, values * 0.5, samples=100, block_length=5, seed=135)
    assert result["samples"] == 100
    assert result["gap_quantiles"]["p50"] > 0
