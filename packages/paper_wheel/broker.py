"""Narrow paper-broker protocol and Alpaca implementation for the wheel runtime."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from packages.runtime_secrets import SecretConfigurationError, require_yaml_file_secret

from .models import WheelAction, WheelOrderPlanV1

_PAPER_ORIGIN = "https://paper-api.alpaca.markets"
_DEFAULT_SECRETS_ROOT = Path("/Users/lipengyuan/.config/great_secrets")


class PaperBrokerError(RuntimeError):
    pass


class PaperSubmissionUnknown(PaperBrokerError):
    pass


@dataclass(frozen=True)
class PaperAccount:
    account_id: str
    equity: Decimal
    day_start_equity: Decimal
    cash: Decimal
    buying_power: Decimal
    options_buying_power: Decimal
    options_trading_level: int
    trading_blocked: bool
    account_blocked: bool
    trade_suspended_by_user: bool


@dataclass(frozen=True)
class PaperClock:
    is_open: bool
    timestamp: datetime
    next_open: datetime
    next_close: datetime


@dataclass(frozen=True)
class PaperPosition:
    symbol: str
    asset_class: str
    quantity: int
    quantity_available: int
    average_entry_price: Decimal


@dataclass(frozen=True)
class PaperOrder:
    client_order_id: str
    broker_order_id: str
    symbol: str
    side: str
    position_intent: str | None
    quantity: int
    filled_quantity: int
    status: str
    filled_average_price: Decimal | None
    submitted_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class PaperQuote:
    bid: Decimal
    ask: Decimal
    timestamp: datetime


@dataclass(frozen=True)
class PaperOptionContract:
    symbol: str
    underlying: str
    right: str
    strike: Decimal
    expiration: date
    quote: PaperQuote


@dataclass(frozen=True)
class PaperOptionLifecycleActivity:
    activity_id: str
    activity_type: str
    symbol: str
    quantity: int
    activity_date: date
    status: str


class PaperWheelBroker(Protocol):
    @property
    def expected_account_id(self) -> str: ...

    def account(self) -> PaperAccount: ...

    def clock(self) -> PaperClock: ...

    def positions(self) -> tuple[PaperPosition, ...]: ...

    def open_orders(self) -> tuple[PaperOrder, ...]: ...

    def order_by_client_id(self, client_order_id: str) -> PaperOrder | None: ...

    def completed_daily_closes(self, symbol: str, *, now: datetime, sessions: int) -> tuple[Decimal, ...]: ...

    def underlying_quote(self, symbol: str) -> PaperQuote: ...

    def option_candidates(
        self,
        symbol: str,
        *,
        right: str,
        minimum_expiration: date,
        maximum_expiration: date,
        minimum_strike: Decimal,
        maximum_strike: Decimal,
    ) -> tuple[PaperOptionContract, ...]: ...

    def option_quote(self, symbol: str) -> PaperQuote: ...

    def calendar_open_dates(self, start: date, end: date) -> tuple[date, ...]: ...

    def option_lifecycle_activities(
        self,
        option_symbol: str,
        *,
        after: datetime,
    ) -> tuple[PaperOptionLifecycleActivity, ...]: ...

    def cancel_order(self, broker_order_id: str) -> None: ...

    def submit(self, plan: WheelOrderPlanV1) -> PaperOrder: ...


def _enum_text(value: Any) -> str:
    return str(getattr(value, "value", value)).lower()


def _integer(value: Any, *, field: str) -> int:
    number = Decimal(str(value or 0))
    if number != number.to_integral_value():
        raise PaperBrokerError(f"PAPER_BROKER_{field}_NOT_INTEGRAL")
    return int(number)


class AlpacaPaperWheelBroker:
    """Paper-only Alpaca facade with no live-origin or generic mutation switch."""

    def __init__(
        self,
        *,
        trading_client: Any,
        stock_data_client: Any,
        option_data_client: Any,
        expected_account_id: str,
        api_key: str | None = None,
        api_secret: str | None = None,
    ) -> None:
        self._trading = trading_client
        self._stock_data = stock_data_client
        self._option_data = option_data_client
        self._expected_account_id = expected_account_id
        self._api_key = api_key
        self._api_secret = api_secret

    @property
    def expected_account_id(self) -> str:
        return self._expected_account_id

    @classmethod
    def from_environment(cls) -> "AlpacaPaperWheelBroker":
        root = Path(os.environ.get("REGIMESWITCH_SECRETS_DIR", str(_DEFAULT_SECRETS_ROOT))).expanduser()
        bundle = root / "alpaca" / "alpaca_api_key.yaml"
        secret_environment = {"PAPER_ALPACA_BUNDLE_FILE": str(bundle)}
        try:
            key = require_yaml_file_secret(
                "PAPER_ALPACA_BUNDLE",
                key_path=("paper_alpaca_api_key",),
                environ=secret_environment,
                allowed_roots=(root,),
            )
            secret = require_yaml_file_secret(
                "PAPER_ALPACA_BUNDLE",
                key_path=("paper_alpaca_api_secret",),
                environ=secret_environment,
                allowed_roots=(root,),
            )
            account_id = require_yaml_file_secret(
                "PAPER_ALPACA_BUNDLE",
                key_path=("paper_account_id",),
                environ=secret_environment,
                allowed_roots=(root,),
            )
        except SecretConfigurationError as exc:
            raise PaperBrokerError("PAPER_EXECUTION_CREDENTIALS_MISSING") from exc
        if os.environ.get("PAPER_API_BASE_URL", _PAPER_ORIGIN) != _PAPER_ORIGIN:
            raise PaperBrokerError("PAPER_EXECUTION_ORIGIN_NOT_EXACT")
        try:
            from alpaca.data.historical.option import OptionHistoricalDataClient
            from alpaca.data.historical.stock import StockHistoricalDataClient
            from alpaca.trading.client import TradingClient
        except ImportError as exc:  # pragma: no cover
            raise PaperBrokerError("ALPACA_PY_NOT_INSTALLED") from exc
        trading = TradingClient(key, secret, paper=True)
        try:
            live_account = trading.get_account()
        except Exception as exc:
            raise PaperBrokerError("PAPER_ACCOUNT_PREFLIGHT_UNAVAILABLE") from exc
        if str(live_account.id) != account_id:
            raise PaperBrokerError("PAPER_ACCOUNT_ID_MISMATCH")
        return cls(
            trading_client=trading,
            stock_data_client=StockHistoricalDataClient(key, secret),
            option_data_client=OptionHistoricalDataClient(key, secret),
            expected_account_id=account_id,
            api_key=key,
            api_secret=secret,
        )

    def account(self) -> PaperAccount:
        try:
            item = self._trading.get_account()
        except Exception as exc:
            raise PaperBrokerError("PAPER_ACCOUNT_SNAPSHOT_UNAVAILABLE") from exc
        account_id = str(item.id)
        if account_id != self._expected_account_id:
            raise PaperBrokerError("PAPER_ACCOUNT_ID_MISMATCH")
        return PaperAccount(
            account_id=account_id,
            equity=Decimal(str(item.equity)),
            day_start_equity=Decimal(str(item.last_equity)),
            cash=Decimal(str(item.cash)),
            buying_power=Decimal(str(item.buying_power)),
            options_buying_power=Decimal(str(getattr(item, "options_buying_power", item.buying_power))),
            options_trading_level=int(getattr(item, "options_trading_level", 0) or 0),
            trading_blocked=bool(getattr(item, "trading_blocked", False)),
            account_blocked=bool(getattr(item, "account_blocked", False)),
            trade_suspended_by_user=bool(getattr(item, "trade_suspended_by_user", False)),
        )

    def clock(self) -> PaperClock:
        try:
            item = self._trading.get_clock()
        except Exception as exc:
            raise PaperBrokerError("PAPER_CLOCK_UNAVAILABLE") from exc
        return PaperClock(
            is_open=bool(item.is_open),
            timestamp=item.timestamp.astimezone(UTC),
            next_open=item.next_open.astimezone(UTC),
            next_close=item.next_close.astimezone(UTC),
        )

    @staticmethod
    def _position(item: Any) -> PaperPosition:
        raw_quantity = _integer(item.qty, field="POSITION_QUANTITY")
        side = _enum_text(item.side)
        signed = -abs(raw_quantity) if side == "short" else abs(raw_quantity)
        available = getattr(item, "qty_available", item.qty)
        available_integer = _integer(available, field="POSITION_AVAILABLE_QUANTITY")
        if side == "short":
            available_integer = -abs(available_integer)
        return PaperPosition(
            symbol=str(item.symbol).upper(),
            asset_class=_enum_text(item.asset_class),
            quantity=signed,
            quantity_available=available_integer,
            average_entry_price=Decimal(str(item.avg_entry_price)),
        )

    def positions(self) -> tuple[PaperPosition, ...]:
        try:
            items = self._trading.get_all_positions()
        except Exception as exc:
            raise PaperBrokerError("PAPER_POSITIONS_UNAVAILABLE") from exc
        return tuple(sorted((self._position(item) for item in items), key=lambda row: row.symbol))

    @staticmethod
    def _order(item: Any) -> PaperOrder:
        submitted = getattr(item, "submitted_at", None) or getattr(item, "created_at", None)
        updated = getattr(item, "updated_at", None) or submitted
        if submitted is None or updated is None:
            raise PaperBrokerError("PAPER_ORDER_TIMESTAMP_MISSING")
        average = getattr(item, "filled_avg_price", None)
        return PaperOrder(
            client_order_id=str(item.client_order_id),
            broker_order_id=str(item.id),
            symbol=str(getattr(item, "symbol", "") or "").upper(),
            side=_enum_text(getattr(item, "side", "")),
            position_intent=(
                None
                if getattr(item, "position_intent", None) is None
                else _enum_text(item.position_intent)
            ),
            quantity=_integer(getattr(item, "qty", 0), field="ORDER_QUANTITY"),
            filled_quantity=_integer(getattr(item, "filled_qty", 0), field="ORDER_FILLED_QUANTITY"),
            status=_enum_text(item.status),
            filled_average_price=None if average is None else Decimal(str(average)),
            submitted_at=submitted.astimezone(UTC),
            updated_at=updated.astimezone(UTC),
        )

    def open_orders(self) -> tuple[PaperOrder, ...]:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        try:
            items = self._trading.get_orders(
                filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True, limit=500)
            )
        except Exception as exc:
            raise PaperBrokerError("PAPER_OPEN_ORDERS_UNAVAILABLE") from exc
        return tuple(sorted((self._order(item) for item in items), key=lambda row: row.client_order_id))

    def order_by_client_id(self, client_order_id: str) -> PaperOrder | None:
        try:
            return self._order(self._trading.get_order_by_client_id(client_order_id))
        except Exception as exc:
            if getattr(exc, "status_code", None) == 404:
                return None
            raise PaperBrokerError("PAPER_ORDER_RECONCILIATION_UNAVAILABLE") from exc

    def completed_daily_closes(self, symbol: str, *, now: datetime, sessions: int) -> tuple[Decimal, ...]:
        from alpaca.data.enums import Adjustment, DataFeed
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        request = StockBarsRequest(
            symbol_or_symbols=[symbol],
            timeframe=TimeFrame.Day,
            start=now.astimezone(UTC) - timedelta(days=max(180, sessions * 3)),
            end=now.astimezone(UTC),
            feed=DataFeed.IEX,
            adjustment=Adjustment.SPLIT,
        )
        try:
            bar_set = self._stock_data.get_stock_bars(request)
        except Exception as exc:
            raise PaperBrokerError("PAPER_DAILY_BARS_UNAVAILABLE") from exc
        bars = getattr(bar_set, "data", bar_set).get(symbol, [])
        today = now.astimezone(ZoneInfo("America/New_York")).date()
        completed = [bar for bar in bars if bar.timestamp.astimezone(ZoneInfo("America/New_York")).date() < today]
        return tuple(Decimal(str(bar.close)) for bar in completed[-sessions:])

    @staticmethod
    def _quote(item: Any) -> PaperQuote:
        return PaperQuote(
            bid=Decimal(str(item.bid_price)),
            ask=Decimal(str(item.ask_price)),
            timestamp=item.timestamp.astimezone(UTC),
        )

    def underlying_quote(self, symbol: str) -> PaperQuote:
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockLatestQuoteRequest

        try:
            values = self._stock_data.get_stock_latest_quote(
                StockLatestQuoteRequest(symbol_or_symbols=[symbol], feed=DataFeed.IEX)
            )
        except Exception as exc:
            raise PaperBrokerError("PAPER_UNDERLYING_QUOTE_UNAVAILABLE") from exc
        if symbol not in values:
            raise PaperBrokerError("PAPER_UNDERLYING_QUOTE_MISSING")
        return self._quote(values[symbol])

    def option_quote(self, symbol: str) -> PaperQuote:
        from alpaca.data.enums import OptionsFeed
        from alpaca.data.requests import OptionLatestQuoteRequest

        try:
            values = self._option_data.get_option_latest_quote(
                OptionLatestQuoteRequest(symbol_or_symbols=[symbol], feed=OptionsFeed.INDICATIVE)
            )
        except Exception as exc:
            raise PaperBrokerError("PAPER_OPTION_QUOTE_UNAVAILABLE") from exc
        if symbol not in values:
            raise PaperBrokerError("PAPER_OPTION_QUOTE_MISSING")
        return self._quote(values[symbol])

    def calendar_open_dates(self, start: date, end: date) -> tuple[date, ...]:
        from alpaca.trading.requests import GetCalendarRequest

        try:
            rows = self._trading.get_calendar(filters=GetCalendarRequest(start=start, end=end))
        except Exception as exc:
            raise PaperBrokerError("PAPER_CALENDAR_UNAVAILABLE") from exc
        return tuple(sorted(item.date for item in rows))

    def option_lifecycle_activities(
        self,
        option_symbol: str,
        *,
        after: datetime,
    ) -> tuple[PaperOptionLifecycleActivity, ...]:
        """Read exact assignment/expiration evidence from Alpaca paper activities."""
        if self._api_key is None or self._api_secret is None:
            raise PaperBrokerError("PAPER_LIFECYCLE_ACTIVITY_CREDENTIALS_MISSING")
        collected: dict[str, PaperOptionLifecycleActivity] = {}
        for activity_type in ("OPASN", "OPEXP"):
            page_token: str | None = None
            for _ in range(20):
                parameters = {
                    "after": after.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                    "direction": "desc",
                    "page_size": "100",
                }
                if page_token is not None:
                    parameters["page_token"] = page_token
                url = f"{_PAPER_ORIGIN}/v2/account/activities/{activity_type}?{urlencode(parameters)}"
                request = Request(
                    url,
                    headers={
                        "APCA-API-KEY-ID": self._api_key,
                        "APCA-API-SECRET-KEY": self._api_secret,
                        "Accept": "application/json",
                    },
                    method="GET",
                )
                try:
                    with urlopen(request, timeout=10) as response:  # noqa: S310 - fixed HTTPS paper origin
                        rows = json.loads(response.read().decode("utf-8"))
                except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
                    raise PaperBrokerError("PAPER_LIFECYCLE_ACTIVITY_UNAVAILABLE") from exc
                if not isinstance(rows, list):
                    raise PaperBrokerError("PAPER_LIFECYCLE_ACTIVITY_INVALID")
                for row in rows:
                    if not isinstance(row, dict) or str(row.get("symbol", "")).upper() != option_symbol:
                        continue
                    activity_id = str(row.get("id", ""))
                    activity_date_text = str(row.get("date", ""))[:10]
                    if not activity_id or not activity_date_text:
                        raise PaperBrokerError("PAPER_LIFECYCLE_ACTIVITY_INVALID")
                    try:
                        activity_date = date.fromisoformat(activity_date_text)
                    except ValueError as exc:
                        raise PaperBrokerError("PAPER_LIFECYCLE_ACTIVITY_INVALID") from exc
                    activity = PaperOptionLifecycleActivity(
                        activity_id=activity_id,
                        activity_type=activity_type,
                        symbol=option_symbol,
                        quantity=_integer(row.get("qty", 0), field="ACTIVITY_QUANTITY"),
                        activity_date=activity_date,
                        status=str(row.get("status", "")).lower(),
                    )
                    collected[activity_id] = activity
                if len(rows) < 100:
                    break
                last_id = str(rows[-1].get("id", "")) if isinstance(rows[-1], dict) else ""
                if not last_id or last_id == page_token:
                    raise PaperBrokerError("PAPER_LIFECYCLE_ACTIVITY_PAGINATION_INVALID")
                page_token = last_id
            else:
                raise PaperBrokerError("PAPER_LIFECYCLE_ACTIVITY_PAGINATION_LIMIT")
        return tuple(
            sorted(
                collected.values(),
                key=lambda item: (item.activity_date, item.activity_type, item.activity_id),
            )
        )

    def cancel_order(self, broker_order_id: str) -> None:
        try:
            self._trading.cancel_order_by_id(broker_order_id)
        except Exception as exc:
            raise PaperBrokerError("PAPER_ORDER_CANCEL_UNKNOWN") from exc

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
        from alpaca.data.enums import OptionsFeed
        from alpaca.data.requests import OptionLatestQuoteRequest
        from alpaca.trading.enums import ContractType, ExerciseStyle
        from alpaca.trading.requests import GetOptionContractsRequest

        contract_type = ContractType.PUT if right == "PUT" else ContractType.CALL
        page_token: str | None = None
        contracts: list[Any] = []
        while True:
            try:
                response = self._trading.get_option_contracts(
                    GetOptionContractsRequest(
                        underlying_symbols=[symbol],
                        expiration_date_gte=minimum_expiration,
                        expiration_date_lte=maximum_expiration,
                        type=contract_type,
                        style=ExerciseStyle.AMERICAN,
                        strike_price_gte=format(minimum_strike, "f"),
                        strike_price_lte=format(maximum_strike, "f"),
                        limit=1000,
                        page_token=page_token,
                    )
                )
            except Exception as exc:
                raise PaperBrokerError("PAPER_OPTION_CONTRACTS_UNAVAILABLE") from exc
            page = getattr(response, "option_contracts", response.get("option_contracts", []) if isinstance(response, dict) else [])
            contracts.extend(page)
            page_token = getattr(response, "next_page_token", None)
            if page_token is None and isinstance(response, dict):
                page_token = response.get("next_page_token")
            if not page_token:
                break
        tradable = [item for item in contracts if bool(getattr(item, "tradable", False))]
        symbols = sorted(str(item.symbol) for item in tradable)
        if not symbols:
            return ()
        quotes: dict[str, Any] = {}
        for start in range(0, len(symbols), 100):
            batch = symbols[start : start + 100]
            try:
                response = self._option_data.get_option_latest_quote(
                    OptionLatestQuoteRequest(
                        symbol_or_symbols=batch,
                        feed=OptionsFeed.INDICATIVE,
                    )
                )
            except Exception as exc:
                raise PaperBrokerError("PAPER_OPTION_QUOTES_UNAVAILABLE") from exc
            quotes.update(response)
        results = []
        for item in tradable:
            option_symbol = str(item.symbol)
            if option_symbol not in quotes:
                continue
            results.append(
                PaperOptionContract(
                    symbol=option_symbol,
                    underlying=symbol,
                    right=right,
                    strike=Decimal(str(item.strike_price)),
                    expiration=item.expiration_date,
                    quote=self._quote(quotes[option_symbol]),
                )
            )
        return tuple(sorted(results, key=lambda item: (item.expiration, item.strike, item.symbol)))

    def submit(self, plan: WheelOrderPlanV1) -> PaperOrder:
        from alpaca.trading.enums import OrderSide, PositionIntent, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest

        closing = plan.action == WheelAction.BUY_TO_CLOSE
        request = LimitOrderRequest(
            symbol=plan.option_symbol,
            qty=plan.quantity,
            side=OrderSide.BUY if closing else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            limit_price=float(plan.limit_price.quantize(Decimal("0.01"))),
            client_order_id=plan.client_order_id,
            position_intent=PositionIntent.BUY_TO_CLOSE if closing else PositionIntent.SELL_TO_OPEN,
        )
        try:
            return self._order(self._trading.submit_order(order_data=request))
        except Exception as exc:
            raise PaperSubmissionUnknown("PAPER_ORDER_SUBMISSION_UNKNOWN") from exc
