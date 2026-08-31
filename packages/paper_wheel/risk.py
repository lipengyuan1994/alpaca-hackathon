"""Pure fail-closed risk gates for fully collateralized paper-wheel orders."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from .broker import PaperAccount, PaperClock, PaperOrder, PaperPosition, PaperQuote
from .config import WheelPaperConfig
from .models import WheelAction, WheelOrderPlanV1, WheelRuntimeStateV1


def quote_violations(
    quote: PaperQuote,
    *,
    now: datetime,
    maximum_age_seconds: int,
    maximum_future_skew_seconds: int,
    maximum_relative_spread: Decimal,
    minimum_bid: Decimal,
) -> tuple[str, ...]:
    reasons: list[str] = []
    normalized_now = now.astimezone(UTC)
    if quote.timestamp > normalized_now + timedelta(seconds=maximum_future_skew_seconds) or (
        normalized_now - quote.timestamp > timedelta(seconds=maximum_age_seconds)
    ):
        reasons.append("WHEEL_QUOTE_STALE")
    if quote.bid < minimum_bid or quote.ask <= 0 or quote.ask < quote.bid:
        reasons.append("WHEEL_QUOTE_INVALID")
    midpoint = (quote.bid + quote.ask) / Decimal("2")
    if midpoint <= 0 or (quote.ask - quote.bid) / midpoint > maximum_relative_spread:
        reasons.append("WHEEL_QUOTE_SPREAD_TOO_WIDE")
    return tuple(reasons)


def account_violations(account: PaperAccount, clock: PaperClock, *, now: datetime, config: WheelPaperConfig) -> tuple[str, ...]:
    reasons: list[str] = []
    if account.trading_blocked or account.account_blocked or account.trade_suspended_by_user:
        reasons.append("WHEEL_PAPER_ACCOUNT_BLOCKED")
    if account.options_trading_level < 1:
        reasons.append("WHEEL_OPTIONS_LEVEL_INSUFFICIENT")
    if account.day_start_equity <= 0:
        reasons.append("WHEEL_DAY_START_EQUITY_INVALID")
    else:
        drawdown = (account.equity - account.day_start_equity) / account.day_start_equity
        if drawdown <= -config.risk.maximum_daily_drawdown_fraction:
            reasons.append("WHEEL_DAILY_DRAWDOWN_LIMIT")
    observed = clock.timestamp.astimezone(UTC)
    normalized_now = now.astimezone(UTC)
    if observed > normalized_now + timedelta(seconds=config.risk.maximum_clock_skew_seconds) or (
        normalized_now - observed > timedelta(seconds=config.risk.maximum_quote_age_seconds)
    ):
        reasons.append("WHEEL_BROKER_CLOCK_STALE")
    return tuple(reasons)


def broker_shape_violations(
    *,
    positions: tuple[PaperPosition, ...],
    orders: tuple[PaperOrder, ...],
    config: WheelPaperConfig,
) -> tuple[str, ...]:
    reasons: list[str] = []
    allowed = set(config.strategy.symbols)
    if not config.risk.allow_unmanaged_positions:
        for position in positions:
            related = position.symbol in allowed or any(position.symbol.startswith(symbol) for symbol in allowed)
            if not related:
                reasons.append("WHEEL_UNMANAGED_POSITION_PRESENT")
                break
    if not config.risk.allow_unmanaged_open_orders:
        if any(not order.client_order_id.startswith(config.runtime.client_order_prefix) for order in orders):
            reasons.append("WHEEL_UNMANAGED_OPEN_ORDER_PRESENT")
    return tuple(dict.fromkeys(reasons))


def runtime_position_violations(
    *,
    state: WheelRuntimeStateV1,
    positions: tuple[PaperPosition, ...],
    config: WheelPaperConfig,
) -> tuple[str, ...]:
    """Bind allowed account positions to the persisted wheel lifecycle state."""
    reasons: list[str] = []
    managed_symbols = {item.option_symbol for item in state.managed_options.values()}
    strategy_symbols = set(config.strategy.symbols)
    for position in positions:
        if position.asset_class == "us_option" and any(
            position.symbol.startswith(symbol) for symbol in strategy_symbols
        ):
            if position.symbol not in managed_symbols:
                reasons.append("WHEEL_UNMANAGED_OPTION_POSITION")
            elif position.quantity != -1:
                reasons.append("WHEEL_MANAGED_OPTION_QUANTITY_DRIFT")
        elif position.symbol in strategy_symbols and position.quantity not in {0, 100}:
            reasons.append("WHEEL_UNDERLYING_SHARE_QUANTITY_INVALID")
    for underlying, managed in state.managed_options.items():
        option = next((item for item in positions if item.symbol == managed.option_symbol), None)
        if option is None:
            reasons.append("WHEEL_MANAGED_OPTION_POSITION_MISSING")
            continue
        equity = next((item for item in positions if item.symbol == underlying), None)
        shares = 0 if equity is None else equity.quantity
        expected_shares = 0 if managed.right == "PUT" else 100
        if shares != expected_shares:
            reasons.append("WHEEL_MANAGED_COLLATERAL_SHAPE_INVALID")
    return tuple(dict.fromkeys(reasons))


def plan_violations(
    plan: WheelOrderPlanV1,
    *,
    account: PaperAccount,
    positions: tuple[PaperPosition, ...],
    open_orders: tuple[PaperOrder, ...],
    config: WheelPaperConfig,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if plan.underlying not in config.risk.allowed_symbols or plan.underlying not in config.strategy.symbols:
        reasons.append("WHEEL_PLAN_SYMBOL_NOT_ALLOWED")
    if plan.quantity != 1 or open_orders:
        reasons.append("WHEEL_PLAN_CONCURRENCY_LIMIT")
    underlying = next((item for item in positions if item.symbol == plan.underlying), None)
    option = next((item for item in positions if item.symbol == plan.option_symbol), None)
    if plan.action == WheelAction.SELL_CASH_SECURED_PUT:
        if underlying is not None and underlying.quantity != 0:
            reasons.append("WHEEL_CSP_REQUIRES_NO_UNDERLYING_POSITION")
        required = plan.strike * Decimal("100")
        if required > config.risk.max_assignment_notional_usd:
            reasons.append("WHEEL_ASSIGNMENT_NOTIONAL_LIMIT")
        if account.cash - required < config.risk.minimum_unreserved_cash_usd:
            reasons.append("WHEEL_CSP_CASH_BUFFER_INSUFFICIENT")
        if account.options_buying_power < required:
            reasons.append("WHEEL_CSP_OPTIONS_BUYING_POWER_INSUFFICIENT")
    elif plan.action == WheelAction.SELL_COVERED_CALL:
        if underlying is None or underlying.quantity != 100 or underlying.quantity_available < 100:
            reasons.append("WHEEL_CC_SHARES_NOT_AVAILABLE")
    elif plan.action == WheelAction.BUY_TO_CLOSE:
        if option is None or option.quantity != -1 or option.quantity_available > -1:
            reasons.append("WHEEL_CLOSE_POSITION_MISMATCH")
    else:  # pragma: no cover - enum exhaustiveness
        reasons.append("WHEEL_PLAN_ACTION_UNSUPPORTED")
    return tuple(dict.fromkeys(reasons))
