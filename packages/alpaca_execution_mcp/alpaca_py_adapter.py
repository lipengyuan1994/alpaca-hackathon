"""Narrow Alpaca-py paper adapter; generic trading methods are never exposed upstream."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from packages.contracts.models import (
    AccountSnapshotV1,
    BrokerEventV1,
    MarketSnapshotV1,
    OrderPlanV1,
    OrderRiskSnapshotV1,
    PositionSnapshotV1,
    ReduceOnlyOrderPlanV1,
)

from .port import PaperEndpointError


def _status(value: Any) -> str:
    raw = str(getattr(value, "value", value)).lower()
    if raw in {"new", "accepted", "pending_new", "accepted_for_bidding", "held"}:
        return "ACCEPTED"
    if raw == "partially_filled":
        return "PARTIAL"
    if raw == "filled":
        return "FILLED"
    if raw in {"canceled", "cancelled"}:
        return "CANCELLED"
    if raw == "expired":
        return "EXPIRED"
    if raw in {"rejected", "stopped", "suspended"}:
        return "REJECTED"
    return "UNKNOWN"


class AlpacaPaperExecutionAdapter:
    """Typed paper-only facade around an Alpaca ``TradingClient`` instance."""

    def __init__(
        self,
        trading_client: Any,
        *,
        expected_account_id: str,
        stock_data_client: Any | None = None,
        option_data_client: Any | None = None,
    ) -> None:
        self._client = trading_client
        self._expected_account_id = expected_account_id
        self._stock_data_client = stock_data_client
        self._option_data_client = option_data_client

    @classmethod
    def from_environment(cls) -> "AlpacaPaperExecutionAdapter":
        """Construct only from the private execution environment; no live-mode switch exists."""
        key = os.environ.get("PAPER_ALPACA_API_KEY")
        secret = os.environ.get("PAPER_ALPACA_API_SECRET")
        account_id = os.environ.get("PAPER_ACCOUNT_ID")
        base_url = os.environ.get("PAPER_API_BASE_URL", "https://paper-api.alpaca.markets")
        if not key or not secret or not account_id:
            raise PaperEndpointError("PAPER_EXECUTION_CREDENTIALS_MISSING")
        if base_url != "https://paper-api.alpaca.markets":
            raise PaperEndpointError("PAPER_EXECUTION_ORIGIN_NOT_EXACT")
        try:
            from alpaca.data.historical.option import OptionHistoricalDataClient
            from alpaca.data.historical.stock import StockHistoricalDataClient
            from alpaca.trading.client import TradingClient
        except ImportError as exc:  # pragma: no cover - deployment dependency check
            raise PaperEndpointError("ALPACA_PY_NOT_INSTALLED") from exc
        client = TradingClient(key, secret, paper=True)
        account = client.get_account()
        if str(account.id) != account_id:
            raise PaperEndpointError("PAPER_ACCOUNT_ID_MISMATCH")
        return cls(
            client,
            expected_account_id=account_id,
            stock_data_client=StockHistoricalDataClient(key, secret),
            option_data_client=OptionHistoricalDataClient(key, secret),
        )

    @staticmethod
    def _signed_position_quantity(position: Any) -> int:
        raw = Decimal(str(position.qty))
        if raw != raw.to_integral_value():
            raise ValueError("non-integral option position")
        side = str(getattr(getattr(position, "side", None), "value", position.side)).lower()
        quantity = abs(int(raw))
        return -quantity if side == "short" else quantity

    @staticmethod
    def _quote_is_current(quote: Any, *, now: datetime, ttl_seconds: int) -> bool:
        timestamp = quote.timestamp.astimezone(UTC)
        bid = Decimal(str(quote.bid_price))
        ask = Decimal(str(quote.ask_price))
        return (
            timestamp <= now
            and now - timestamp <= timedelta(seconds=ttl_seconds)
            and bid >= 0
            and ask > 0
            and ask >= bid
        )

    def runtime_state_violations(
        self,
        *,
        account: AccountSnapshotV1,
        positions: PositionSnapshotV1,
        order_risk: OrderRiskSnapshotV1,
        market: MarketSnapshotV1,
        now: datetime,
        quote_ttl_seconds: int,
    ) -> tuple[str, ...]:
        """Independently refresh credential-zone state immediately before submission."""
        now = now.astimezone(UTC)
        if self._stock_data_client is None or self._option_data_client is None:
            return ("PREFLIGHT_BROKER_DATA_CLIENTS_UNAVAILABLE",)
        try:
            from alpaca.data.enums import DataFeed, OptionsFeed
            from alpaca.data.requests import OptionLatestQuoteRequest, StockLatestQuoteRequest
            from alpaca.trading.enums import QueryOrderStatus
            from alpaca.trading.requests import GetOrdersRequest

            live_account = self._client.get_account()
            live_positions = self._client.get_all_positions()
            live_orders = self._client.get_orders(
                filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True)
            )
            clock = self._client.get_clock()
            stock_quotes = self._stock_data_client.get_stock_latest_quote(
                StockLatestQuoteRequest(
                    symbol_or_symbols=sorted(market.underlying_quotes),
                    feed=DataFeed.IEX,
                )
            )
            option_symbols = sorted({contract.symbol for contract in market.option_contracts})
            option_quotes = self._option_data_client.get_option_latest_quote(
                OptionLatestQuoteRequest(
                    symbol_or_symbols=option_symbols,
                    feed=OptionsFeed.INDICATIVE,
                )
            )
        except Exception:
            return ("PREFLIGHT_BROKER_STATE_UNAVAILABLE",)

        reasons: list[str] = []
        if str(live_account.id) != self._expected_account_id or account.account_id != self._expected_account_id:
            reasons.append("PREFLIGHT_BROKER_ACCOUNT_MISMATCH")
        expected_account_values = {
            "equity": account.equity,
            "last_equity": account.day_start_equity,
            "cash": account.cash,
            "buying_power": account.buying_power,
        }
        for field, expected in expected_account_values.items():
            live = getattr(live_account, field, None)
            if expected is None or live is None or Decimal(str(live)) != expected:
                reasons.append("PREFLIGHT_BROKER_ACCOUNT_SNAPSHOT_MISMATCH")
                break
        if any(
            bool(getattr(live_account, field, False))
            for field in ("trading_blocked", "account_blocked", "trade_suspended_by_user")
        ):
            reasons.append("PREFLIGHT_BROKER_ACCOUNT_BLOCKED")
        if int(getattr(live_account, "options_trading_level", 0) or 0) < 3:
            reasons.append("PREFLIGHT_BROKER_OPTIONS_LEVEL_INSUFFICIENT")

        try:
            actual_positions = {
                str(item.symbol): self._signed_position_quantity(item) for item in live_positions
            }
        except (ArithmeticError, TypeError, ValueError):
            actual_positions = {}
            reasons.append("PREFLIGHT_BROKER_POSITION_INVALID")
        expected_positions = {item.symbol: item.quantity for item in positions.legs}
        if actual_positions != expected_positions:
            reasons.append("PREFLIGHT_BROKER_POSITION_SNAPSHOT_MISMATCH")
        actual_working = tuple(sorted(str(item.client_order_id) for item in live_orders))
        if actual_working != tuple(sorted(order_risk.working_client_order_ids)):
            reasons.append("PREFLIGHT_BROKER_WORKING_ORDER_MISMATCH")

        clock_time = clock.timestamp.astimezone(UTC)
        if (
            not clock.is_open
            or clock_time > now
            or now - clock_time > timedelta(seconds=quote_ttl_seconds)
            or clock.next_close.astimezone(UTC) <= now
        ):
            reasons.append("PREFLIGHT_BROKER_CLOCK_INVALID")
        if set(stock_quotes) != set(market.underlying_quotes) or any(
            not self._quote_is_current(item, now=now, ttl_seconds=quote_ttl_seconds)
            for item in stock_quotes.values()
        ):
            reasons.append("PREFLIGHT_BROKER_UNDERLYING_QUOTE_INVALID")
        if set(option_quotes) != set(option_symbols) or any(
            not self._quote_is_current(item, now=now, ttl_seconds=quote_ttl_seconds)
            for item in option_quotes.values()
        ):
            reasons.append("PREFLIGHT_BROKER_OPTION_QUOTE_INVALID")
        return tuple(dict.fromkeys(reasons))

    @staticmethod
    def _request(plan: OrderPlanV1 | ReduceOnlyOrderPlanV1) -> Any:
        try:
            from alpaca.trading.enums import OrderClass, OrderSide, PositionIntent, TimeInForce
            from alpaca.trading.requests import LimitOrderRequest, OptionLegRequest
        except ImportError as exc:  # pragma: no cover - deployment dependency check
            raise PaperEndpointError("ALPACA_PY_NOT_INSTALLED") from exc

        is_close = isinstance(plan, ReduceOnlyOrderPlanV1)
        if is_close:
            raw_price = plan.limit_price if plan.price_effect == "DEBIT" else -plan.limit_price
        else:
            raw_price = plan.limit_debit
        limit_price = float(Decimal(raw_price).quantize(Decimal("0.01")))

        if len(plan.legs) == 1:
            leg = plan.legs[0]
            side = OrderSide.BUY if leg.side == "BUY" else OrderSide.SELL
            if is_close:
                intent = PositionIntent.BUY_TO_CLOSE if leg.side == "BUY" else PositionIntent.SELL_TO_CLOSE
            else:
                intent = PositionIntent.BUY_TO_OPEN if leg.side == "BUY" else PositionIntent.SELL_TO_OPEN
            return LimitOrderRequest(
                symbol=leg.symbol,
                qty=plan.quantity,
                side=side,
                time_in_force=TimeInForce.DAY,
                limit_price=abs(limit_price),
                client_order_id=plan.client_order_id,
                position_intent=intent,
            )

        option_legs = []
        for leg in plan.legs:
            side = OrderSide.BUY if leg.side == "BUY" else OrderSide.SELL
            if is_close:
                intent = PositionIntent.BUY_TO_CLOSE if leg.side == "BUY" else PositionIntent.SELL_TO_CLOSE
            else:
                intent = PositionIntent.BUY_TO_OPEN if leg.side == "BUY" else PositionIntent.SELL_TO_OPEN
            option_legs.append(
                OptionLegRequest(symbol=leg.symbol, ratio_qty=1, side=side, position_intent=intent)
            )
        return LimitOrderRequest(
            qty=plan.quantity,
            time_in_force=TimeInForce.DAY,
            order_class=OrderClass.MLEG,
            limit_price=limit_price,
            client_order_id=plan.client_order_id,
            legs=option_legs,
        )

    @staticmethod
    def _event(order: Any, *, now: datetime, fallback_client_id: str) -> BrokerEventV1:
        occurred_at = getattr(order, "updated_at", None) or getattr(order, "submitted_at", None) or now
        filled_quantity = int(Decimal(str(getattr(order, "filled_qty", 0) or 0)))
        return BrokerEventV1(
            client_order_id=str(getattr(order, "client_order_id", fallback_client_id)),
            status=_status(getattr(order, "status", "unknown")),  # type: ignore[arg-type]
            occurred_at=occurred_at.astimezone(UTC),
            broker_order_id=str(getattr(order, "id", "")) or None,
            filled_quantity=filled_quantity,
            reason_code="ALPACA_PAPER_ORDER_STATE",
        )

    def submit(
        self,
        plan: OrderPlanV1 | ReduceOnlyOrderPlanV1,
        *,
        now: datetime,
    ) -> BrokerEventV1:
        try:
            order = self._client.submit_order(order_data=self._request(plan))
        except Exception:  # Alpaca/network failure is ambiguous; reconciliation must decide.
            return BrokerEventV1(
                client_order_id=plan.client_order_id,
                status="UNKNOWN",
                occurred_at=now.astimezone(UTC),
                reason_code="ALPACA_SUBMISSION_UNKNOWN_RECONCILE_REQUIRED",
            )
        return self._event(order, now=now, fallback_client_id=plan.client_order_id)

    def reconcile(self, client_order_id: str, *, now: datetime) -> BrokerEventV1 | None:
        try:
            order = self._client.get_order_by_client_id(client_order_id)
        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            if status_code == 404:
                return None
            return BrokerEventV1(
                client_order_id=client_order_id,
                status="UNKNOWN",
                occurred_at=now.astimezone(UTC),
                reason_code="ALPACA_RECONCILIATION_UNKNOWN",
            )
        return self._event(order, now=now, fallback_client_id=client_order_id)

    def cancel(self, client_order_id: str, *, now: datetime) -> BrokerEventV1:
        existing = self.reconcile(client_order_id, now=now)
        if existing is None or existing.broker_order_id is None:
            return BrokerEventV1(
                client_order_id=client_order_id,
                status="UNKNOWN",
                occurred_at=now.astimezone(UTC),
                reason_code="ALPACA_CANCEL_ORDER_NOT_FOUND",
            )
        try:
            self._client.cancel_order_by_id(existing.broker_order_id)
        except Exception:
            return BrokerEventV1(
                client_order_id=client_order_id,
                status="UNKNOWN",
                occurred_at=now.astimezone(UTC),
                broker_order_id=existing.broker_order_id,
                reason_code="ALPACA_CANCEL_UNKNOWN_RECONCILE_REQUIRED",
            )
        return self.reconcile(client_order_id, now=now) or BrokerEventV1(
            client_order_id=client_order_id,
            status="UNKNOWN",
            occurred_at=now.astimezone(UTC),
            broker_order_id=existing.broker_order_id,
            reason_code="ALPACA_CANCEL_RECONCILIATION_REQUIRED",
        )
