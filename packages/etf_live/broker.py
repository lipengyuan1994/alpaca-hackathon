"""Small, allowlisted Alpaca production client for the ETF runtime.

The client exposes only account, market-data, order and reconciliation calls
needed by this strategy.  It has no transfer, options, withdrawal or blanket
liquidation methods.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx

from packages.runtime_secrets import require_file_secret


class BrokerError(RuntimeError):
    pass


class BrokerSubmissionUnknown(BrokerError):
    pass


class HTTPTransport(Protocol):
    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response: ...


@dataclass(frozen=True)
class AccountSnapshot:
    account_id: str
    status: str
    cash: Decimal
    buying_power: Decimal
    non_marginable_buying_power: Decimal | None
    equity: Decimal
    multiplier: Decimal
    trading_blocked: bool
    account_blocked: bool
    trade_suspended_by_user: bool


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: Decimal
    ask: Decimal
    timestamp: datetime

    @property
    def midpoint(self) -> Decimal:
        return (self.bid + self.ask) / Decimal("2")


class AlpacaLiveBroker:
    TRADING_ORIGIN = "https://api.alpaca.markets"
    DATA_ORIGIN = "https://data.alpaca.markets"

    def __init__(self, *, api_key: str, api_secret: str, account_id: str, transport: HTTPTransport | None = None, trading_origin: str = TRADING_ORIGIN, data_origin: str = DATA_ORIGIN) -> None:
        if trading_origin != self.TRADING_ORIGIN or data_origin != self.DATA_ORIGIN:
            raise ValueError("ETF_LIVE_ORIGIN_NOT_ALLOWLISTED")
        if not api_key.strip() or not api_secret.strip():
            raise ValueError("ETF_LIVE_CREDENTIALS_EMPTY")
        self.account_id = account_id
        self.trading_origin = trading_origin
        self.data_origin = data_origin
        self._client = transport or httpx.Client(timeout=httpx.Timeout(12.0, connect=5.0))
        self._headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret, "Accept": "application/json"}

    @classmethod
    def from_environment(cls, *, account_id: str, secrets_root: Path = Path("/run/etf-live-secrets")) -> "AlpacaLiveBroker":
        environ = os.environ
        key = require_file_secret("ALPACA_API_KEY", environ=environ, allowed_roots=(secrets_root,))
        secret = require_file_secret("ALPACA_API_SECRET", environ=environ, allowed_roots=(secrets_root,))
        return cls(api_key=key, api_secret=secret, account_id=account_id)

    def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        try:
            response = self._client.request(method, url, headers=self._headers, **kwargs)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise BrokerError("ETF_LIVE_BROKER_UNAVAILABLE") from exc
        if response.status_code >= 400:
            detail = ""
            try:
                body = response.json()
                detail = str(body.get("code") or body.get("message") or "")[:120]
            except (ValueError, json.JSONDecodeError):
                detail = ""
            raise BrokerError(f"ETF_LIVE_BROKER_HTTP_{response.status_code}:{detail}")
        try:
            return response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise BrokerError("ETF_LIVE_BROKER_INVALID_JSON") from exc

    def account(self) -> AccountSnapshot:
        data = self._request("GET", f"{self.trading_origin}/v2/account")
        actual = str(data.get("id") or "")
        if actual != self.account_id:
            raise BrokerError("ETF_LIVE_ACCOUNT_ID_MISMATCH")
        return AccountSnapshot(actual, str(data.get("status", "")), Decimal(str(data.get("cash", "0"))), Decimal(str(data.get("buying_power", "0"))), None if data.get("non_marginable_buying_power") is None else Decimal(str(data["non_marginable_buying_power"])), Decimal(str(data.get("equity", "0"))), Decimal(str(data.get("multiplier", "1"))), bool(data.get("trading_blocked")), bool(data.get("account_blocked")), bool(data.get("trade_suspended_by_user")))

    def clock(self) -> dict[str, Any]:
        return self._request("GET", f"{self.trading_origin}/v2/clock")

    def positions(self) -> list[dict[str, Any]]:
        return list(self._request("GET", f"{self.trading_origin}/v2/positions"))

    def open_orders(self) -> list[dict[str, Any]]:
        return list(self._request("GET", f"{self.trading_origin}/v2/orders?{urlencode({'status': 'open', 'nested': 'false', 'direction': 'asc'})}"))

    def order_by_client_id(self, client_order_id: str) -> dict[str, Any] | None:
        try:
            return self._request("GET", f"{self.trading_origin}/v2/orders:by_client_order_id?{urlencode({'client_order_id': client_order_id})}")
        except BrokerError as exc:
            if "HTTP_404" in str(exc):
                return None
            raise

    def order_by_id(self, order_id: str) -> dict[str, Any] | None:
        try:
            return self._request("GET", f"{self.trading_origin}/v2/orders/{order_id}")
        except BrokerError as exc:
            if "HTTP_404" in str(exc):
                return None
            raise

    def order_fill_activities(self, order_id: str, *, page_size: int = 100) -> list[dict[str, Any]]:
        """Read the individual FILL activities for one order, in time order.

        The Trading API exposes order-associated fills through
        ``GET /v2/account/activities/FILL``.  Activity IDs are durable event
        identities; callers should deduplicate by ``id`` rather than infer
        incremental executions from a changing order-level average price.
        """

        if not str(order_id).strip():
            raise ValueError("ETF_LIVE_FILL_ORDER_ID_REQUIRED")
        size = min(max(int(page_size), 1), 100)
        page_token: str | None = None
        output: list[dict[str, Any]] = []
        while True:
            params: dict[str, Any] = {
                "order_id": str(order_id),
                "direction": "asc",
                "page_size": size,
            }
            if page_token:
                params["page_token"] = page_token
            url = f"{self.trading_origin}/v2/account/activities/FILL?{urlencode(params)}"
            payload = self._request("GET", url)
            if isinstance(payload, list):
                records = payload
            elif isinstance(payload, dict):
                records = payload.get("activities", payload.get("data", []))
            else:
                raise BrokerError("ETF_LIVE_FILL_ACTIVITIES_INVALID")
            if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
                raise BrokerError("ETF_LIVE_FILL_ACTIVITIES_INVALID")
            page = [dict(item) for item in records]
            output.extend(page)
            if len(page) < size:
                break
            last_id = page[-1].get("id")
            if last_id is None or str(last_id) == page_token:
                raise BrokerError("ETF_LIVE_FILL_ACTIVITY_PAGINATION_INVALID")
            page_token = str(last_id)
        return output

    def asset(self, symbol: str) -> dict[str, Any]:
        return self._request("GET", f"{self.trading_origin}/v2/assets/{symbol}")

    def latest_quotes(self, symbols: tuple[str, ...]) -> dict[str, Quote]:
        params = urlencode({"symbols": ",".join(symbols), "feed": "sip"})
        data = self._request("GET", f"{self.data_origin}/v2/stocks/quotes/latest?{params}")
        output: dict[str, Quote] = {}
        for symbol, row in dict(data.get("quotes", data)).items():
            timestamp = datetime.fromisoformat(str(row["t"]).replace("Z", "+00:00")).astimezone(UTC)
            output[symbol] = Quote(symbol, Decimal(str(row["bp"])), Decimal(str(row["ap"])), timestamp)
        return output

    def daily_bars(
        self,
        symbols: tuple[str, ...],
        *,
        start: datetime,
        end: datetime,
        limit: int = 1000,
        adjustment: str = "raw",
    ) -> dict[str, list[dict[str, Any]]]:
        """Read paginated SIP daily bars with an explicit adjustment mode.

        Executable prices must be raw.  Signal callers may request a provider
        adjusted series explicitly, but the mode is always visible in the
        request and never silently shared with sizing.
        """

        output: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in symbols}
        page_token: str | None = None
        while True:
            query: dict[str, Any] = {
                "symbols": ",".join(symbols),
                "timeframe": "1Day",
                "start": start.astimezone(UTC).isoformat(),
                "end": end.astimezone(UTC).isoformat(),
                "limit": min(max(limit, 1), 10000),
                "feed": "sip",
                "adjustment": adjustment,
                "sort": "asc",
            }
            if page_token:
                query["page_token"] = page_token
            params = urlencode(query)
            data = self._request("GET", f"{self.data_origin}/v2/stocks/bars?{params}")
            bars = dict(data.get("bars", data))
            for symbol in symbols:
                output.setdefault(symbol, []).extend(list(bars.get(symbol, [])))
            page_token = data.get("next_page_token")
            if not page_token:
                break
        for symbol in output:
            output[symbol].sort(key=lambda row: str(row.get("t", row.get("timestamp", row.get("date", "")))))
        return output

    def corporate_actions(self, symbols: tuple[str, ...], *, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """Read the immutable market-data corporate-action records.

        The runtime uses these rows only to build causal signal features; raw
        TECL bars remain the sole source for executable prices.  Unknown or
        missing action rows are handled by the caller as a fail-closed data
        condition.
        """

        output: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            query: dict[str, Any] = {
                "symbols": ",".join(symbols),
                "start": start.astimezone(UTC).date().isoformat(),
                "end": end.astimezone(UTC).date().isoformat(),
                "types": "cash_dividend,capital_gains_distribution,stock_dividend,forward_split,reverse_split,unit_split",
                "limit": "1000",
                "sort": "asc",
            }
            if page_token:
                query["page_token"] = page_token
            data = self._request("GET", f"{self.data_origin}/v1/corporate-actions?{urlencode(query)}")
            rows = data if isinstance(data, list) else data.get("corporate_actions", data.get("actions", data))
            if isinstance(rows, dict):
                for value in rows.values():
                    if isinstance(value, list):
                        output.extend(row for row in value if isinstance(row, dict))
            elif isinstance(rows, list):
                output.extend(row for row in rows if isinstance(row, dict))
            page_token = None if isinstance(data, list) else data.get("next_page_token")
            if not page_token:
                return output

    def submit_order(self, order: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._request("POST", f"{self.trading_origin}/v2/orders", json=order)
        except BrokerError as exc:
            if "HTTP_5" in str(exc) or "UNAVAILABLE" in str(exc):
                raise BrokerSubmissionUnknown("ETF_LIVE_ORDER_SUBMISSION_UNKNOWN") from exc
            raise

    def cancel_order(self, broker_order_id: str) -> None:
        self._request("DELETE", f"{self.trading_origin}/v2/orders/{broker_order_id}")

    def activities(self, *, after: datetime | None = None, activity_types: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        """Read all account activities with stable forward pagination."""

        page_token: str | None = None
        output: list[dict[str, Any]] = []
        while True:
            query: dict[str, Any] = {"direction": "asc", "page_size": "100"}
            if after is not None:
                query["after"] = after.astimezone(UTC).isoformat()
            if activity_types:
                query["activity_types"] = ",".join(activity_types)
            if page_token:
                query["page_token"] = page_token
            payload = self._request("GET", f"{self.trading_origin}/v2/account/activities?{urlencode(query)}")
            if isinstance(payload, list):
                records = payload
            elif isinstance(payload, dict):
                records = payload.get("activities", payload.get("data", []))
            else:
                raise BrokerError("ETF_LIVE_ACCOUNT_ACTIVITIES_INVALID")
            if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
                raise BrokerError("ETF_LIVE_ACCOUNT_ACTIVITIES_INVALID")
            page = [dict(item) for item in records]
            output.extend(page)
            if len(page) < 100:
                return output
            last_id = page[-1].get("id")
            if last_id is None or str(last_id) == page_token:
                raise BrokerError("ETF_LIVE_ACCOUNT_ACTIVITY_PAGINATION_INVALID")
            page_token = str(last_id)

    def settlement_calendar(self, *, start: date, end: date) -> list[dict[str, Any]]:
        """Read Alpaca's explicit market/settlement calendar records."""

        if end < start:
            raise ValueError("ETF_LIVE_CALENDAR_RANGE_INVALID")
        query = urlencode({"start": start.isoformat(), "end": end.isoformat(), "date_type": "TRADING"})
        payload = self._request("GET", f"{self.trading_origin}/v2/calendar?{query}")
        if isinstance(payload, list):
            records = payload
        elif isinstance(payload, dict):
            records = payload.get("calendar", payload.get("data", []))
        else:
            raise BrokerError("ETF_LIVE_SETTLEMENT_CALENDAR_INVALID")
        if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
            raise BrokerError("ETF_LIVE_SETTLEMENT_CALENDAR_INVALID")
        for row in records:
            if not row.get("date") or not row.get("settlement_date"):
                raise BrokerError("ETF_LIVE_SETTLEMENT_DATE_MISSING")
        return [dict(item) for item in records]
