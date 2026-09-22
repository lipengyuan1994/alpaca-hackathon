from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from packages.contracts.canonical import canonical_hash
from packages.etf_live.broker import AccountSnapshot, Quote
from packages.etf_live.config import load_config
from packages.etf_live.policy import PolicyContext
from packages.etf_live.runtime import LiveRuntime
from packages.etf_live.state import LiveState


class T08Broker:
    def __init__(self) -> None:
        self.submissions: list[dict[str, object]] = []
        self.cancellations: list[str] = []
        self.bars: list[dict[str, str]] = []
        self.cash_activities: list[dict[str, object]] = []
        self.settlement_dates: dict[str, str] = {}
        start = datetime(2025, 10, 1, tzinfo=UTC)
        for offset in range(360):
            stamp = start + timedelta(days=offset)
            if stamp.weekday() < 5:
                self.bars.append({"t": stamp.isoformat().replace("+00:00", "Z"), "o": "100", "h": "101", "l": "99", "c": str(100 + offset / 10), "v": "1000"})

    def account(self) -> AccountSnapshot:
        return AccountSnapshot("pending", "ACTIVE", Decimal("2000"), Decimal("2000"), Decimal("2000"), Decimal("2000"), Decimal("1"), False, False, False)

    def positions(self) -> list[dict[str, str]]:
        return []

    def open_orders(self) -> list[dict[str, str]]:
        return []

    def asset(self, symbol: str) -> dict[str, object]:
        return {"symbol": symbol, "tradable": True, "fractionable": True}

    def daily_bars(self, symbols: tuple[str, ...], **_: object) -> dict[str, list[dict[str, str]]]:
        return {symbol: list(self.bars) for symbol in symbols}

    def corporate_actions(self, symbols: tuple[str, ...], **_: object) -> list[dict[str, object]]:
        return []

    def activities(self, *, after=None) -> list[dict[str, object]]:
        return list(self.cash_activities)

    def settlement_calendar(self, *, start, end) -> list[dict[str, object]]:
        return [{"date": start.isoformat(), "settlement_date": self.settlement_dates.get(start.isoformat(), start.isoformat())}]

    def order_by_id(self, *_: object) -> None:
        return None

    def order_by_client_id(self, *_: object) -> None:
        return None

    def clock(self) -> dict[str, object]:
        return {"is_open": True, "next_open": "2026-09-21T13:30:00Z"}

    def latest_quotes(self, symbols: tuple[str, ...]) -> dict[str, Quote]:
        now = datetime(2026, 9, 21, 13, 31, tzinfo=UTC)
        return {symbol: Quote(symbol, Decimal("100"), Decimal("100.10"), now) for symbol in symbols}

    def submit_order(self, payload: dict[str, object]) -> dict[str, object]:
        self.submissions.append(payload)
        return {"id": f"broker-{len(self.submissions)}", "status": "accepted", **payload}

    def cancel_order(self, broker_order_id: str) -> None:
        self.cancellations.append(broker_order_id)



def test_t08_premarket_then_open_submits_only_tecl(tmp_path: Path) -> None:
    enabled = tmp_path / "enabled"
    enabled.write_text("1\n", encoding="utf-8")
    config = load_config(Path("configs/live/t08_tecl.yaml")).model_copy(update={"state_path": tmp_path / "state.db", "enabled_file": enabled, "mode": "live"})
    broker = T08Broker()
    state = LiveState(config.state_path)
    state.record_cash_event(
        event_id="funding-2000",
        category="cleared_funding",
        activity_type="CSD",
        amount="2000",
        effective_date="2026-09-21",
        spendable_date="2026-09-21",
        payload={"fixture": True},
    )
    state.initialize_cash_ledger(evidence_id="funding-2000", account_id_hash=canonical_hash({"account_id": config.account_id}), as_of="2026-09-21")
    state.bind_config(config.config_hash)
    state.set_activation(account_id_hash=canonical_hash({"account_id": config.account_id}), config_hash=config.config_hash, operator_reason="fixture T08 activation", at=datetime(2026, 9, 21, 13, 0, tzinfo=UTC))
    runtime = LiveRuntime(config=config, broker=broker, state=state)
    premarket = runtime.run_once(now=datetime(2026, 9, 21, 13, 20, tzinfo=UTC))
    assert premarket["status"] == "PREMARKET_DECISION_RECORDED"
    assert broker.submissions == []
    opened = runtime.run_once(now=datetime(2026, 9, 21, 13, 31, tzinfo=UTC))
    assert opened["status"] == "READY"
    assert broker.submissions
    assert {str(item["symbol"]) for item in broker.submissions} == {"TECL"}
    assert all(str(item["client_order_id"]).startswith("t08-") for item in broker.submissions)


def test_t08_observe_mode_never_submits(tmp_path: Path) -> None:
    config = load_config(Path("configs/live/t08_tecl.yaml")).model_copy(update={"state_path": tmp_path / "state.db"})
    broker = T08Broker()
    runtime = LiveRuntime(config=config, broker=broker, state=LiveState(config.state_path))
    runtime.run_once(now=datetime(2026, 9, 21, 13, 20, tzinfo=UTC))
    result = runtime.run_once(now=datetime(2026, 9, 21, 13, 31, tzinfo=UTC))
    assert result["status"] == "OBSERVE_ONLY"
    assert broker.submissions == []


def test_t08_partial_terminal_buy_accepts_allocation_state_without_daily_top_up(tmp_path: Path) -> None:
    config = load_config(Path("configs/live/t08_tecl.yaml")).model_copy(update={"state_path": tmp_path / "state.db"})
    broker = T08Broker()
    state = LiveState(config.state_path)
    runtime = LiveRuntime(config=config, broker=broker, state=state)
    state.save_order({
        "client_order_id": "t08-decision-tecl-buy",
        "decision_id": "decision",
        "symbol": "TECL",
        "side": "buy",
        "order_type": "limit",
        "requested_qty": "9.9",
        "limit_price": "100",
        "status": "canceled",
        "broker_order_id": "broker-1",
        "reserved_cash": "990",
        "updated_at": "2026-09-21T14:00:00+00:00",
        "target_weight": "0.99",
        "target_quantity": "9.9",
        "reason": "T08_ALLOCATION_INCREASE",
    })
    state.save_fill({
        "fill_id": "activity-partial-1",
        "client_order_id": "t08-decision-tecl-buy",
        "symbol": "TECL",
        "side": "buy",
        "quantity": "4.0",
        "price": "100",
        "notional": "400",
        "occurred_at": "2026-09-21T14:01:00+00:00",
    })
    runtime._sync_t08_allocation_feedback()
    policy_state = runtime._policy_state()
    assert policy_state.allocation_target_weight == Decimal("0.99")

    context = PolicyContext(
        execution_session="2026-09-22",
        information_cutoff="2026-09-21",
        features={"XLK": {"close": "110", "sma200": "100", "asof": "2026-09-21", "asymmetry": "1", "vol_ratio": "1"}},
        positions={"TECL": Decimal("4")},
        prior_close_equity=Decimal("1000"),
        prior_close={"TECL": Decimal("100")},
        state=policy_state,
    )
    assert runtime.policy.decide(context).intents == ()


def test_t08_day_buy_remainder_is_cancel_requested_at_cutoff(tmp_path: Path) -> None:
    config = load_config(Path("configs/live/t08_tecl.yaml")).model_copy(update={"state_path": tmp_path / "state.db"})
    broker = T08Broker()
    state = LiveState(config.state_path)
    runtime = LiveRuntime(config=config, broker=broker, state=state)
    state.save_order({
        "client_order_id": "t08-decision-tecl-buy",
        "decision_id": "decision",
        "symbol": "TECL",
        "side": "buy",
        "order_type": "limit",
        "requested_qty": "9.9",
        "limit_price": "100",
        "status": "accepted",
        "broker_order_id": "broker-1",
        "reserved_cash": "990",
        "updated_at": "2026-09-21T14:00:00+00:00",
        "target_weight": "0.99",
    })
    now = datetime(2026, 9, 21, 13, 36, tzinfo=UTC)
    blocked = runtime._cancel_t08_buy_remainders(now=now)
    assert blocked == {"TECL"}
    assert broker.cancellations == ["broker-1"]
    assert state.order("t08-decision-tecl-buy")["status"] == "cancel_pending"


def test_t08_cash_bootstrap_requires_broker_activity_and_settled_funding(tmp_path: Path) -> None:
    config = load_config(Path("configs/live/t08_tecl.yaml")).model_copy(update={"state_path": tmp_path / "state.db", "mode": "live"})
    broker = T08Broker()
    broker.cash_activities = [{
        "id": "funding-1",
        "activity_type": "CSD",
        "date": "2026-09-21",
        "transaction_time": "2026-09-21T12:00:00Z",
        "net_amount": "2000",
    }]
    state = LiveState(config.state_path)
    runtime = LiveRuntime(config=config, broker=broker, state=state)
    now = datetime(2026, 9, 21, 13, 0, tzinfo=UTC)
    runtime._reconcile_account_cash_activities(now=now)
    runtime._maybe_initialize_t08_cash_ledger(now=now)
    ledger = state.cash_ledger_snapshot(as_of="2026-09-21")
    assert state.get_meta("cash_ledger_initialized") == "1"
    assert ledger["cleared_funding"] == "2000"


def test_unexplained_activity_blocks_funding_initialization(tmp_path: Path) -> None:
    config = load_config(Path("configs/live/t08_tecl.yaml")).model_copy(update={"state_path": tmp_path / "state.db", "mode": "live"})
    broker = T08Broker()
    broker.cash_activities = [
        {"id": "funding-1", "activity_type": "CSD", "date": "2026-09-21", "transaction_time": "2026-09-21T12:00:00Z", "net_amount": "2000"},
        {"id": "misc-1", "activity_type": "MISC", "date": "2026-09-21", "transaction_time": "2026-09-21T12:01:00Z", "net_amount": "0"},
    ]
    state = LiveState(config.state_path)
    runtime = LiveRuntime(config=config, broker=broker, state=state)
    now = datetime(2026, 9, 21, 13, 0, tzinfo=UTC)
    runtime._reconcile_account_cash_activities(now=now)
    runtime._maybe_initialize_t08_cash_ledger(now=now)
    assert state.get_meta("cash_ledger_initialized") is None
    assert "UNRESOLVED_ACCOUNT_ACTIVITY" in state.cash_ledger_snapshot(as_of="2026-09-21")["blockers"]


def test_sell_fill_cash_is_unavailable_until_explicit_calendar_settlement(tmp_path: Path) -> None:
    config = load_config(Path("configs/live/t08_tecl.yaml")).model_copy(update={"state_path": tmp_path / "state.db"})
    broker = T08Broker()
    broker.settlement_dates["2026-09-21"] = "2026-09-22"
    runtime = LiveRuntime(config=config, broker=broker, state=LiveState(config.state_path))
    runtime._record_fill_cash_movement({
        "fill_id": "sell-fill-1",
        "broker_order_id": "broker-1",
        "symbol": "TECL",
        "side": "sell",
        "quantity": "2",
        "price": "100",
        "notional": "200",
        "occurred_at": "2026-09-21T15:00:00Z",
    })
    trade_day = runtime.state.cash_ledger_snapshot(as_of="2026-09-21")
    settlement_day = runtime.state.cash_ledger_snapshot(as_of="2026-09-22")
    assert trade_day["settled_cash"] == "0"
    assert trade_day["unsettled_sale_proceeds"] == "200"
    assert settlement_day["settled_cash"] == "200"


def test_distribution_entitlement_uses_pre_ex_date_fills_and_verified_payable_date(tmp_path: Path) -> None:
    config = load_config(Path("configs/live/t08_tecl.yaml")).model_copy(update={"state_path": tmp_path / "state.db"})
    state = LiveState(config.state_path)
    broker = T08Broker()
    runtime = LiveRuntime(config=config, broker=broker, state=state)
    state.save_order({"client_order_id": "buy-before", "decision_id": "d1", "symbol": "TECL", "side": "buy", "order_type": "limit", "requested_qty": "2", "status": "filled", "updated_at": "2026-09-21T14:00:00Z"})
    state.save_order({"client_order_id": "buy-ex", "decision_id": "d2", "symbol": "TECL", "side": "buy", "order_type": "limit", "requested_qty": "3", "status": "filled", "updated_at": "2026-09-22T14:00:00Z"})
    for fill_id, order_id, qty, stamp in (
        ("fill-before", "buy-before", "2", "2026-09-21T14:00:00Z"),
        ("fill-ex", "buy-ex", "3", "2026-09-22T14:00:00Z"),
    ):
        state.save_fill({"fill_id": fill_id, "client_order_id": order_id, "symbol": "TECL", "side": "buy", "quantity": qty, "price": "100", "notional": str(Decimal(qty) * 100), "occurred_at": stamp})
    runtime._record_tecl_distribution_entitlements([{
        "id": "distribution-1",
        "symbol": "TECL",
        "type": "cash_dividend",
        "ex_date": "2026-09-22",
        "payable_date": "2026-09-30",
        "cash_amount": "2",
    }], through_date=date(2026, 9, 22))
    entitlement = state.distribution_entitlements()[0]
    assert entitlement["amount"] == "4"
    assert entitlement["payable_date"] == "2026-09-30"
    assert state.cash_ledger_snapshot(as_of="2026-09-22")["distribution_receivables"] == "4"


def test_distribution_without_payable_date_blocks_only_purchases(tmp_path: Path) -> None:
    config = load_config(Path("configs/live/t08_tecl.yaml")).model_copy(update={"state_path": tmp_path / "state.db"})
    state = LiveState(config.state_path)
    broker = T08Broker()
    runtime = LiveRuntime(config=config, broker=broker, state=state)
    state.save_order({"client_order_id": "buy-before", "decision_id": "d1", "symbol": "TECL", "side": "buy", "order_type": "limit", "requested_qty": "2", "status": "filled", "updated_at": "2026-09-21T14:00:00Z"})
    state.save_fill({"fill_id": "fill-before", "client_order_id": "buy-before", "symbol": "TECL", "side": "buy", "quantity": "2", "price": "100", "notional": "200", "occurred_at": "2026-09-21T14:00:00Z"})
    runtime._record_tecl_distribution_entitlements([{"id": "distribution-missing", "symbol": "TECL", "type": "cash_dividend", "ex_date": "2026-09-22", "cash_amount": "2"}], through_date=date(2026, 9, 22))
    assert "DISTRIBUTION_INPUTS_UNRESOLVED" in state.cash_ledger_snapshot(as_of="2026-09-22")["blockers"]
    context = PolicyContext(
        execution_session="2026-09-23",
        information_cutoff="2026-09-22",
        features={"XLK": {"close": "90", "sma200": "100", "asof": "2026-09-22"}},
        positions={"TECL": Decimal("2")},
        prior_close_equity=Decimal("1000"),
        prior_close={"TECL": Decimal("100")},
        purchase_allowed=False,
    )
    assert [intent.side for intent in runtime.policy.decide(context).intents] == ["sell"]
