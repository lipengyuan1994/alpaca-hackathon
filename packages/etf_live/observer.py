"""Optional Gemini observation adapter with no trading authority."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from packages.etf_cash_research.news_gate import (
    MorningNewsContext,
    NewsVetoAssessment,
    evaluate_news_gate,
    model_input_hash,
)


def observe_news(*, context: MorningNewsContext, proposed_increases: dict[str, float], assessment: NewsVetoAssessment, now: datetime) -> dict[str, Any]:
    """Validate a precomputed observer result without changing ETF intents.

    The production transport is intentionally supplied later.  This adapter
    proves the observer contract and returns evidence only; the live runtime
    never passes its result into sizing or order submission.
    """
    result = evaluate_news_gate(context=context, proposed_increases=proposed_increases, assessment=assessment, now=now)
    return {"observed_at": now.astimezone(UTC).isoformat(), "context_hash": context.content_hash, "model_input_hash": model_input_hash(context, proposed_increases), "result": result}
