"""Schema-only Gemini news veto contract for future prospective overlay use."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from packages.contracts.canonical import canonical_hash


@dataclass(frozen=True)
class NewsItem:
    evidence_id: str
    published_at: datetime
    headline: str
    source: str
    symbols: tuple[str, ...] = ()
    category: str = "unknown"
    updated_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "published_at": self.published_at.astimezone(timezone.utc).isoformat(),
            "headline": self.headline,
            "source": self.source,
            "symbols": list(self.symbols),
            "category": self.category,
            "updated_at": self.updated_at.astimezone(timezone.utc).isoformat() if self.updated_at else None,
        }


@dataclass(frozen=True)
class MorningNewsContext:
    trading_date: str
    frozen_at: datetime
    items: tuple[NewsItem, ...]
    market_snapshot: Mapping[str, Any]
    collection_start: datetime | None = None
    background_start: datetime | None = None
    context_hash: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "etf-cash-morning-news-context/v1",
            "trading_date": self.trading_date,
            "frozen_at": self.frozen_at.astimezone(timezone.utc).isoformat(),
            "items": [item.as_dict() for item in self.items],
            "market_snapshot": dict(self.market_snapshot),
            "collection_start": self.collection_start.astimezone(timezone.utc).isoformat() if self.collection_start else None,
            "background_start": self.background_start.astimezone(timezone.utc).isoformat() if self.background_start else None,
        }

    @property
    def content_hash(self) -> str:
        return canonical_hash(self.as_dict())


@dataclass(frozen=True)
class NewsVetoAssessment:
    recommendation: str
    reason_code: str
    evidence_ids: tuple[str, ...]
    explanation: str
    model_version: str
    prompt_version: str
    model_input_hash: str
    raw_output_hash: str | None
    expires_at: datetime

    def __post_init__(self) -> None:
        if self.recommendation not in {"ALLOW_UNCHANGED", "VETO"}:
            raise ValueError("ETF_NEWS_RECOMMENDATION_INVALID")


def build_news_context(*, trading_date: str, frozen_at: datetime, items: list[NewsItem], market_snapshot: Mapping[str, Any], previous_cutoff: datetime | None = None) -> MorningNewsContext:
    """Freeze and hash a bounded, timestamp-ordered morning context."""
    if frozen_at.tzinfo is None:
        raise ValueError("ETF_NEWS_FROZEN_TIME_MUST_BE_TIMEZONE_AWARE")
    ordered = tuple(sorted(items, key=lambda item: (item.published_at, item.evidence_id)))
    if len(ordered) > 16:
        raise ValueError("ETF_NEWS_CONTEXT_TOO_LARGE")
    cutoff = frozen_at.astimezone(timezone.utc)
    trading = date.fromisoformat(trading_date)
    eastern = ZoneInfo("America/New_York")
    collection_start = datetime.combine(trading, time(8, 45), tzinfo=eastern).astimezone(timezone.utc)
    background_start = (previous_cutoff.astimezone(timezone.utc) if previous_cutoff else cutoff - timedelta(days=7))
    if any(item.published_at.astimezone(timezone.utc) < background_start or item.published_at.astimezone(timezone.utc) > cutoff for item in ordered):
        raise ValueError("ETF_NEWS_ITEM_OUTSIDE_CONTEXT_WINDOW")
    context = MorningNewsContext(trading_date, cutoff, ordered, dict(market_snapshot), collection_start, background_start)
    return replace(context, context_hash=context.content_hash)


def evaluate_news_gate(*, context: MorningNewsContext, proposed_increases: Mapping[str, float], assessment: NewsVetoAssessment, now: datetime) -> dict[str, Any]:
    """Return unchanged buys or a veto while always preserving deterministic exits.

    The assessment cannot redirect allocation, change quantities, or veto a
    reduction.  This pure helper is used by the fixture tests and later by the
    prospective Gemini adapter.
    """
    if context.context_hash is not None and context.content_hash != context.context_hash:
        return {"status": "VETO", "reason_code": "NEWS_CONTEXT_HASH_MISMATCH", "approved_increases": {}, "approved_exits": True}
    current = now.astimezone(timezone.utc)
    if current >= assessment.expires_at or assessment.model_input_hash != canonical_hash({"context_hash": context.content_hash, "proposed_increases": dict(proposed_increases)}):
        return {"status": "VETO", "reason_code": "NEWS_ASSESSMENT_STALE_OR_BINDING_MISMATCH", "approved_increases": {}, "approved_exits": True}
    available_evidence = {item.evidence_id for item in context.items}
    if any(evidence_id not in available_evidence for evidence_id in assessment.evidence_ids):
        return {"status": "VETO", "reason_code": "NEWS_EVIDENCE_ID_UNKNOWN", "approved_increases": {}, "approved_exits": True}
    if assessment.recommendation != "ALLOW_UNCHANGED":
        return {"status": "VETO", "reason_code": assessment.reason_code or "NEWS_VETO", "approved_increases": {}, "approved_exits": True}
    return {"status": "ALLOW_UNCHANGED", "reason_code": assessment.reason_code, "approved_increases": dict(proposed_increases), "approved_exits": True}


def model_input_hash(context: MorningNewsContext, proposed_increases: Mapping[str, float]) -> str:
    return canonical_hash({"context_hash": context.content_hash, "proposed_increases": dict(proposed_increases)})
