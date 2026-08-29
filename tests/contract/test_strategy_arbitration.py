from __future__ import annotations

from decimal import Decimal

import pytest

from packages.strategy_sdk import ArbitrationError, select_ranked_symbol


def test_arbitration_selects_highest_score_and_uses_frozen_tie_break() -> None:
    selected = select_ranked_symbol(
        {"QQQ": Decimal("1.25"), "SPY": Decimal("1.25")},
        eligible_symbols=("QQQ", "SPY"),
        minimum_score=Decimal("1"),
    )
    assert selected is not None
    assert selected.symbol == "SPY"
    assert selected.score == Decimal("1.25")


def test_arbitration_returns_none_below_preregistered_threshold() -> None:
    assert (
        select_ranked_symbol(
            {"SMH": Decimal("0.99"), "SOXL": Decimal("0.75")},
            eligible_symbols=("SMH", "SOXL"),
            minimum_score=Decimal("1"),
        )
        is None
    )


@pytest.mark.parametrize(
    ("scores", "eligible", "reason"),
    [
        ({"SPY": Decimal("1")}, ("SPY", "QQQ"), "ARBITRATION_SCORE_SCOPE_MISMATCH"),
        ({"AAPL": Decimal("1")}, ("AAPL",), "ARBITRATION_SYMBOL_NOT_FROZEN"),
        ({"SPY": Decimal("NaN")}, ("SPY",), "ARBITRATION_SCORE_INVALID"),
    ],
)
def test_arbitration_fails_closed_on_invalid_authority(
    scores: dict[str, Decimal],
    eligible: tuple[str, ...],
    reason: str,
) -> None:
    with pytest.raises(ArbitrationError, match=reason):
        select_ranked_symbol(
            scores,
            eligible_symbols=eligible,
            minimum_score=Decimal("1"),
        )
