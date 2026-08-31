"""Build one causal, bounded daily Alpaca market/news context."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol
from zoneinfo import ZoneInfo

from packages.contracts.canonical import canonical_hash
from packages.contracts.models import (
    DailyEconomicContextV1,
    EconomicMarketObservationV1,
    EconomicNewsHeadlineV1,
)

from .config import EconomicContextConfigV1

_NEW_YORK = ZoneInfo("America/New_York")


class EconomicContextError(ValueError):
    """Stable fail-closed error from the economic context boundary."""


@dataclass(frozen=True)
class RawDailyBarV1:
    symbol: str
    timestamp: datetime
    close: Decimal


@dataclass(frozen=True)
class RawNewsHeadlineV1:
    news_id: str
    headline: str
    source: str
    symbols: tuple[str, ...]
    published_at: datetime
    updated_at: datetime


class EconomicDataPort(Protocol):
    """Read-only Alpaca data surface; no trading client is part of this port."""

    def fetch_daily_bars(
        self,
        symbols: tuple[str, ...],
        *,
        start: datetime,
        end: datetime,
    ) -> dict[str, tuple[RawDailyBarV1, ...]]: ...

    def fetch_news_headlines(
        self,
        symbols: tuple[str, ...],
        *,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> tuple[RawNewsHeadlineV1, ...]: ...


class DailyEconomicContextStore(Protocol):
    def load_daily_context(self, trading_date: date) -> DailyEconomicContextV1 | None: ...

    def claim_daily_collection(
        self,
        *,
        trading_date: date,
        config_hash: str,
        now: datetime,
    ) -> str: ...

    def complete_daily_collection(self, context: DailyEconomicContextV1) -> None: ...

    def fail_daily_collection(self, *, trading_date: date, reason_code: str, now: datetime) -> None: ...


@dataclass(frozen=True)
class EconomicContextResult:
    context: DailyEconomicContextV1
    source: str


def nyse_trading_date(now: datetime) -> date:
    return now.astimezone(_NEW_YORK).date()


def _midnight_after(trading_date: date) -> datetime:
    return datetime.combine(trading_date + timedelta(days=1), time.min, tzinfo=_NEW_YORK).astimezone(UTC)


class EconomicContextCollector:
    """Claims the one daily capture before touching Alpaca data endpoints."""

    def __init__(
        self,
        *,
        store: DailyEconomicContextStore,
        data_port: EconomicDataPort,
        config: EconomicContextConfigV1,
    ) -> None:
        self._store = store
        self._data_port = data_port
        self._config = config

    def get_or_collect(self, *, now: datetime) -> EconomicContextResult:
        now = now.astimezone(UTC)
        trading_date = nyse_trading_date(now)
        existing = self._store.load_daily_context(trading_date)
        if existing is not None:
            return EconomicContextResult(context=existing, source="CACHE")
        local = now.astimezone(_NEW_YORK)
        if local.weekday() >= 5:
            raise EconomicContextError("ECONOMIC_CONTEXT_NON_TRADING_DAY")
        if not (self._config.collection_window_start_et <= local.timetz().replace(tzinfo=None) <= self._config.collection_window_end_et):
            raise EconomicContextError("ECONOMIC_CONTEXT_OUTSIDE_MORNING_WINDOW")
        claim = self._store.claim_daily_collection(
            trading_date=trading_date,
            config_hash=self._config.config_hash,
            now=now,
        )
        if claim != "CLAIMED":
            raise EconomicContextError(f"ECONOMIC_CONTEXT_{claim}")
        try:
            context = self._collect(now=now, trading_date=trading_date)
            self._store.complete_daily_collection(context)
            return EconomicContextResult(context=context, source="ALPACA")
        except EconomicContextError as exc:
            self._store.fail_daily_collection(
                trading_date=trading_date,
                reason_code=str(exc),
                now=now,
            )
            raise
        except Exception as exc:
            self._store.fail_daily_collection(
                trading_date=trading_date,
                reason_code="ECONOMIC_CONTEXT_PROVIDER_FAILURE",
                now=now,
            )
            raise EconomicContextError("ECONOMIC_CONTEXT_PROVIDER_FAILURE") from exc

    def _collect(self, *, now: datetime, trading_date: date) -> DailyEconomicContextV1:
        request_start = now - timedelta(days=self._config.lookback_calendar_days)
        bars = self._data_port.fetch_daily_bars(
            self._config.all_symbols,
            start=request_start,
            end=now,
        )
        macro = tuple(
            self._observation(symbol, "MACRO", bars, now=now)
            for symbol in self._config.macro_proxy_symbols
        )
        micro = tuple(
            self._observation(symbol, "MICRO", bars, now=now)
            for symbol in self._config.micro_context_symbols
        )
        raw_news = self._data_port.fetch_news_headlines(
            self._config.all_symbols,
            start=now - timedelta(hours=self._config.news_lookback_hours),
            end=now,
            limit=self._config.maximum_news_headlines,
        )
        news = self._news(raw_news, now=now)
        source_request_hash = canonical_hash(
            {
                "schema_version": "alpaca-economic-context-request/v1",
                "trading_date": trading_date.isoformat(),
                "config_hash": self._config.config_hash,
                "symbols": self._config.all_symbols,
                "lookback_calendar_days": self._config.lookback_calendar_days,
                "news_lookback_hours": self._config.news_lookback_hours,
                "maximum_news_headlines": self._config.maximum_news_headlines,
            }
        )
        return DailyEconomicContextV1(
            context_id=f"economic-{source_request_hash.removeprefix('sha256:')[:24]}",
            trading_date=trading_date,
            collected_at=now,
            expires_at=_midnight_after(trading_date),
            collection_config_hash=self._config.config_hash,
            source_request_hash=source_request_hash,
            macro_observations=macro,
            micro_observations=micro,
            news_headlines=news,
            quality_flags=(
                "ALPACA_MARKET_PROXIES_ARE_NOT_OFFICIAL_MACRO_SERIES",
                "ALPACA_NEWS_HEADLINES_ARE_UNTRUSTED_CONTEXT",
            ),
        )

    @staticmethod
    def _observation(
        symbol: str,
        category: str,
        bars: dict[str, tuple[RawDailyBarV1, ...]],
        *,
        now: datetime,
    ) -> EconomicMarketObservationV1:
        candidates = sorted(
            (item for item in bars.get(symbol, ()) if item.timestamp.astimezone(UTC) <= now),
            key=lambda item: item.timestamp,
        )
        if len(candidates) < 2:
            raise EconomicContextError("ECONOMIC_CONTEXT_HISTORY_INSUFFICIENT")
        previous, current = candidates[-2:]
        if current.close <= 0 or previous.close <= 0:
            raise EconomicContextError("ECONOMIC_CONTEXT_INVALID_CLOSE")
        return_bps = ((current.close / previous.close) - Decimal("1")) * Decimal("10000")
        return EconomicMarketObservationV1(
            category=category,  # type: ignore[arg-type]
            symbol=symbol,
            session_date=current.timestamp.astimezone(_NEW_YORK).date(),
            close=current.close,
            previous_close=previous.close,
            return_bps=return_bps.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            observed_at=current.timestamp,
            available_at=now,
        )

    def _news(
        self,
        raw_news: tuple[RawNewsHeadlineV1, ...],
        *,
        now: datetime,
    ) -> tuple[EconomicNewsHeadlineV1, ...]:
        normalized: list[EconomicNewsHeadlineV1] = []
        seen: set[str] = set()
        for item in sorted(raw_news, key=lambda value: (value.published_at, value.news_id)):
            if item.news_id in seen or item.updated_at.astimezone(UTC) > now:
                continue
            headline = item.headline.strip()
            if not headline:
                continue
            seen.add(item.news_id)
            normalized.append(
                EconomicNewsHeadlineV1(
                    news_id=item.news_id,
                    headline=headline,
                    source=item.source,
                    symbols=tuple(sorted(set(item.symbols))),
                    published_at=item.published_at,
                    updated_at=item.updated_at,
                )
            )
            if len(normalized) == self._config.maximum_news_headlines:
                break
        return tuple(normalized)
