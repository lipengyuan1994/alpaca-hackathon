"""Pure deterministic arbitration shared by research adapters and plug-ins."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

FROZEN_SYMBOL_ORDER = ("SPY", "QQQ", "TQQQ", "SMH", "SOXL", "IGV")


class ArbitrationError(ValueError):
    """The caller supplied an incomplete or unauthorized candidate set."""


@dataclass(frozen=True)
class RankedCandidate:
    symbol: str
    score: Decimal


def select_ranked_symbol(
    scores: Mapping[str, Decimal],
    *,
    eligible_symbols: tuple[str, ...],
    minimum_score: Decimal,
) -> RankedCandidate | None:
    """Select the greatest eligible score, breaking exact ties by frozen order.

    Callers provide nonnegative, already-normalized opportunity scores. The
    helper deliberately does not infer direction, transform features, or widen
    the registered symbol set.
    """
    if minimum_score < 0 or not minimum_score.is_finite():
        raise ArbitrationError("ARBITRATION_THRESHOLD_INVALID")
    if not eligible_symbols or len(set(eligible_symbols)) != len(eligible_symbols):
        raise ArbitrationError("ARBITRATION_ELIGIBLE_SYMBOLS_INVALID")
    if any(symbol not in FROZEN_SYMBOL_ORDER for symbol in eligible_symbols):
        raise ArbitrationError("ARBITRATION_SYMBOL_NOT_FROZEN")
    if set(scores) != set(eligible_symbols):
        raise ArbitrationError("ARBITRATION_SCORE_SCOPE_MISMATCH")
    if any(not score.is_finite() or score < 0 for score in scores.values()):
        raise ArbitrationError("ARBITRATION_SCORE_INVALID")

    rank = {symbol: index for index, symbol in enumerate(FROZEN_SYMBOL_ORDER)}
    selected = min(
        eligible_symbols,
        key=lambda symbol: (-scores[symbol], rank[symbol]),
    )
    if scores[selected] < minimum_score:
        return None
    return RankedCandidate(symbol=selected, score=scores[selected])
