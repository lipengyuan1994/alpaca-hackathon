"""One-shot, idempotent V13.5 paper runtime designed for a 60-second scheduler."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from packages.contracts.canonical import canonical_hash

from .broker import (
    PaperAccount,
    PaperBrokerError,
    PaperClock,
    PaperOptionContract,
    PaperOrder,
    PaperPosition,
    PaperSubmissionUnknown,
    PaperWheelBroker,
)
from .config import LoadedWheelConfig
from .models import (
    ManagedOptionV1,
    WheelAction,
    WheelArmTokenV1,
    WheelOrderPlanV1,
    WheelRuntimeStateV1,
)
from .risk import (
    account_violations,
    broker_shape_violations,
    plan_violations,
    quote_violations,
    runtime_position_violations,
)
from .state import WheelStateStore
from .strategy import should_take_profit, target_strike_fraction, trend_is_up

_EASTERN = ZoneInfo("America/New_York")
_TERMINAL = {
    "filled",
    "canceled",
    "cancelled",
    "rejected",
    "expired",
    "done_for_day",
    "replaced",
    "stopped",
    "suspended",
    "calculated",
}
_NONTERMINAL = {
    "new",
    "accepted",
    "pending_new",
    "accepted_for_bidding",
    "held",
    "partially_filled",
    "pending_cancel",
    "pending_replace",
    "pending_review",
}


@dataclass(frozen=True)
class RuntimeOutcome:
    status: str
    reason_codes: tuple[str, ...] = ()
    plan_hash: str | None = None
    client_order_id: str | None = None
    state_hash: str | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "plan_hash": self.plan_hash,
            "client_order_id": self.client_order_id,
            "state_hash": self.state_hash,
        }


def _account_hash(account_id: str) -> str:
    return canonical_hash({"paper_account_id": account_id})


def _clock_text(value: str) -> time:
    hour, minute = (int(item) for item in value.split(":"))
    return time(hour=hour, minute=minute)


def _week_key(value: date) -> str:
    year, week, _ = value.isocalendar()
    return f"{year:04d}-W{week:02d}"


def _client_order_id(prefix: str, payload: dict[str, Any]) -> str:
    digest = canonical_hash(payload).removeprefix("sha256:")
    return f"{prefix}-{digest[:32]}"


class PaperWheelRuntime:
    def __init__(self, *, loaded: LoadedWheelConfig, broker: PaperWheelBroker, project_root: Any) -> None:
        self.loaded = loaded
        self.config = loaded.config
        self.broker = broker
        self.project_root = project_root
        self.store = WheelStateStore(project_root / self.config.runtime.runtime_root)

    def create_arm(self, *, now: datetime, operator_reason: str) -> WheelArmTokenV1:
        account = self.broker.account()
        if account.account_id != self.broker.expected_account_id:
            raise RuntimeError("WHEEL_ARM_ACCOUNT_MISMATCH")
        local_start = datetime.combine(self.config.activation.start_date, time.min, tzinfo=_EASTERN)
        local_expiry = datetime.combine(self.config.activation.end_date + timedelta(days=1), time.min, tzinfo=_EASTERN)
        token = WheelArmTokenV1(
            config_hash=self.loaded.config_hash,
            account_id_hash=_account_hash(account.account_id),
            valid_from=local_start.astimezone(UTC),
            expires_at=local_expiry.astimezone(UTC),
            operator_reason=operator_reason,
        )
        self.store.save_arm(token)
        self.store.append(
            event_type="PAPER_ARM_CREATED",
            occurred_at=now,
            detail={"config_hash": self.loaded.config_hash, "account_id_hash": token.account_id_hash, "token_hash": token.token_hash},
        )
        return token

    def operator_halt(self, *, now: datetime, reason: str) -> RuntimeOutcome:
        if not 8 <= len(reason.strip()) <= 256:
            raise RuntimeError("WHEEL_OPERATOR_HALT_REASON_INVALID")
        with self.store.lease():
            state = self.store.load_state(config_hash=self.loaded.config_hash, now=now)
            normalized_now = now.astimezone(UTC)
            cancellation_unknown = False
            for order in self.broker.open_orders():
                if not order.client_order_id.startswith(self.config.runtime.client_order_prefix):
                    continue
                try:
                    self.broker.cancel_order(order.broker_order_id)
                except PaperBrokerError:
                    cancellation_unknown = True
                    self.store.append(
                        event_type="OPERATOR_HALT_CANCEL_UNKNOWN",
                        occurred_at=normalized_now,
                        client_order_id=order.client_order_id,
                        detail={"broker_order_id": order.broker_order_id},
                    )
                else:
                    self.store.append(
                        event_type="OPERATOR_HALT_CANCEL_REQUESTED",
                        occurred_at=normalized_now,
                        client_order_id=order.client_order_id,
                        detail={"broker_order_id": order.broker_order_id},
                    )
            reason_code = "WHEEL_OPERATOR_HALT_CANCEL_UNKNOWN" if cancellation_unknown else "WHEEL_OPERATOR_HALTED"
            self.store.append(
                event_type="OPERATOR_HALT_REQUESTED",
                occurred_at=normalized_now,
                detail={"operator_reason": reason.strip(), "reason_code": reason_code},
            )
            return self._halt(state, now=normalized_now, reason=reason_code)

    def preflight(self, *, now: datetime) -> RuntimeOutcome:
        state = self.store.load_state(config_hash=self.loaded.config_hash, now=now)
        account = self.broker.account()
        clock = self.broker.clock()
        positions = self.broker.positions()
        orders = self.broker.open_orders()
        reasons = (
            *account_violations(account, clock, now=now, config=self.config),
            *broker_shape_violations(positions=positions, orders=orders, config=self.config),
            *runtime_position_violations(state=state, positions=positions, config=self.config),
        )
        if state.status == "HALTED":
            reasons = (*reasons, state.halt_reason or "WHEEL_HALTED")
        if account.account_id != self.broker.expected_account_id:
            reasons = (*reasons, "WHEEL_PAPER_ACCOUNT_ID_MISMATCH")
        status = "PREFLIGHT_READY" if not reasons else "PREFLIGHT_BLOCKED"
        return RuntimeOutcome(status=status, reason_codes=tuple(dict.fromkeys(reasons)))

    def scheduled_arm_preflight(self, *, now: datetime) -> RuntimeOutcome:
        """Verify an arm is bound to this config/account for its scheduled window."""
        preflight = self.preflight(now=now)
        if preflight.status != "PREFLIGHT_READY":
            return preflight
        account = self.broker.account()
        token = self.store.load_arm()
        reasons: list[str] = []
        if token is None:
            reasons.append("WHEEL_OPERATOR_ARM_MISSING")
        else:
            expected_start = datetime.combine(
                self.config.activation.start_date,
                time.min,
                tzinfo=_EASTERN,
            ).astimezone(UTC)
            expected_expiry = datetime.combine(
                self.config.activation.end_date + timedelta(days=1),
                time.min,
                tzinfo=_EASTERN,
            ).astimezone(UTC)
            if token.config_hash != self.loaded.config_hash:
                reasons.append("WHEEL_ARM_CONFIG_HASH_MISMATCH")
            if token.account_id_hash != _account_hash(account.account_id):
                reasons.append("WHEEL_ARM_ACCOUNT_MISMATCH")
            if token.valid_from != expected_start or token.expires_at != expected_expiry:
                reasons.append("WHEEL_ARM_SCHEDULE_WINDOW_MISMATCH")
        status = "PAPER_ARM_SCHEDULE_READY" if not reasons else "PAPER_ARM_SCHEDULE_BLOCKED"
        return RuntimeOutcome(status=status, reason_codes=tuple(reasons))

    def run_once(self, *, now: datetime) -> RuntimeOutcome:
        now = now.astimezone(UTC)
        try:
            with self.store.lease():
                return self._run_leased(now=now)
        except RuntimeError as exc:
            return RuntimeOutcome(status="BLOCKED", reason_codes=(str(exc),))
        except PaperBrokerError as exc:
            return RuntimeOutcome(status="BROKER_UNAVAILABLE", reason_codes=(str(exc),))

    def _run_leased(self, *, now: datetime) -> RuntimeOutcome:
        state = self.store.load_state(config_hash=self.loaded.config_hash, now=now)
        if state.status == "HALTED":
            return RuntimeOutcome(status="HALTED", reason_codes=(state.halt_reason or "WHEEL_HALTED",), state_hash=state.state_hash)
        account = self.broker.account()
        clock = self.broker.clock()
        positions = self.broker.positions()
        orders = self.broker.open_orders()
        state, reconciliation_reason = self._reconcile_journal(state, positions=positions, now=now)
        if reconciliation_reason is not None:
            return self._halt(state, now=now, reason=reconciliation_reason)
        if self._journal_has_unresolved():
            next_state = self._advance(state, now=now, status="RECONCILE_ONLY")
            self.store.save_state(next_state)
            return RuntimeOutcome(status="ORDER_PENDING", state_hash=next_state.state_hash)
        positions = self.broker.positions()
        orders = self.broker.open_orders()
        structural = broker_shape_violations(positions=positions, orders=orders, config=self.config)
        if structural:
            return self._halt(state, now=now, reason=structural[0])
        account_reasons = account_violations(account, clock, now=now, config=self.config)
        arm_reasons = self._arm_violations(account=account, now=now)
        local = now.astimezone(_EASTERN)
        if local.date() < self.config.activation.start_date or local.date() > self.config.activation.end_date:
            arm_reasons = (*arm_reasons, "WHEEL_OUTSIDE_ACTIVATION_WINDOW")
        if not clock.is_open:
            next_state = self._advance(state, now=now)
            self.store.save_state(next_state)
            return RuntimeOutcome(status="MARKET_CLOSED", reason_codes=tuple(dict.fromkeys((*account_reasons, *arm_reasons))), state_hash=next_state.state_hash)
        hard_account_reasons = tuple(
            reason for reason in account_reasons if reason != "WHEEL_DAILY_DRAWDOWN_LIMIT"
        )
        if hard_account_reasons:
            return self._halt(state, now=now, reason=hard_account_reasons[0])
        if arm_reasons and not state.managed_options:
            next_state = self._advance(state, now=now)
            self.store.save_state(next_state)
            return RuntimeOutcome(status="DISARMED", reason_codes=tuple(dict.fromkeys(arm_reasons)), state_hash=next_state.state_hash)
        if orders:
            next_state = self._advance(state, now=now, status="RECONCILE_ONLY")
            self.store.save_state(next_state)
            return RuntimeOutcome(status="ORDER_PENDING", state_hash=next_state.state_hash)
        for symbol in self.config.strategy.symbols:
            outcome = self._manage_symbol(
                symbol,
                state=state,
                account=account,
                clock=clock,
                positions=positions,
                now=now,
                entry_block_reasons=tuple(
                    dict.fromkeys(
                        (
                            *(reason for reason in account_reasons if reason == "WHEEL_DAILY_DRAWDOWN_LIMIT"),
                            *arm_reasons,
                        )
                    )
                ),
            )
            if outcome is not None:
                return outcome
        next_state = self._advance(state, now=now)
        self.store.save_state(next_state)
        return RuntimeOutcome(status="NO_ACTION", reason_codes=("WHEEL_NO_ELIGIBLE_ACTION",), state_hash=next_state.state_hash)

    def _arm_violations(self, *, account: PaperAccount, now: datetime) -> tuple[str, ...]:
        if not self.config.runtime.submission_enabled:
            return ("WHEEL_SUBMISSION_DISABLED",)
        if not self.config.activation.require_operator_arm:
            return ()
        token = self.store.load_arm()
        if token is None:
            return ("WHEEL_OPERATOR_ARM_MISSING",)
        reasons = []
        if token.config_hash != self.loaded.config_hash:
            reasons.append("WHEEL_ARM_CONFIG_HASH_MISMATCH")
        if token.account_id_hash != _account_hash(account.account_id):
            reasons.append("WHEEL_ARM_ACCOUNT_MISMATCH")
        if not (token.valid_from <= now < token.expires_at):
            reasons.append("WHEEL_ARM_EXPIRED_OR_NOT_YET_VALID")
        return tuple(reasons)

    def _reconcile_journal(
        self,
        state: WheelRuntimeStateV1,
        *,
        positions: tuple[PaperPosition, ...],
        now: datetime,
    ) -> tuple[WheelRuntimeStateV1, str | None]:
        events = self.store.events()
        latest: dict[str, Any] = {}
        plans: dict[str, WheelOrderPlanV1] = {}
        for event in events:
            if event.client_order_id:
                latest[event.client_order_id] = event
                if event.event_type == "ORDER_PREPARED" and "plan" in event.detail:
                    plans[event.client_order_id] = WheelOrderPlanV1.model_validate(event.detail["plan"])
        for client_id, event in sorted(latest.items()):
            if event.event_type in {"ORDER_TERMINAL", "ORDER_ABORTED_BEFORE_SUBMISSION"}:
                continue
            order = self.broker.order_by_client_id(client_id)
            if order is None:
                if event.event_type == "ORDER_PREPARED":
                    self.store.append(event_type="ORDER_ABORTED_BEFORE_SUBMISSION", occurred_at=now, client_order_id=client_id, plan_hash=event.plan_hash)
                    continue
                return state, "WHEEL_UNCERTAIN_SUBMISSION_NOT_FOUND"
            if order.status in _NONTERMINAL:
                if order.submitted_at > now:
                    return state, "WHEEL_ORDER_TIMESTAMP_IN_FUTURE"
                age = now - order.submitted_at
                if age >= timedelta(seconds=self.config.schedule.order_cancel_after_seconds):
                    if event.event_type == "ORDER_CANCEL_REQUESTED":
                        cancel_age = now - event.occurred_at.astimezone(UTC)
                        if cancel_age >= timedelta(seconds=self.config.schedule.cancel_confirmation_grace_seconds):
                            return state, "WHEEL_ORDER_CANCEL_NOT_CONFIRMED"
                    else:
                        try:
                            self.broker.cancel_order(order.broker_order_id)
                        except PaperBrokerError:
                            self.store.append(
                                event_type="ORDER_CANCEL_UNKNOWN",
                                occurred_at=now,
                                client_order_id=client_id,
                                plan_hash=event.plan_hash,
                                detail={"broker_order_id": order.broker_order_id},
                            )
                            return state, "WHEEL_ORDER_CANCEL_UNKNOWN"
                        self.store.append(
                            event_type="ORDER_CANCEL_REQUESTED",
                            occurred_at=now,
                            client_order_id=client_id,
                            plan_hash=event.plan_hash,
                            detail={"broker_order_id": order.broker_order_id},
                        )
                return self._advance(state, now=now, status="RECONCILE_ONLY"), None
            if order.status not in _TERMINAL:
                return state, "WHEEL_BROKER_ORDER_STATUS_UNKNOWN"
            self.store.append(
                event_type="ORDER_TERMINAL",
                occurred_at=now,
                client_order_id=client_id,
                plan_hash=event.plan_hash,
                detail={"status": order.status, "filled_quantity": order.filled_quantity, "broker_order_id": order.broker_order_id},
            )
            plan = plans.get(client_id)
            if order.status == "filled":
                if plan is None or order.filled_quantity != 1 or order.filled_average_price is None:
                    return state, "WHEEL_FILLED_ORDER_RECONCILIATION_INVALID"
                state = self._apply_fill(state, plan=plan, order=order, now=now)
        for symbol, managed in tuple(state.managed_options.items()):
            option = next((item for item in positions if item.symbol == managed.option_symbol), None)
            if option is not None:
                if option.quantity != -1:
                    return state, "WHEEL_MANAGED_OPTION_QUANTITY_DRIFT"
                if symbol in state.lifecycle_missing_since_by_symbol:
                    missing = dict(state.lifecycle_missing_since_by_symbol)
                    missing.pop(symbol, None)
                    state = self._advance(state, now=now, lifecycle_missing_since_by_symbol=missing)
                continue
            if now - managed.opened_at.astimezone(UTC) <= timedelta(seconds=60):
                return self._advance(state, now=now, status="RECONCILE_ONLY"), None
            underlying = next((item for item in positions if item.symbol == symbol), None)
            shares = 0 if underlying is None else underlying.quantity
            activities = tuple(
                item
                for item in self.broker.option_lifecycle_activities(
                    managed.option_symbol,
                    after=managed.opened_at.astimezone(UTC) - timedelta(days=1),
                )
                if item.status == "executed" and abs(item.quantity) == 1
            )
            activity_types = {item.activity_type for item in activities}
            if len(activity_types) > 1:
                return state, "WHEEL_LIFECYCLE_ACTIVITY_AMBIGUOUS"
            if not activity_types:
                missing = dict(state.lifecycle_missing_since_by_symbol)
                missing_since = missing.setdefault(symbol, now)
                state = self._advance(state, now=now, status="RECONCILE_ONLY", lifecycle_missing_since_by_symbol=missing)
                if now - missing_since.astimezone(UTC) >= timedelta(
                    minutes=self.config.schedule.lifecycle_settlement_grace_minutes
                ):
                    return state, "WHEEL_LIFECYCLE_ACTIVITY_MISSING"
                continue
            activity_type = next(iter(activity_types))
            expected_shares = (
                100
                if (activity_type == "OPASN" and managed.right == "PUT")
                or (activity_type == "OPEXP" and managed.right == "CALL")
                else 0
            )
            if shares != expected_shares:
                return state, "WHEEL_ASSIGNMENT_RECONCILIATION_DRIFT"
            remaining = dict(state.managed_options)
            remaining.pop(symbol, None)
            missing = dict(state.lifecycle_missing_since_by_symbol)
            missing.pop(symbol, None)
            state = self._advance(
                state,
                now=now,
                managed_options=remaining,
                lifecycle_missing_since_by_symbol=missing,
            )
            self.store.append(
                event_type="BROKER_OPTION_LIFECYCLE_RECONCILED",
                occurred_at=now,
                detail={
                    "underlying": symbol,
                    "option_symbol": managed.option_symbol,
                    "activity_type": activity_type,
                    "activity_ids": [item.activity_id for item in activities],
                    "resulting_shares": shares,
                },
            )
        return state, None

    def _manage_symbol(
        self,
        symbol: str,
        *,
        state: WheelRuntimeStateV1,
        account: PaperAccount,
        clock: PaperClock,
        positions: tuple[PaperPosition, ...],
        now: datetime,
        entry_block_reasons: tuple[str, ...],
    ) -> RuntimeOutcome | None:
        managed = state.managed_options.get(symbol)
        if managed is not None:
            option_position = next((item for item in positions if item.symbol == managed.option_symbol), None)
            if option_position is None:
                return None
            quote = self.broker.option_quote(managed.option_symbol)
            reasons = quote_violations(
                quote,
                now=now,
                maximum_age_seconds=self.config.risk.maximum_quote_age_seconds,
                maximum_relative_spread=self.config.risk.maximum_relative_spread,
                minimum_bid=Decimal("0"),
            )
            if reasons:
                return RuntimeOutcome(status="HOLD", reason_codes=reasons, state_hash=state.state_hash)
            if should_take_profit(
                entry_credit=managed.entry_credit,
                close_debit=quote.ask,
                target_fraction=self.config.strategy.take_profit_fraction,
            ):
                closes = self.broker.completed_daily_closes(symbol, now=now, sessions=self.config.strategy.trend_sessions)
                trend = trend_is_up(closes, sessions=self.config.strategy.trend_sessions)
                if trend is None:
                    return RuntimeOutcome(status="HOLD", reason_codes=("WHEEL_TREND_HISTORY_MISSING",), state_hash=state.state_hash)
                plan = self._close_plan(managed=managed, quote=quote, trend_up=trend, now=now)
                return self._submit(plan, state=state, account=account, positions=positions, now=now)
            return RuntimeOutcome(status="HOLD", reason_codes=("WHEEL_TAKE_PROFIT_NOT_REACHED",), state_hash=state.state_hash)
        underlying = next((item for item in positions if item.symbol == symbol), None)
        shares = 0 if underlying is None else underlying.quantity
        related_options = [item for item in positions if item.asset_class == "us_option" and item.symbol.startswith(symbol)]
        if related_options:
            return self._halt(state, now=now, reason="WHEEL_UNMANAGED_OPTION_POSITION")
        if shares not in {0, 100}:
            return self._halt(state, now=now, reason="WHEEL_UNDERLYING_SHARE_QUANTITY_INVALID")
        if entry_block_reasons:
            return RuntimeOutcome(status="NO_ACTION", reason_codes=entry_block_reasons, state_hash=state.state_hash)
        if not self._entry_window(now=now, clock=clock):
            return RuntimeOutcome(status="NO_ACTION", reason_codes=("WHEEL_OUTSIDE_ENTRY_WINDOW",), state_hash=state.state_hash)
        week = _week_key(now.astimezone(_EASTERN).date())
        if state.last_entry_week_by_symbol.get(symbol) == week:
            return RuntimeOutcome(status="NO_ACTION", reason_codes=("WHEEL_WEEKLY_ENTRY_ALREADY_USED",), state_hash=state.state_hash)
        closes = self.broker.completed_daily_closes(symbol, now=now, sessions=self.config.strategy.trend_sessions)
        trend = trend_is_up(closes, sessions=self.config.strategy.trend_sessions)
        if trend is None:
            return RuntimeOutcome(status="NO_ACTION", reason_codes=("WHEEL_TREND_HISTORY_MISSING",), state_hash=state.state_hash)
        plan_or_reasons = self._entry_plan(symbol=symbol, shares=shares, trend_up=trend, now=now)
        if isinstance(plan_or_reasons, tuple):
            return RuntimeOutcome(status="NO_ACTION", reason_codes=plan_or_reasons, state_hash=state.state_hash)
        return self._submit(plan_or_reasons, state=state, account=account, positions=positions, now=now)

    def _journal_has_unresolved(self) -> bool:
        latest: dict[str, str] = {}
        for event in self.store.events():
            if event.client_order_id:
                latest[event.client_order_id] = event.event_type
        return any(
            event_type not in {"ORDER_TERMINAL", "ORDER_ABORTED_BEFORE_SUBMISSION"}
            for event_type in latest.values()
        )

    def _entry_window(self, *, now: datetime, clock: PaperClock) -> bool:
        local = now.astimezone(_EASTERN)
        start = _clock_text(self.config.schedule.entry_time)
        start_dt = datetime.combine(local.date(), start, tzinfo=_EASTERN)
        end_dt = start_dt + timedelta(minutes=self.config.schedule.entry_window_minutes)
        if not (start_dt <= local < end_dt) or not clock.is_open:
            return False
        if self.config.schedule.first_eligible_session_per_iso_week:
            monday = local.date() - timedelta(days=local.weekday())
            friday = monday + timedelta(days=4)
            sessions = self.broker.calendar_open_dates(monday, friday)
            if not sessions or local.date() != min(sessions):
                return False
        return True

    def _entry_plan(
        self,
        *,
        symbol: str,
        shares: int,
        trend_up: bool,
        now: datetime,
    ) -> WheelOrderPlanV1 | tuple[str, ...]:
        right = "PUT" if shares == 0 else "CALL"
        action = WheelAction.SELL_CASH_SECURED_PUT if right == "PUT" else WheelAction.SELL_COVERED_CALL
        underlying_quote = self.broker.underlying_quote(symbol)
        underlying_reasons = quote_violations(
            underlying_quote,
            now=now,
            maximum_age_seconds=self.config.risk.maximum_quote_age_seconds,
            maximum_relative_spread=self.config.risk.maximum_relative_spread,
            minimum_bid=Decimal("0.01"),
        )
        if underlying_reasons:
            return underlying_reasons
        spot = (underlying_quote.bid + underlying_quote.ask) / Decimal("2")
        fraction = target_strike_fraction(right=right, trend_up=trend_up, config=self.config.strategy)
        target = spot * fraction
        local_date = now.astimezone(_EASTERN).date()
        contracts = self.broker.option_candidates(
            symbol,
            right=right,
            minimum_expiration=local_date + timedelta(days=self.config.strategy.minimum_dte),
            maximum_expiration=local_date + timedelta(days=self.config.strategy.maximum_dte),
            minimum_strike=spot * Decimal("0.75"),
            maximum_strike=spot * Decimal("1.25"),
        )
        valid = [
            item
            for item in contracts
            if not quote_violations(
                item.quote,
                now=now,
                maximum_age_seconds=self.config.risk.maximum_quote_age_seconds,
                maximum_relative_spread=self.config.risk.maximum_relative_spread,
                minimum_bid=self.config.risk.minimum_option_bid,
            )
        ]
        if right == "PUT":
            eligible = [item for item in valid if item.strike <= target]
            selected = None if not eligible else max(eligible, key=lambda item: (item.strike, -item.expiration.toordinal(), item.symbol))
        else:
            eligible = [item for item in valid if item.strike >= target]
            selected = None if not eligible else min(eligible, key=lambda item: (item.strike, item.expiration, item.symbol))
        if selected is None:
            return ("WHEEL_NO_ELIGIBLE_OPTION_CONTRACT",)
        return self._entry_plan_from_contract(
            action=action,
            contract=selected,
            spot=spot,
            trend_up=trend_up,
            now=now,
        )

    def _entry_plan_from_contract(
        self,
        *,
        action: WheelAction,
        contract: PaperOptionContract,
        spot: Decimal,
        trend_up: bool,
        now: datetime,
    ) -> WheelOrderPlanV1:
        collateral = contract.strike * Decimal("100") if action == WheelAction.SELL_CASH_SECURED_PUT else spot * Decimal("100")
        semantics = {
            "strategy_id": self.config.strategy.strategy_id,
            "underlying": contract.underlying,
            "action": action,
            "option_symbol": contract.symbol,
            "date": str(now.astimezone(_EASTERN).date()),
            "config_hash": self.loaded.config_hash,
        }
        return WheelOrderPlanV1(
            strategy_id="v13.5",
            underlying=contract.underlying,
            action=action,
            option_symbol=contract.symbol,
            right=contract.right,
            strike=contract.strike,
            expiration=contract.expiration,
            limit_price=contract.quote.bid.quantize(Decimal("0.01"), rounding=ROUND_DOWN),
            quote_bid=contract.quote.bid,
            quote_ask=contract.quote.ask,
            quote_time=contract.quote.timestamp,
            underlying_price=spot,
            trend_up=trend_up,
            collateral_required=collateral,
            maximum_loss=max(Decimal("0.01"), collateral - contract.quote.bid * Decimal("100")),
            client_order_id=_client_order_id(self.config.runtime.client_order_prefix, semantics),
            config_hash=self.loaded.config_hash,
            created_at=now,
        )

    def _close_plan(self, *, managed: ManagedOptionV1, quote: Any, trend_up: bool, now: datetime) -> WheelOrderPlanV1:
        semantics = {
            "strategy_id": "v13.5",
            "underlying": managed.underlying,
            "action": WheelAction.BUY_TO_CLOSE,
            "option_symbol": managed.option_symbol,
            "date": str(now.astimezone(_EASTERN).date()),
            "entry_client_order_id": managed.entry_client_order_id,
            "config_hash": self.loaded.config_hash,
        }
        return WheelOrderPlanV1(
            strategy_id="v13.5",
            underlying=managed.underlying,
            action=WheelAction.BUY_TO_CLOSE,
            option_symbol=managed.option_symbol,
            right=managed.right,
            strike=managed.strike,
            expiration=managed.expiration,
            limit_price=quote.ask.quantize(Decimal("0.01"), rounding=ROUND_UP),
            quote_bid=quote.bid,
            quote_ask=quote.ask,
            quote_time=quote.timestamp,
            underlying_price=managed.strike,
            trend_up=trend_up,
            collateral_required=Decimal("0"),
            maximum_loss=quote.ask * Decimal("100"),
            client_order_id=_client_order_id(self.config.runtime.client_order_prefix, semantics),
            config_hash=self.loaded.config_hash,
            created_at=now,
        )

    def _submit(
        self,
        plan: WheelOrderPlanV1,
        *,
        state: WheelRuntimeStateV1,
        account: PaperAccount,
        positions: tuple[PaperPosition, ...],
        now: datetime,
    ) -> RuntimeOutcome:
        if any(event.client_order_id == plan.client_order_id for event in self.store.events()):
            return RuntimeOutcome(
                status="NO_ACTION",
                reason_codes=("WHEEL_CLIENT_ORDER_ID_ALREADY_USED",),
                plan_hash=plan.plan_hash,
                client_order_id=plan.client_order_id,
                state_hash=state.state_hash,
            )
        open_orders = self.broker.open_orders()
        reasons = plan_violations(plan, account=account, positions=positions, open_orders=open_orders, config=self.config)
        if reasons:
            return RuntimeOutcome(status="RISK_REJECTED", reason_codes=reasons, plan_hash=plan.plan_hash, state_hash=state.state_hash)
        self.store.append(
            event_type="ORDER_PREPARED",
            occurred_at=now,
            client_order_id=plan.client_order_id,
            plan_hash=plan.plan_hash,
            detail={"plan": plan.model_dump(mode="json")},
        )
        self.store.append(
            event_type="ORDER_SUBMISSION_STARTED",
            occurred_at=now,
            client_order_id=plan.client_order_id,
            plan_hash=plan.plan_hash,
        )
        try:
            order = self.broker.submit(plan)
        except PaperSubmissionUnknown:
            self.store.append(
                event_type="ORDER_SUBMISSION_UNKNOWN",
                occurred_at=now,
                client_order_id=plan.client_order_id,
                plan_hash=plan.plan_hash,
            )
            next_state = self._advance(state, now=now, status="RECONCILE_ONLY")
            self.store.save_state(next_state)
            return RuntimeOutcome(
                status="SUBMISSION_UNKNOWN",
                reason_codes=("WHEEL_RECONCILE_BEFORE_RETRY",),
                plan_hash=plan.plan_hash,
                client_order_id=plan.client_order_id,
                state_hash=next_state.state_hash,
            )
        self.store.append(
            event_type="ORDER_ACKNOWLEDGED",
            occurred_at=now,
            client_order_id=plan.client_order_id,
            plan_hash=plan.plan_hash,
            detail={"broker_order_id": order.broker_order_id, "status": order.status},
        )
        next_state = self._advance(state, now=now, status="RECONCILE_ONLY")
        if order.status == "filled" and order.filled_quantity == 1 and order.filled_average_price is not None:
            self.store.append(
                event_type="ORDER_TERMINAL",
                occurred_at=now,
                client_order_id=plan.client_order_id,
                plan_hash=plan.plan_hash,
                detail={"status": order.status, "filled_quantity": 1, "broker_order_id": order.broker_order_id},
            )
            next_state = self._apply_fill(next_state, plan=plan, order=order, now=now)
        self.store.save_state(next_state)
        return RuntimeOutcome(
            status="ORDER_SUBMITTED",
            plan_hash=plan.plan_hash,
            client_order_id=plan.client_order_id,
            state_hash=next_state.state_hash,
        )

    def _apply_fill(self, state: WheelRuntimeStateV1, *, plan: WheelOrderPlanV1, order: PaperOrder, now: datetime) -> WheelRuntimeStateV1:
        managed = dict(state.managed_options)
        entries = dict(state.last_entry_week_by_symbol)
        if plan.action == WheelAction.BUY_TO_CLOSE:
            managed.pop(plan.underlying, None)
        else:
            managed[plan.underlying] = ManagedOptionV1(
                option_symbol=plan.option_symbol,
                underlying=plan.underlying,
                right=plan.right,
                strike=plan.strike,
                expiration=plan.expiration,
                entry_credit=order.filled_average_price or plan.limit_price,
                entry_client_order_id=plan.client_order_id,
                entry_order_id=order.broker_order_id,
                opened_at=order.updated_at,
            )
            entries[plan.underlying] = _week_key(now.astimezone(_EASTERN).date())
        return self._advance(state, now=now, status="READY", managed_options=managed, last_entry_week_by_symbol=entries)

    def _halt(self, state: WheelRuntimeStateV1, *, now: datetime, reason: str) -> RuntimeOutcome:
        halted = self._advance(state, now=now, status="HALTED", halt_reason=reason)
        self.store.save_state(halted)
        self.store.append(event_type="RUNTIME_HALTED", occurred_at=now, detail={"reason_code": reason})
        return RuntimeOutcome(status="HALTED", reason_codes=(reason,), state_hash=halted.state_hash)

    @staticmethod
    def _advance(state: WheelRuntimeStateV1, *, now: datetime, **updates: Any) -> WheelRuntimeStateV1:
        body = state.model_dump(mode="python", exclude={"state_hash"})
        body.update(updates)
        body["sequence"] = state.sequence + 1
        body["last_run_at"] = now
        return WheelRuntimeStateV1.model_validate(body)
