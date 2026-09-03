from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from packages.contracts.canonical import canonical_hash
from packages.paper_wheel.broker import (
    PaperAccount,
    PaperClock,
    PaperOptionContract,
    PaperOptionLifecycleActivity,
    PaperOrder,
    PaperPosition,
    PaperQuote,
    PaperSubmissionUnknown,
)
from packages.paper_wheel.config import LoadedWheelConfig, WheelPaperConfig, load_config
from packages.paper_wheel.models import WheelAction, WheelOrderPlanV1
from packages.paper_wheel.runtime import PaperWheelRuntime

MONDAY = datetime(2026, 8, 31, 14, 0, 1, tzinfo=UTC)


class FakePaperBroker:
    expected_account_id = "paper-fixture-account"

    def __init__(self, *, now: datetime = MONDAY, submission: str = "filled") -> None:
        self.now = now
        self.submission = submission
        self.submit_count = 0
        self.submitted_plans: list[WheelOrderPlanV1] = []
        self.position_rows: list[PaperPosition] = []
        self.orders: dict[str, PaperOrder] = {}
        self.option_ask = Decimal("2.20")
        self.underlying_bid = Decimal("569.90")
        self.underlying_ask = Decimal("570.00")
        self.put_candidates = (
            PaperOptionContract(
                symbol="QQQ260911P00564000",
                underlying="QQQ",
                right="PUT",
                strike=Decimal("564"),
                expiration=date(2026, 9, 11),
                quote=PaperQuote(bid=Decimal("2.00"), ask=Decimal("2.20"), timestamp=self.now),
            ),
            PaperOptionContract(
                symbol="QQQ260911P00552000",
                underlying="QQQ",
                right="PUT",
                strike=Decimal("552"),
                expiration=date(2026, 9, 11),
                quote=PaperQuote(bid=Decimal("1.00"), ask=Decimal("1.10"), timestamp=self.now),
            ),
        )
        self.activity_rows: list[PaperOptionLifecycleActivity] = []
        self.cancel_count = 0
        self.account_row = PaperAccount(
            account_id=self.expected_account_id,
            equity=Decimal("100000"),
            day_start_equity=Decimal("100000"),
            cash=Decimal("100000"),
            buying_power=Decimal("100000"),
            options_buying_power=Decimal("100000"),
            options_trading_level=1,
            trading_blocked=False,
            account_blocked=False,
            trade_suspended_by_user=False,
        )

    def account(self) -> PaperAccount:
        return self.account_row

    def clock(self) -> PaperClock:
        return PaperClock(
            is_open=True,
            timestamp=self.now,
            next_open=self.now + timedelta(days=1),
            next_close=self.now + timedelta(hours=6),
        )

    def positions(self) -> tuple[PaperPosition, ...]:
        return tuple(self.position_rows)

    def open_orders(self) -> tuple[PaperOrder, ...]:
        return tuple(item for item in self.orders.values() if item.status in {"accepted", "new", "partially_filled"})

    def order_by_client_id(self, client_order_id: str) -> PaperOrder | None:
        return self.orders.get(client_order_id)

    def completed_daily_closes(self, symbol: str, *, now: datetime, sessions: int) -> tuple[Decimal, ...]:
        return tuple(Decimal(index) for index in range(521, 521 + sessions))

    def underlying_quote(self, symbol: str) -> PaperQuote:
        return PaperQuote(bid=self.underlying_bid, ask=self.underlying_ask, timestamp=self.now)

    def option_candidates(
        self,
        symbol: str,
        *,
        right: str,
        minimum_expiration: date,
        maximum_expiration: date,
        minimum_strike: Decimal,
        maximum_strike: Decimal,
    ) -> tuple[PaperOptionContract, ...]:
        expiration = date(2026, 9, 11)
        if right == "PUT":
            return tuple(
                replace(item, quote=replace(item.quote, timestamp=self.now))
                for item in self.put_candidates
            )
        return (
            PaperOptionContract(
                symbol="QQQ260911C00588000",
                underlying="QQQ",
                right="CALL",
                strike=Decimal("588"),
                expiration=expiration,
                quote=PaperQuote(bid=Decimal("1.80"), ask=Decimal("2.00"), timestamp=self.now),
            ),
        )

    def option_quote(self, symbol: str) -> PaperQuote:
        return PaperQuote(bid=max(Decimal("0"), self.option_ask - Decimal("0.10")), ask=self.option_ask, timestamp=self.now)

    def calendar_open_dates(self, start: date, end: date) -> tuple[date, ...]:
        return tuple(start + timedelta(days=index) for index in range(5))

    def option_lifecycle_activities(
        self,
        option_symbol: str,
        *,
        after: datetime,
    ) -> tuple[PaperOptionLifecycleActivity, ...]:
        del after
        return tuple(item for item in self.activity_rows if item.symbol == option_symbol)

    def cancel_order(self, broker_order_id: str) -> None:
        self.cancel_count += 1
        for client_order_id, order in tuple(self.orders.items()):
            if order.broker_order_id == broker_order_id:
                self.orders[client_order_id] = replace(order, status="canceled", updated_at=self.now)
                return
        raise AssertionError("unknown broker order")

    def submit(self, plan: WheelOrderPlanV1) -> PaperOrder:
        self.submit_count += 1
        self.submitted_plans.append(plan)
        if self.submission == "unknown":
            raise PaperSubmissionUnknown("PAPER_ORDER_SUBMISSION_UNKNOWN")
        status = self.submission
        order = PaperOrder(
            client_order_id=plan.client_order_id,
            broker_order_id=f"broker-{self.submit_count}",
            symbol=plan.option_symbol,
            side="buy" if plan.action == WheelAction.BUY_TO_CLOSE else "sell",
            position_intent="buy_to_close" if plan.action == WheelAction.BUY_TO_CLOSE else "sell_to_open",
            quantity=1,
            filled_quantity=1 if status == "filled" else 0,
            status=status,
            filled_average_price=plan.limit_price if status == "filled" else None,
            submitted_at=self.now,
            updated_at=self.now,
        )
        self.orders[plan.client_order_id] = order
        if status == "filled":
            if plan.action == WheelAction.BUY_TO_CLOSE:
                self.position_rows = [item for item in self.position_rows if item.symbol != plan.option_symbol]
            else:
                self.position_rows.append(
                    PaperPosition(
                        symbol=plan.option_symbol,
                        asset_class="us_option",
                        quantity=-1,
                        quantity_available=-1,
                        average_entry_price=plan.limit_price,
                    )
                )
        return order


def _loaded(tmp_path: Path, *, end_date: date = date(2026, 9, 4)) -> LoadedWheelConfig:
    base = load_config(Path("configs/paper/v13_5_qqq.yaml")).config
    config = WheelPaperConfig.model_validate(
        {
            **base.model_dump(mode="python"),
            "runtime": {**base.runtime.model_dump(mode="python"), "runtime_root": Path("runtime")},
            "activation": {
                **base.activation.model_dump(mode="python"),
                "end_date": end_date,
            },
        }
    )
    return LoadedWheelConfig(
        path=tmp_path / "config.yaml",
        config=config,
        config_hash=canonical_hash(config.model_dump(mode="json")),
    )


def _runtime(tmp_path: Path, broker: FakePaperBroker, *, end_date: date = date(2026, 9, 4)) -> PaperWheelRuntime:
    runtime = PaperWheelRuntime(loaded=_loaded(tmp_path, end_date=end_date), broker=broker, project_root=tmp_path)
    runtime.create_arm(now=MONDAY - timedelta(days=1), operator_reason="user authorized QQQ paper canary")
    return runtime


def test_cash_state_submits_one_cash_secured_put_and_never_duplicates(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    runtime = _runtime(tmp_path, broker)

    first = runtime.run_once(now=MONDAY)
    broker.now = MONDAY + timedelta(minutes=1)
    second = runtime.run_once(now=broker.now)

    assert first.status == "ORDER_SUBMITTED"
    assert broker.submit_count == 1
    assert broker.submitted_plans[0].action == WheelAction.SELL_CASH_SECURED_PUT
    assert broker.submitted_plans[0].option_symbol == "QQQ260911P00564000"
    assert second.status == "HOLD"


def test_flat_runtime_can_enter_on_tuesday_at_market_open(tmp_path: Path) -> None:
    tuesday_open = datetime(2026, 9, 1, 13, 30, 1, tzinfo=UTC)
    broker = FakePaperBroker(now=tuesday_open)
    runtime = _runtime(tmp_path, broker)

    outcome = runtime.run_once(now=tuesday_open)

    assert outcome.status == "ORDER_SUBMITTED"
    assert broker.submit_count == 1
    assert broker.submitted_plans[0].action == WheelAction.SELL_CASH_SECURED_PUT


def test_new_entry_cutoff_is_strict_but_does_not_halt_runtime(tmp_path: Path) -> None:
    cutoff = datetime(2026, 9, 1, 19, 15, tzinfo=UTC)
    broker = FakePaperBroker(now=cutoff)
    runtime = _runtime(tmp_path, broker)

    outcome = runtime.run_once(now=cutoff)

    assert outcome.status == "NO_ACTION"
    assert outcome.reason_codes == ("WHEEL_NEW_ENTRY_CUTOFF_REACHED",)
    assert broker.submit_count == 0
    persisted = runtime.store.load_state(config_hash=runtime.loaded.config_hash, now=cutoff)
    assert persisted.sequence == 1
    assert persisted.last_run_at == cutoff


def test_insufficient_cash_rejection_advances_audit_state_without_submission(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    broker.account_row = replace(broker.account_row, cash=Decimal("56399.99"))
    runtime = _runtime(tmp_path, broker)

    outcome = runtime.run_once(now=MONDAY)

    assert outcome.status == "RISK_REJECTED"
    assert outcome.reason_codes == ("WHEEL_CSP_CASH_INSUFFICIENT",)
    assert broker.submit_count == 0
    persisted = runtime.store.load_state(config_hash=runtime.loaded.config_hash, now=MONDAY)
    assert persisted.sequence == 1
    assert persisted.last_run_at == MONDAY
    assert outcome.state_hash == persisted.state_hash


def test_exact_assignment_cash_is_sufficient_without_reserve(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    broker.account_row = replace(broker.account_row, cash=Decimal("56400"))
    runtime = _runtime(tmp_path, broker)

    outcome = runtime.run_once(now=MONDAY)

    assert outcome.status == "ORDER_SUBMITTED"
    assert broker.submit_count == 1
    assert broker.submitted_plans[0].collateral_required == Decimal("56400")


def test_options_buying_power_still_must_cover_full_assignment(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    broker.account_row = replace(broker.account_row, options_buying_power=Decimal("56399.99"))
    runtime = _runtime(tmp_path, broker)

    outcome = runtime.run_once(now=MONDAY)

    assert outcome.status == "RISK_REJECTED"
    assert outcome.reason_codes == ("WHEEL_CSP_OPTIONS_BUYING_POWER_INSUFFICIENT",)
    assert broker.submit_count == 0


def test_seventy_thousand_dollar_put_is_allowed_when_fully_cash_secured(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    broker.underlying_bid = Decimal("714.90")
    broker.underlying_ask = Decimal("715.00")
    broker.put_candidates = (
        PaperOptionContract(
            symbol="QQQ260911P00700000",
            underlying="QQQ",
            right="PUT",
            strike=Decimal("700"),
            expiration=date(2026, 9, 11),
            quote=PaperQuote(bid=Decimal("2.00"), ask=Decimal("2.20"), timestamp=broker.now),
        ),
    )
    runtime = _runtime(tmp_path, broker)

    outcome = runtime.run_once(now=MONDAY)

    assert outcome.status == "ORDER_SUBMITTED"
    assert broker.submit_count == 1
    assert broker.submitted_plans[0].collateral_required == Decimal("70000")


def test_missing_arm_is_deterministic_no_submission(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    runtime = PaperWheelRuntime(loaded=_loaded(tmp_path), broker=broker, project_root=tmp_path)

    outcome = runtime.run_once(now=MONDAY)

    assert outcome.status == "DISARMED"
    assert outcome.reason_codes == ("WHEEL_OPERATOR_ARM_MISSING",)
    assert broker.submit_count == 0


def test_uncertain_submission_halts_without_retry(tmp_path: Path) -> None:
    broker = FakePaperBroker(submission="unknown")
    runtime = _runtime(tmp_path, broker)

    first = runtime.run_once(now=MONDAY)
    second = runtime.run_once(now=MONDAY + timedelta(minutes=1))

    assert first.status == "SUBMISSION_UNKNOWN"
    assert second.status == "HALTED"
    assert second.reason_codes == ("WHEEL_UNCERTAIN_SUBMISSION_NOT_FOUND",)
    assert broker.submit_count == 1


def test_strict_take_profit_submits_buy_to_close(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    runtime = _runtime(tmp_path, broker)
    assert runtime.run_once(now=MONDAY).status == "ORDER_SUBMITTED"
    broker.option_ask = Decimal("1.69")
    broker.now = MONDAY + timedelta(days=1)

    outcome = runtime.run_once(now=broker.now)

    assert outcome.status == "ORDER_SUBMITTED"
    assert broker.submit_count == 2
    assert broker.submitted_plans[-1].action == WheelAction.BUY_TO_CLOSE


def test_assignment_shape_transitions_to_covered_call_next_week(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    runtime = _runtime(tmp_path, broker, end_date=date(2026, 9, 8))
    assert runtime.run_once(now=MONDAY).status == "ORDER_SUBMITTED"
    broker.position_rows = [
        PaperPosition(
            symbol="QQQ",
            asset_class="us_equity",
            quantity=100,
            quantity_available=100,
            average_entry_price=Decimal("564"),
        )
    ]
    broker.activity_rows = [
        PaperOptionLifecycleActivity(
            activity_id="assignment-1",
            activity_type="OPASN",
            symbol="QQQ260911P00564000",
            quantity=1,
            activity_date=date(2026, 9, 7),
            status="executed",
        )
    ]
    broker.now = MONDAY + timedelta(days=7)

    outcome = runtime.run_once(now=broker.now)

    assert outcome.status == "ORDER_SUBMITTED"
    assert broker.submitted_plans[-1].action == WheelAction.SELL_COVERED_CALL


def test_unmanaged_position_halts_before_any_order(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    broker.position_rows.append(
        PaperPosition(
            symbol="SPY",
            asset_class="us_equity",
            quantity=1,
            quantity_available=1,
            average_entry_price=Decimal("500"),
        )
    )
    runtime = _runtime(tmp_path, broker)

    outcome = runtime.run_once(now=MONDAY)

    assert outcome.status == "HALTED"
    assert outcome.reason_codes == ("WHEEL_UNMANAGED_POSITION_PRESENT",)
    assert broker.submit_count == 0


def test_stale_open_order_is_canceled_and_client_id_is_never_reused(tmp_path: Path) -> None:
    broker = FakePaperBroker(submission="accepted")
    runtime = _runtime(tmp_path, broker)
    assert runtime.run_once(now=MONDAY).status == "ORDER_SUBMITTED"

    broker.now = MONDAY + timedelta(minutes=3, seconds=1)
    cancel_requested = runtime.run_once(now=broker.now)
    broker.now += timedelta(seconds=30)
    no_retry = runtime.run_once(now=broker.now)

    assert cancel_requested.status == "ORDER_PENDING"
    assert broker.cancel_count == 1
    assert no_retry.status == "NO_ACTION"
    assert no_retry.reason_codes == ("WHEEL_CLIENT_ORDER_ID_ALREADY_USED",)
    assert broker.submit_count == 1


def test_canceled_take_profit_retries_with_fresh_deterministic_client_id(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    runtime = _runtime(tmp_path, broker)
    assert runtime.run_once(now=MONDAY).status == "ORDER_SUBMITTED"
    broker.option_ask = Decimal("1.69")
    broker.submission = "accepted"
    broker.now = MONDAY + timedelta(minutes=1)
    first_close = runtime.run_once(now=broker.now)
    first_close_id = first_close.client_order_id

    broker.now += timedelta(minutes=3, seconds=1)
    assert runtime.run_once(now=broker.now).status == "ORDER_PENDING"
    broker.now += timedelta(seconds=30)
    retry = runtime.run_once(now=broker.now)

    assert retry.status == "ORDER_SUBMITTED"
    assert retry.client_order_id != first_close_id
    assert broker.submit_count == 3
    assert broker.submitted_plans[-1].action == WheelAction.BUY_TO_CLOSE


def test_terminal_cancel_restores_ready_state_when_take_profit_recedes(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    runtime = _runtime(tmp_path, broker)
    assert runtime.run_once(now=MONDAY).status == "ORDER_SUBMITTED"
    broker.option_ask = Decimal("1.69")
    broker.submission = "accepted"
    broker.now = MONDAY + timedelta(minutes=1)
    assert runtime.run_once(now=broker.now).status == "ORDER_SUBMITTED"

    broker.now += timedelta(minutes=3, seconds=1)
    assert runtime.run_once(now=broker.now).status == "ORDER_PENDING"
    broker.option_ask = Decimal("2.20")
    broker.now += timedelta(seconds=30)
    outcome = runtime.run_once(now=broker.now)

    assert outcome.status == "HOLD"
    persisted = runtime.store.load_state(config_hash=runtime.loaded.config_hash, now=broker.now)
    assert persisted.status == "READY"
    assert broker.submit_count == 2


def test_operator_halt_cancels_owned_open_order_and_stays_halted(tmp_path: Path) -> None:
    broker = FakePaperBroker(submission="accepted")
    runtime = _runtime(tmp_path, broker)
    assert runtime.run_once(now=MONDAY).status == "ORDER_SUBMITTED"
    broker.now = MONDAY + timedelta(minutes=1)

    halted = runtime.operator_halt(now=broker.now, reason="operator emergency stop")
    later = runtime.run_once(now=broker.now + timedelta(minutes=1))

    assert halted.status == "HALTED"
    assert halted.reason_codes == ("WHEEL_OPERATOR_HALTED",)
    assert broker.cancel_count == 1
    assert later.status == "HALTED"
    assert broker.submit_count == 1


def test_preflight_rejects_same_symbol_option_not_bound_to_state(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    broker.position_rows = [
        PaperPosition(
            symbol="QQQ260911P00564000",
            asset_class="us_option",
            quantity=-1,
            quantity_available=-1,
            average_entry_price=Decimal("2.00"),
        )
    ]
    runtime = PaperWheelRuntime(loaded=_loaded(tmp_path), broker=broker, project_root=tmp_path)

    outcome = runtime.preflight(now=MONDAY)

    assert outcome.status == "PREFLIGHT_BLOCKED"
    assert "WHEEL_UNMANAGED_OPTION_POSITION" in outcome.reason_codes


def test_schedule_verification_requires_exact_bound_arm(tmp_path: Path) -> None:
    broker = FakePaperBroker(now=MONDAY - timedelta(days=1))
    runtime = PaperWheelRuntime(loaded=_loaded(tmp_path), broker=broker, project_root=tmp_path)
    missing = runtime.scheduled_arm_preflight(now=broker.now)
    runtime.create_arm(now=broker.now, operator_reason="authorized schedule fixture")
    ready = runtime.scheduled_arm_preflight(now=broker.now)

    assert missing.status == "PAPER_ARM_SCHEDULE_BLOCKED"
    assert missing.reason_codes == ("WHEEL_OPERATOR_ARM_MISSING",)
    assert ready.status == "PAPER_ARM_SCHEDULE_READY"


def test_config_migration_preserves_managed_position_and_rebinds_arm(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    current_runtime = _runtime(tmp_path, broker)
    assert current_runtime.run_once(now=MONDAY).status == "ORDER_SUBMITTED"
    previous_hash = current_runtime.loaded.config_hash
    migrated_hash = canonical_hash({"config": current_runtime.config.model_dump(mode="json"), "revision": "next"})
    migrated_loaded = current_runtime.loaded.model_copy(update={"config_hash": migrated_hash})
    migrated_runtime = PaperWheelRuntime(loaded=migrated_loaded, broker=broker, project_root=tmp_path)
    submissions_before_migration = broker.submit_count

    outcome = migrated_runtime.migrate_config(
        now=MONDAY + timedelta(minutes=1),
        expected_current_config_hash=previous_hash,
        operator_reason="operator authorized cash policy migration",
    )
    repeated = migrated_runtime.migrate_config(
        now=MONDAY + timedelta(minutes=2),
        expected_current_config_hash=previous_hash,
        operator_reason="operator authorized cash policy migration",
    )

    state = migrated_runtime.store.load_state(config_hash=migrated_hash, now=MONDAY)
    arm = migrated_runtime.store.load_arm()
    events = migrated_runtime.store.events()
    assert outcome.status == "PAPER_CONFIG_MIGRATED"
    assert repeated.status == "PAPER_CONFIG_MIGRATION_ALREADY_APPLIED"
    assert state.managed_options["QQQ"].option_symbol == "QQQ260911P00564000"
    assert arm is not None and arm.config_hash == migrated_hash
    assert [event.event_type for event in events[-2:]] == ["CONFIG_MIGRATION_STARTED", "CONFIG_MIGRATION_COMPLETED"]
    assert broker.submit_count == submissions_before_migration
    assert broker.cancel_count == 0


def test_config_migration_rejects_wrong_source_hash_without_writes(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    runtime = _runtime(tmp_path, broker)
    assert runtime.run_once(now=MONDAY).status == "ORDER_SUBMITTED"
    migrated_hash = canonical_hash({"config": runtime.config.model_dump(mode="json"), "revision": "next"})
    migrated_loaded = runtime.loaded.model_copy(update={"config_hash": migrated_hash})
    migrated_runtime = PaperWheelRuntime(loaded=migrated_loaded, broker=broker, project_root=tmp_path)
    previous_events = migrated_runtime.store.events()

    with pytest.raises(RuntimeError, match="WHEEL_CONFIG_MIGRATION_BINDING_MISMATCH"):
        migrated_runtime.migrate_config(
            now=MONDAY,
            expected_current_config_hash=canonical_hash({"wrong": "source"}),
            operator_reason="operator authorized cash policy migration",
        )

    assert migrated_runtime.store.events() == previous_events


def test_daily_drawdown_blocks_entry_but_does_not_block_buy_to_close(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    runtime = _runtime(tmp_path, broker)
    assert runtime.run_once(now=MONDAY).status == "ORDER_SUBMITTED"
    broker.option_ask = Decimal("1.69")
    broker.now = MONDAY + timedelta(days=1)
    broker.account_row = replace(broker.account_row, equity=Decimal("97000"))

    outcome = runtime.run_once(now=broker.now)

    assert outcome.status == "ORDER_SUBMITTED"
    assert broker.submitted_plans[-1].action == WheelAction.BUY_TO_CLOSE


def test_expired_arm_blocks_entries_but_still_allows_buy_to_close(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    runtime = _runtime(tmp_path, broker)
    assert runtime.run_once(now=MONDAY).status == "ORDER_SUBMITTED"
    broker.option_ask = Decimal("1.69")
    broker.now = MONDAY + timedelta(days=7)

    outcome = runtime.run_once(now=broker.now)

    assert outcome.status == "ORDER_SUBMITTED"
    assert broker.submitted_plans[-1].action == WheelAction.BUY_TO_CLOSE


def test_missing_lifecycle_activity_waits_then_halts_without_new_order(tmp_path: Path) -> None:
    broker = FakePaperBroker()
    runtime = _runtime(tmp_path, broker)
    assert runtime.run_once(now=MONDAY).status == "ORDER_SUBMITTED"
    broker.position_rows = []
    broker.now = MONDAY + timedelta(minutes=2)

    waiting = runtime.run_once(now=broker.now)
    broker.now += timedelta(minutes=31)
    halted = runtime.run_once(now=broker.now)

    assert waiting.status == "NO_ACTION"
    assert halted.status == "HALTED"
    assert halted.reason_codes == ("WHEEL_LIFECYCLE_ACTIVITY_MISSING",)
    assert broker.submit_count == 1


def test_fresh_identical_runs_create_identical_plan_hash_and_client_id(tmp_path: Path) -> None:
    broker_one = FakePaperBroker()
    broker_two = FakePaperBroker()
    first = _runtime(tmp_path / "one", broker_one).run_once(now=MONDAY)
    second = _runtime(tmp_path / "two", broker_two).run_once(now=MONDAY)

    assert first.status == second.status == "ORDER_SUBMITTED"
    assert first.client_order_id == second.client_order_id
    assert first.plan_hash == second.plan_hash
