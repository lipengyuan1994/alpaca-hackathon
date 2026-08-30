"""Read-only Alpaca Market Data adapter for the daily economic context."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from packages.runtime_secrets import SecretConfigurationError, require_yaml_file_secret

from .collector import EconomicContextError, RawDailyBarV1, RawNewsHeadlineV1


class AlpacaEconomicDataAdapter:
    """Only exposes historical bars and headline metadata; never a trading client."""

    def __init__(self, *, stock_client: Any, news_client: Any) -> None:
        self._stock_client = stock_client
        self._news_client = news_client

    @classmethod
    def from_environment(cls) -> "AlpacaEconomicDataAdapter":
        try:
            key = require_yaml_file_secret(
                "ECONOMIC_ALPACA_API_KEY",
                key_path=("economic_alpaca_api_key",),
            )
            secret = require_yaml_file_secret(
                "ECONOMIC_ALPACA_API_SECRET",
                key_path=("economic_alpaca_api_secret",),
            )
        except SecretConfigurationError as exc:
            raise EconomicContextError("ECONOMIC_ALPACA_CREDENTIALS_MISSING") from exc
        try:
            from alpaca.data.historical.news import NewsClient
            from alpaca.data.historical.stock import StockHistoricalDataClient
        except ImportError as exc:  # pragma: no cover - deployment dependency check
            raise EconomicContextError("ALPACA_PY_NOT_INSTALLED") from exc
        return cls(
            stock_client=StockHistoricalDataClient(key, secret),
            news_client=NewsClient(key, secret),
        )

    def fetch_daily_bars(
        self,
        symbols: tuple[str, ...],
        *,
        start: datetime,
        end: datetime,
    ) -> dict[str, tuple[RawDailyBarV1, ...]]:
        try:
            from alpaca.data.enums import DataFeed
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame

            response = self._stock_client.get_stock_bars(
                StockBarsRequest(
                    symbol_or_symbols=list(symbols),
                    timeframe=TimeFrame.Day,
                    start=start,
                    end=end,
                    feed=DataFeed.IEX,
                )
            )
            data = getattr(response, "data", response)
            if not isinstance(data, dict):
                raise ValueError("ALPACA_BARS_RESPONSE_INVALID")
            result: dict[str, tuple[RawDailyBarV1, ...]] = {}
            for symbol in symbols:
                values = data.get(symbol, ())
                result[symbol] = tuple(
                    RawDailyBarV1(
                        symbol=symbol,
                        timestamp=self._timestamp(item.timestamp),
                        close=Decimal(str(item.close)),
                    )
                    for item in values
                )
            return result
        except EconomicContextError:
            raise
        except Exception as exc:
            raise EconomicContextError("ALPACA_ECONOMIC_BARS_UNAVAILABLE") from exc

    def fetch_news_headlines(
        self,
        symbols: tuple[str, ...],
        *,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> tuple[RawNewsHeadlineV1, ...]:
        if limit == 0:
            return ()
        try:
            from alpaca.data.requests import NewsRequest

            response = self._news_client.get_news(
                NewsRequest(
                    symbols=",".join(symbols),
                    start=start,
                    end=end,
                    limit=limit,
                    include_content=False,
                )
            )
            data = getattr(response, "data", response)
            if not isinstance(data, (list, tuple)):
                raise ValueError("ALPACA_NEWS_RESPONSE_INVALID")
            return tuple(
                RawNewsHeadlineV1(
                    news_id=str(item.id),
                    headline=str(item.headline),
                    source=str(getattr(item, "source", "ALPACA_NEWS")),
                    symbols=tuple(str(value) for value in getattr(item, "symbols", ()) or ()),
                    published_at=self._timestamp(item.created_at),
                    updated_at=self._timestamp(item.updated_at),
                )
                for item in data
            )
        except EconomicContextError:
            raise
        except Exception as exc:
            raise EconomicContextError("ALPACA_ECONOMIC_NEWS_UNAVAILABLE") from exc

    @staticmethod
    def _timestamp(value: Any) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise EconomicContextError("ALPACA_ECONOMIC_TIMESTAMP_INVALID")
        return value.astimezone(UTC)
