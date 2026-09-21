from __future__ import annotations

import pandas as pd
import pytest

from packages.etf_cash_research.engine_v3 import (
    EngineConfig,
    OrderIntent,
    SizingMode,
    run_engine_v3,
)
from packages.etf_cash_research.independent_reconstruction_v3 import (
    compare_saved_equity,
    reconstruct_daily_equity_v3,
)


def _dates() -> pd.DatetimeIndex:
    return pd.date_range("2024-05-28", periods=4, freq="B", tz="UTC")


def _bars(dates: pd.DatetimeIndex, closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"date": date, "symbol": "TEST", "open": close, "high": close, "low": close, "close": close}
            for date, close in zip(dates, closes, strict=True)
        ]
    )


def _labels(frame: pd.DataFrame, *, candidate: str = "TEST__primary") -> pd.DataFrame:
    value = frame.copy()
    value["candidate_id"] = candidate
    value["phase"] = "continuous"
    value["cost_scenario"] = "base"
    return value


def test_reconstruction_uses_fills_cash_events_and_raw_marks_without_saved_equity() -> None:
    dates = _dates()
    fills = _labels(
        pd.DataFrame(
            [
                {"date": dates[0], "symbol": "TEST", "side": "buy", "quantity": 5.0, "price": 100.0, "fee": 0.0, "order_id": "buy-1"},
                {"date": dates[1], "symbol": "TEST", "side": "sell", "quantity": 5.0, "price": 110.0, "fee": 0.01, "settlement_date": dates[2], "order_id": "sell-1"},
            ]
        )
    )
    cash = _labels(
        pd.DataFrame(
            [
                {"date": dates[0], "kind": "buy", "amount": -500.0, "symbol": "TEST", "order_id": "buy-1"},
                {"date": dates[1], "kind": "sale_pending", "amount": 549.99, "symbol": "TEST", "settlement_date": dates[2], "order_id": "sell-1"},
                {"date": dates[2], "kind": "sale_settled", "amount": 549.99, "symbol": "TEST", "settlement_date": dates[2], "order_id": "sell-1"},
            ]
        )
    )

    result = reconstruct_daily_equity_v3(
        fills,
        cash,
        _bars(dates, [100.0, 110.0, 110.0, 110.0]),
        pd.DataFrame(columns=["symbol", "ex_date", "action_type", "rate", "payable_date"]),
        candidate_id="TEST__primary",
        valuation_dates=dates,
    )

    assert result.daily["equity_reconstructed"].tolist() == pytest.approx([1000.0, 1049.99, 1049.99, 1049.99])
    assert result.daily.loc[0, "holding_TEST_microshares"] == 5_000_000
    assert result.summary["fee_mismatch_count"] == 0
    assert result.summary["settlement_mismatch_count"] == 0
    assert result.summary["negative_holding_or_cash_events"] == 0


def test_reconstruction_applies_split_before_dividend_entitlement_and_excludes_ex_date_purchase() -> None:
    dates = _dates()
    actions = pd.DataFrame(
        [
            {"id": "split-1", "symbol": "TEST", "ex_date": dates[1], "action_type": "forward_split", "old_rate": 1.0, "new_rate": 2.0, "rate": None, "payable_date": pd.NaT},
            {"id": "div-1", "symbol": "TEST", "ex_date": dates[1], "action_type": "cash_dividend", "rate": 1.0, "payable_date": dates[2]},
        ]
    )
    fills = _labels(
        pd.DataFrame(
            [
                {"date": dates[0], "symbol": "TEST", "side": "buy", "quantity": 5.0, "price": 100.0},
                {"date": dates[1], "symbol": "TEST", "side": "buy", "quantity": 1.0, "price": 50.0},
            ]
        )
    )
    cash = _labels(
        pd.DataFrame(
            [
                {"date": dates[0], "kind": "buy", "amount": -500.0, "symbol": "TEST"},
                {"date": dates[1], "kind": "buy", "amount": -50.0, "symbol": "TEST"},
                {"date": dates[1], "kind": "dividend_receivable", "amount": 10.0, "symbol": "TEST", "payable_date": dates[2]},
                {"date": dates[2], "kind": "dividend_paid", "amount": 10.0, "symbol": "TEST"},
            ]
        )
    )

    result = reconstruct_daily_equity_v3(
        fills,
        cash,
        _bars(dates, [100.0, 50.0, 51.0, 52.0]),
        actions,
        candidate_id="TEST__primary",
        valuation_dates=dates,
    )

    check = result.dividend_checks.iloc[0]
    assert check["expected_quantity_microshares"] == 10_000_000
    assert check["expected_amount"] == pytest.approx(10.0)
    assert check["status"] == "MATCHED"
    # The ex-date purchase is owned after the entitlement check.
    assert result.daily.loc[1, "holding_TEST_microshares"] == 11_000_000
    assert result.summary["dividend_mismatch_count"] == 0


def test_reconstruction_requires_explicit_action_resolution_for_duplicate_distribution() -> None:
    dates = _dates()
    actions = pd.DataFrame(
        [
            {"id": "div-a", "symbol": "TEST", "ex_date": dates[0], "action_type": "cash_dividend", "rate": 1.0, "payable_date": dates[1]},
            {"id": "div-b", "symbol": "TEST", "ex_date": dates[0], "action_type": "cash_dividend", "rate": 1.0, "payable_date": dates[2]},
        ]
    )
    with pytest.raises(ValueError, match="MULTIPLE_DISTRIBUTIONS"):
        reconstruct_daily_equity_v3(
            _labels(pd.DataFrame(columns=["date", "symbol", "side", "quantity", "price"])),
            _labels(pd.DataFrame(columns=["date", "kind", "amount"])),
            _bars(dates, [100.0, 100.0, 100.0, 100.0]),
            actions,
            candidate_id="TEST__primary",
            valuation_dates=dates,
        )


def test_component_ownership_reconciles_in_exact_microshares() -> None:
    dates = _dates()
    fills = _labels(pd.DataFrame([{"date": dates[0], "symbol": "TEST", "side": "buy", "quantity": 1.5, "price": 100.0}]))
    cash = _labels(pd.DataFrame([{"date": dates[0], "kind": "buy", "amount": -150.0, "symbol": "TEST"}]))
    components = _labels(
        pd.DataFrame(
            [
                {"date": dates[0], "component_id": "trend", "symbol": "TEST", "quantity_microshares": 1_000_000},
                {"date": dates[0], "component_id": "pullback", "symbol": "TEST", "quantity_microshares": 500_000},
            ]
        )
    )
    result = reconstruct_daily_equity_v3(
        fills,
        cash,
        _bars(dates, [100.0, 100.0, 100.0, 100.0]),
        pd.DataFrame(columns=["symbol", "ex_date", "action_type", "rate", "payable_date"]),
        candidate_id="TEST__primary",
        valuation_dates=dates,
        component_ledger=components,
    )

    assert result.summary["component_mismatch_count"] == 0
    assert result.component_checks["status"].eq("MATCHED").all()


def test_compare_saved_equity_is_separate_and_ignores_daily_profit() -> None:
    dates = _dates()[:2]
    rebuilt = pd.DataFrame({"date": dates, "equity_reconstructed": [1000.0, 1001.0]})
    saved = pd.DataFrame({"date": dates, "equity": [1000.0, 1001.0], "daily_profit": [999.0, -999.0]})

    comparison = compare_saved_equity(rebuilt, saved)

    assert comparison["equity_within_tolerance"] is True
    assert comparison["daily_profit_used"] is False


def test_reconstruction_accepts_engine_v3_scoped_ledgers_without_saved_equity() -> None:
    dates = _dates()
    bars = _bars(dates, [100.0, 110.0, 120.0, 120.0])

    class BuyThenExit:
        def decide(self, context):
            if context.information_cutoff is None:
                return []
            if context.execution_session == dates[1]:
                return [OrderIntent("TEST", SizingMode.ENTER_SLEEVE, target_weight=0.5)]
            if context.execution_session == dates[2]:
                return [OrderIntent("TEST", SizingMode.EXIT_FULLY)]
            return []

    engine = run_engine_v3(
        bars,
        strategy=BuyThenExit(),
        config=EngineConfig(cost_basis_points=0.0, sell_fee=0.01),
        tradable_symbols=("TEST",),
        start=dates[0],
        end=dates[-1],
        candidate_id="TEST__primary",
    )
    actions = pd.DataFrame(columns=["symbol", "ex_date", "action_type", "rate", "payable_date"])

    result = reconstruct_daily_equity_v3(
        engine.fills,
        engine.cash_ledger,
        bars,
        actions,
        candidate_id="TEST__primary",
        valuation_dates=dates,
        component_ledger=engine.component_ledger,
    )

    comparison = compare_saved_equity(result.daily, engine.equity)
    assert comparison["equity_within_tolerance"] is True
    assert result.summary["fee_mismatch_count"] == 0
    assert result.summary["settlement_mismatch_count"] == 0
    assert result.summary["component_mismatch_count"] == 0


def test_reconstruction_checks_each_daily_component_sum_after_internal_transfer() -> None:
    dates = _dates()
    bars = _bars(dates, [100.0, 100.0, 100.0, 100.0])

    class RotateComponents:
        def __init__(self) -> None:
            self.step = 0

        def decide(self, context):
            if context.information_cutoff is None:
                return []
            self.step += 1
            if self.step == 1:
                return [OrderIntent("TEST", SizingMode.ENTER_SLEEVE, component_id="a", target_weight=0.4)]
            if self.step == 2:
                return [
                    OrderIntent("TEST", SizingMode.EXIT_FULLY, component_id="a"),
                    OrderIntent("TEST", SizingMode.ENTER_SLEEVE, component_id="b", target_weight=0.4),
                ]
            return []

    engine = run_engine_v3(
        bars,
        strategy=RotateComponents(),
        config=EngineConfig(cost_basis_points=0.0),
        tradable_symbols=("TEST",),
        start=dates[0],
        end=dates[-1],
        candidate_id="TEST__rotation",
    )
    result = reconstruct_daily_equity_v3(
        engine.fills,
        engine.cash_ledger,
        bars,
        pd.DataFrame(columns=["symbol", "ex_date", "action_type", "rate", "payable_date"]),
        candidate_id="TEST__rotation",
        valuation_dates=dates,
        component_ledger=engine.component_ledger,
    )

    assert len(result.component_checks) == len(dates)
    assert result.component_checks["status"].eq("MATCHED").all()
    assert result.component_checks["difference_microshares"].eq(0).all()


def test_component_snapshots_cannot_overwrite_independently_rebuilt_ownership():
    dates = _dates()
    bars = _bars(dates,[100.,100.,100.,100.])
    class Entry:
        def decide(self,context):
            if context.execution_session==dates[1]:
                return [OrderIntent('TEST',SizingMode.ENTER_SLEEVE,component_id='trend',target_weight=.4)]
            return []
    engine = run_engine_v3(bars,strategy=Entry(),config=EngineConfig(cost_basis_points=0),tradable_symbols=('TEST',),start=dates[0],end=dates[-1],candidate_id='TEST__primary')
    corrupted = engine.component_ledger.copy()
    mask = corrupted['kind'].eq('component_position') & corrupted['component_id'].eq('trend') & corrupted['quantity_microshares'].gt(0)
    corrupted.loc[mask,'quantity_microshares'] += 1
    actions = pd.DataFrame(columns=['symbol','ex_date','action_type','rate','payable_date'])
    result = reconstruct_daily_equity_v3(engine.fills,engine.cash_ledger,bars,actions,candidate_id='TEST__primary',valuation_dates=dates,component_ledger=corrupted)
    assert result.summary['component_mismatch_count']>0


def test_all_cash_account_reconstructs_empty_ledgers():
    dates = _dates()
    bars = _bars(dates,[100.,100.,100.,100.])
    class Cash:
        def decide(self,context):
            return []
    engine = run_engine_v3(bars,strategy=Cash(),tradable_symbols=('TEST',),start=dates[0],end=dates[-1],candidate_id='TEST__primary')
    actions = pd.DataFrame(columns=['symbol','ex_date','action_type','rate','payable_date'])
    result = reconstruct_daily_equity_v3(engine.fills,engine.cash_ledger,bars,actions,candidate_id='TEST__primary',valuation_dates=dates,component_ledger=engine.component_ledger)
    assert compare_saved_equity(result.daily,engine.equity)['equity_within_tolerance']
    assert result.daily.equity_reconstructed.eq(1000).all()
