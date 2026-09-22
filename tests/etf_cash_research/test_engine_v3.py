from __future__ import annotations

import pandas as pd
import pytest

from packages.etf_cash_research.engine_v3 import (
    DecisionContext,
    EngineConfig,
    OrderIntent,
    SizingMode,
    reconstruct_account_hash,
    run_engine_v3,
)


def _bars(
    dates: list[str],
    opens: list[float],
    closes: list[float] | None = None,
    *,
    symbol: str = "QQQM",
    splits: list[float] | None = None,
    dividends: list[float] | None = None,
    payable: list[str | None] | None = None,
) -> pd.DataFrame:
    closes = closes or opens
    splits = splits or [1.0] * len(dates)
    dividends = dividends or [0.0] * len(dates)
    payable = payable or [None] * len(dates)
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates, utc=True),
            "symbol": symbol,
            "open": opens,
            "high": [value * 1.01 for value in opens],
            "low": [value * 0.99 for value in opens],
            "close": closes,
            "split_factor": splits,
            "dividend": dividends,
            "dividend_payable_date": payable,
        }
    )


def _calendar(dates: list[str], settlements: list[str | None] | None = None) -> pd.DataFrame:
    settlements = settlements or [None] * len(dates)
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates, utc=True),
            "settlement_date": pd.to_datetime(settlements, utc=True),
        }
    )


class _EntryOnce:
    def __init__(self, weight: float = 0.50) -> None:
        self.weight = weight
        self.called: list[DecisionContext] = []
        self.entered = False

    def decide(self, context: DecisionContext):
        self.called.append(context)
        if context.information_cutoff is not None and not self.entered:
            self.entered = True
            return [OrderIntent("QQQM", SizingMode.ENTER_SLEEVE, target_weight=self.weight)]
        return []


def test_contract_context_is_point_in_time_and_uses_execution_calendar() -> None:
    dates = ["2024-01-02", "2024-01-03", "2024-01-08", "2024-01-09"]
    strategy = _EntryOnce()
    result = run_engine_v3(
        _bars(dates, [100, 100, 100, 100]),
        strategy=strategy,
        start="2024-01-03",
        calendar=_calendar(dates),
    )
    assert strategy.called
    assert all(context.execution_session not in context.history["QQQM"]["date"].tolist() for context in strategy.called if context.information_cutoff is not None)
    assert strategy.called[1].review_weekly is True  # Monday review uses the execution session.
    assert "QQQM" in result.final_positions
    assert result.account_hash == reconstruct_account_hash(result)


def test_target_quantity_is_sized_from_prior_close_not_current_open() -> None:
    dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
    strategy = _EntryOnce(0.50)
    result = run_engine_v3(
        _bars(dates, [100, 200, 200], closes=[100, 200, 200]),
        strategy=strategy,
        start="2024-01-03",
        calendar=_calendar(dates),
    )
    created = result.orders[result.orders["status"] == "scheduled"]
    assert not created.empty
    # $500 / $100 prior close = five requested shares.  The $200 open can
    # cause a cash-limited partial fill, but cannot rewrite that request.
    assert created.iloc[0]["quantity"] == pytest.approx(5.0)
    assert result.fills.iloc[0]["quantity"] == pytest.approx(4.997501, abs=1e-6)


class _DelayedEntry:
    def __init__(self) -> None:
        self.sent = False

    def decide(self, context: DecisionContext):
        if context.information_cutoff is not None and not self.sent:
            self.sent = True
            return [OrderIntent("QQQM", SizingMode.ENTER_SLEEVE, target_weight=0.50)]
        return []


def test_delayed_order_keeps_original_quantity_and_cannot_use_future_price() -> None:
    dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    result = run_engine_v3(
        _bars(dates, [100, 100, 200, 400], closes=[100, 100, 200, 400]),
        strategy=_DelayedEntry(),
        config=EngineConfig(execution_delay_sessions=1),
        start="2024-01-03",
        calendar=_calendar(dates),
    )
    scheduled = result.orders[result.orders["status"] == "scheduled"]
    assert not scheduled.empty
    assert scheduled.iloc[0]["quantity"] == pytest.approx(5.0)
    assert result.fills.iloc[0]["date"].startswith("2024-01-04")
    assert result.fills.iloc[0]["quantity"] == pytest.approx(4.997501, abs=1e-6)


class _SplitAndExit:
    def __init__(self) -> None:
        self.did_entry = False
        self.did_exit = False

    def decide(self, context: DecisionContext):
        if context.information_cutoff is None or self.did_exit:
            return []
        if not self.did_entry:
            self.did_entry = True
            return [OrderIntent("QQQM", SizingMode.ENTER_SLEEVE, target_weight=0.50)]
        if context.execution_session == pd.Timestamp("2024-05-30", tz="UTC"):
            self.did_exit = True
            return [OrderIntent("QQQM", SizingMode.EXIT_FULLY)]
        return []


def test_split_dividend_and_explicit_settlement_calendar() -> None:
    dates = ["2024-05-24", "2024-05-28", "2024-05-29", "2024-05-30", "2024-05-31"]
    bars = _bars(
        dates,
        [100, 100, 50, 51, 52],
        closes=[100, 100, 50, 51, 52],
        splits=[1, 1, 2, 1, 1],
        dividends=[0, 0, 1, 0, 0],
        payable=[None, None, "2024-05-29", None, None],
    )
    settlement = _calendar(dates, [None, "2024-05-29", "2024-05-30", "2024-05-31", None])
    result = run_engine_v3(bars, strategy=_SplitAndExit(), calendar=settlement)
    split_rows = result.cash_ledger[result.cash_ledger["kind"] == "split"]
    assert len(split_rows) == 1
    dividend = result.cash_ledger[result.cash_ledger["kind"] == "dividend_paid"]
    assert len(dividend) == 1
    assert dividend.iloc[0]["amount"] > 0
    assert result.fills[result.fills["side"] == "sell"].iloc[0]["settlement_date"].startswith("2024-05-31")
    assert result.trades.iloc[0]["holding_sessions"] >= 2


class _ComponentRotation:
    def __init__(self) -> None:
        self.step = 0

    def decide(self, context: DecisionContext):
        if context.information_cutoff is None:
            return []
        self.step += 1
        if self.step == 1:
            return [OrderIntent("QQQM", SizingMode.ENTER_SLEEVE, component_id="a", target_weight=0.40)]
        if self.step == 2:
            return [
                OrderIntent("QQQM", SizingMode.EXIT_FULLY, component_id="a"),
                OrderIntent("QQQM", SizingMode.ENTER_SLEEVE, component_id="b", target_weight=0.40),
            ]
        return []


def test_component_rotation_is_internal_and_has_no_external_fill() -> None:
    dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    result = run_engine_v3(
        _bars(dates, [100, 100, 100, 100]),
        strategy=_ComponentRotation(),
        calendar=_calendar(dates),
    )
    transfers = result.component_ledger[result.component_ledger["kind"] == "internal_transfer"]
    assert len(transfers) == 1
    assert transfers.iloc[0]["from_component"] == "a"
    assert transfers.iloc[0]["to_component"] == "b"
    # The desired 40% target is a few microshares below the original fill
    # because the prior-close equity includes the entry cost.  The bulk of
    # ownership moves internally; only that residual is a real sale.
    assert result.fills[result.fills["side"] == "sell"]["quantity"].sum() < 0.001
    assert len(result.fills[result.fills["side"] == "buy"]) == 1


def test_partial_sale_is_not_reported_as_completed_round_trip() -> None:
    class PartialThenFull:
        def __init__(self):
            self.step = 0

        def decide(self, context: DecisionContext):
            if context.information_cutoff is None:
                return []
            self.step += 1
            if self.step == 1:
                return [OrderIntent("QQQM", SizingMode.ENTER_SLEEVE, target_quantity=10)]
            if self.step == 2:
                return [OrderIntent("QQQM", SizingMode.REDUCE, target_quantity=5)]
            if self.step == 3:
                return [OrderIntent("QQQM", SizingMode.EXIT_FULLY)]
            return []

    dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]
    result = run_engine_v3(_bars(dates, [100] * len(dates)), strategy=PartialThenFull(), calendar=_calendar(dates))
    assert len(result.trades) == 1
    assert result.trades.iloc[0]["quantity"] == pytest.approx(9.9, abs=1e-6)
    partials = result.component_ledger[result.component_ledger["kind"] == "realized_partial"]
    assert len(partials) == 2


def test_settled_cash_is_not_available_until_frozen_calendar_date() -> None:
    dates = ["2024-06-03", "2024-06-04", "2024-06-05", "2024-06-06", "2024-06-07"]

    class SellThenBuy:
        def __init__(self):
            self.step = 0

        def decide(self, context: DecisionContext):
            if context.information_cutoff is None:
                return []
            self.step += 1
            if self.step == 1:
                return [OrderIntent("QQQM", SizingMode.ENTER_SLEEVE, target_quantity=9)]
            if self.step == 2:
                return [OrderIntent("QQQM", SizingMode.EXIT_FULLY)]
            if self.step == 3:
                return [OrderIntent("QQQM", SizingMode.ENTER_SLEEVE, target_quantity=9)]
            return []

    settlement = _calendar(dates, [None, "2024-06-06", "2024-06-07", None, None])
    result = run_engine_v3(_bars(dates, [100] * len(dates)), strategy=SellThenBuy(), calendar=settlement)
    # The buy decision on June 5 cannot spend the June 4 sale proceeds.  The
    # release is visible exactly on the explicit June 6 settlement session.
    releases = result.cash_ledger[result.cash_ledger["kind"] == "sale_settled"]
    assert len(releases) == 1
    assert releases.iloc[0]["date"].startswith("2024-06-07")


def test_delayed_buy_is_cancelled_by_new_exit_and_does_not_fill() -> None:
    dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]

    class EnterThenExit:
        def __init__(self):
            self.step = 0

        def decide(self, context: DecisionContext):
            if context.information_cutoff is None:
                return []
            self.step += 1
            if self.step == 1:
                return [OrderIntent("QQQM", SizingMode.ENTER_SLEEVE, target_weight=0.50)]
            if self.step == 2:
                return [OrderIntent("QQQM", SizingMode.EXIT_FULLY)]
            return []

    result = run_engine_v3(
        _bars(dates, [100, 100, 100, 100]),
        strategy=EnterThenExit(),
        config=EngineConfig(execution_delay_sessions=1),
        calendar=_calendar(dates),
        start="2024-01-03",
    )
    assert result.fills.empty
    assert (result.orders["status"] == "cancelled").any()


def test_delayed_quantity_is_adjusted_only_by_intervening_split() -> None:
    dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]

    class EnterOnce:
        def __init__(self):
            self.sent = False

        def decide(self, context: DecisionContext):
            if context.information_cutoff is not None and not self.sent:
                self.sent = True
                return [OrderIntent("QQQM", SizingMode.ENTER_SLEEVE, target_quantity=5)]
            return []

    bars = _bars(dates, [100, 100, 50, 50], closes=[100, 100, 50, 50], splits=[1, 1, 2, 1])
    result = run_engine_v3(
        bars,
        strategy=EnterOnce(),
        config=EngineConfig(execution_delay_sessions=1),
        calendar=_calendar(dates),
        start="2024-01-03",
    )
    # Five frozen shares at the cutoff become ten shares after the 2-for-1
    # split before the delayed execution.
    assert result.orders[result.orders["status"] == "scheduled"].iloc[0]["quantity"] == pytest.approx(5.0)
    assert result.fills.iloc[0]["quantity"] == pytest.approx(10.0, abs=1e-6)


def test_global_target_weight_is_capped_at_99_percent() -> None:
    dates = ["2024-01-02", "2024-01-03", "2024-01-04"]

    class TwoSleeves:
        def decide(self, context: DecisionContext):
            if context.information_cutoff is None:
                return []
            return [
                OrderIntent("QQQM", SizingMode.ENTER_SLEEVE, component_id="a", target_weight=0.99),
                OrderIntent("QQQM", SizingMode.ENTER_SLEEVE, component_id="b", target_weight=0.99),
            ]

    result = run_engine_v3(_bars(dates, [100] * len(dates)), strategy=TwoSleeves(), calendar=_calendar(dates))
    # Both intents are normalized before sizing; the account cannot request
    # more than the configured 99% aggregate target.
    assert result.final_positions["QQQM"].quantity <= 9.9 + 1e-6


def test_aggregate_cap_preserves_existing_shares_when_prices_differ() -> None:
    dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    qqqm = _bars(dates, [100] * len(dates), symbol="QQQM")
    soxx = _bars(dates, [200] * len(dates), symbol="SOXX")
    bars = pd.concat([qqqm, soxx], ignore_index=True)

    class IncreaseAfterEntry:
        def __init__(self) -> None:
            self.step = 0

        def decide(self, context: DecisionContext):
            if context.information_cutoff is None:
                return []
            self.step += 1
            if self.step == 1:
                return [OrderIntent("QQQM", SizingMode.ENTER_SLEEVE, target_quantity=4)]
            if self.step == 2:
                return [
                    OrderIntent("QQQM", SizingMode.ENTER_SLEEVE, target_quantity=10),
                    OrderIntent("SOXX", SizingMode.ENTER_SLEEVE, target_quantity=10),
                ]
            return []

    result = run_engine_v3(
        bars,
        strategy=IncreaseAfterEntry(),
        calendar=_calendar(dates),
        tradable_symbols=("QQQM", "SOXX"),
        config=EngineConfig(cost_basis_points=0.0),
    )
    qqqm_shares = result.final_positions["QQQM"].quantity
    soxx_shares = result.final_positions["SOXX"].quantity
    # The existing four QQQM shares remain.  Only the explicitly requested
    # increases are scaled into the remaining $990 target room.
    assert qqqm_shares >= 4.0
    assert qqqm_shares * 100.0 + soxx_shares * 200.0 <= 990.0 + 1e-6


def test_feature_cache_is_cutoff_only() -> None:
    dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
    seen: list[dict] = []

    class FeatureReader:
        def decide(self, context: DecisionContext):
            seen.append(dict(context.features.get("QQQM", {})))
            return []

    config = EngineConfig(feature_cache={pd.Timestamp("2024-01-02", tz="UTC"): {"QQQM": {"r63": 1.0}}})
    run_engine_v3(_bars(dates, [100] * len(dates)), strategy=FeatureReader(), config=config, calendar=_calendar(dates))
    assert seen[0] == {}
    assert seen[1] == {"r63": 1.0}


def test_no_intent_decision_preserves_appreciated_fixed_shares() -> None:
    dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]

    class EnterThenNoIntent:
        def __init__(self) -> None:
            self.step = 0

        def decide(self, context: DecisionContext):
            if context.information_cutoff is None:
                return []
            self.step += 1
            if self.step == 1:
                return [OrderIntent("QQQM", SizingMode.ENTER_SLEEVE, target_quantity=5)]
            return []

    result = run_engine_v3(
        _bars(dates, [100, 100, 200, 250]),
        strategy=EnterThenNoIntent(),
        calendar=_calendar(dates),
    )
    assert result.fills[result.fills["side"] == "sell"].empty
    assert result.final_positions["QQQM"].quantity == pytest.approx(5.0, abs=1e-6)
    assert (result.signals["action"] == "NO_INTENT").any()


def test_same_symbol_component_orders_share_fee_and_minimum_notional() -> None:
    dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]

    class TwoComponents:
        def __init__(self) -> None:
            self.step = 0

        def decide(self, context: DecisionContext):
            if context.information_cutoff is None:
                return []
            self.step += 1
            if self.step == 1:
                return [
                    OrderIntent("QQQM", SizingMode.ENTER_SLEEVE, component_id="a", target_quantity=0.03),
                    OrderIntent("QQQM", SizingMode.ENTER_SLEEVE, component_id="b", target_quantity=0.03),
                ]
            if self.step == 2:
                return [
                    OrderIntent("QQQM", SizingMode.EXIT_FULLY, component_id="a"),
                    OrderIntent("QQQM", SizingMode.EXIT_FULLY, component_id="b"),
                ]
            return []

    result = run_engine_v3(_bars(dates, [100] * len(dates)), strategy=TwoComponents(), calendar=_calendar(dates))
    buys = result.fills[result.fills["side"] == "buy"]
    sells = result.fills[result.fills["side"] == "sell"]
    assert len(buys) == 2
    assert len(sells) == 2
    assert sells["fee"].sum() == pytest.approx(0.01, abs=1e-12)
    assert sells["external_order_id"].nunique() == 1
    assert buys["external_order_id"].nunique() == 1
    assert sells["notional"].max() < 5.0
    assert sells["notional"].sum() > 5.0


def test_tiny_full_exit_never_creates_negative_pending_proceeds() -> None:
    dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]

    class TinyEntryThenExit:
        def __init__(self) -> None:
            self.step = 0

        def decide(self, context: DecisionContext):
            if context.information_cutoff is None:
                return []
            self.step += 1
            if self.step == 1:
                return [OrderIntent("QQQM", SizingMode.ENTER_SLEEVE, target_quantity=0.005)]
            if self.step == 2:
                return [OrderIntent("QQQM", SizingMode.EXIT_FULLY)]
            return []

    result = run_engine_v3(
        _bars(dates, [1.0] * len(dates)),
        strategy=TinyEntryThenExit(),
        config=EngineConfig(minimum_order_notional=0.0, cost_basis_points=0.0, sell_fee=0.01),
        calendar=_calendar(dates),
    )
    sale_pending = result.cash_ledger[result.cash_ledger["kind"] == "sale_pending"]
    fee_cash = result.cash_ledger[result.cash_ledger["kind"] == "fee"]
    assert not sale_pending.empty
    assert (sale_pending["amount"] >= 0).all()
    assert fee_cash["amount"].sum() == pytest.approx(-0.005, abs=1e-12)
    assert (result.equity["cash_pending"] >= 0).all()
    assert result.fills.iloc[-1]["side"] == "sell"


def test_context_holding_age_counts_completed_sessions_only() -> None:
    dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]

    class AgeRecorder:
        def __init__(self) -> None:
            self.step = 0
            self.ages: list[int] = []

        def decide(self, context: DecisionContext):
            if context.information_cutoff is None:
                return []
            held = context.positions.get(("account", "QQQM"))
            if held is not None and held.quantity_microshares:
                self.ages.append(held.holding_sessions)
                return []
            self.step += 1
            if self.step == 1:
                return [OrderIntent("QQQM", SizingMode.ENTER_SLEEVE, target_quantity=5)]
            return []

    strategy = AgeRecorder()
    run_engine_v3(_bars(dates, [100] * len(dates)), strategy=strategy, calendar=_calendar(dates))
    assert strategy.ages[:3] == [1, 2, 3]
