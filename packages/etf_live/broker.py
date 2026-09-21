"""Small, allowlisted Alpaca production client for the L11 runtime.

The client exposes only account, market-data, order and reconciliation calls
needed by this strategy.  It has no transfer, options, withdrawal or blanket
liquidation methods.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
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

    def daily_bars(self, symbols: tuple[str, ...], *, start: datetime, end: datetime, limit: int = 1000) -> dict[str, list[dict[str, Any]]]:
        params = urlencode({"symbols": ",".join(symbols), "timeframe": "1Day", "start": start.astimezone(UTC).isoformat(), "end": end.astimezone(UTC).isoformat(), "limit": limit, "feed": "sip", "adjustment": "all", "sort": "asc"})
        data = self._request("GET", f"{self.data_origin}/v2/stocks/bars?{params}")
        return dict(data.get("bars", data))

    def submit_order(self, order: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._request("POST", f"{self.trading_origin}/v2/orders", json=order)
        except BrokerError as exc:
            if "HTTP_5" in str(exc) or "UNAVAILABLE" in str(exc):
                raise BrokerSubmissionUnknown("ETF_LIVE_ORDER_SUBMISSION_UNKNOWN") from exc
            raise

    def cancel_order(self, broker_order_id: str) -> None:
        self._request("DELETE", f"{self.trading_origin}/v2/orders/{broker_order_id}")

    def activities(self, *, after: datetime | None = None) -> list[dict[str, Any]]:
        query = {"direction": "asc", "page_size": "100"}
        if after is not None:
            query["after"] = after.astimezone(UTC).isoformat()
        return list(self._request("GET", f"{self.trading_origin}/v2/account/activities?{urlencode(query)}"))
