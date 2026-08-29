from __future__ import annotations

from decimal import Decimal

import pytest

from apps.decision_worker.main import FIXTURE_TIME, _evaluate
from packages.contracts.models import (
    MarketSnapshotV1,
    OptionContractV1,
    PositionPolicyIdV1,
    QuoteV1,
    TradeIntentV1,
)
from packages.decision_core.resolver import NoTradeRecordedV1, resolve
from packages.order_planner import PlanningError, select_vertical_contracts


def _intent():
    market, _, _, _, context, _, pair = _evaluate("regime_momentum", momentum=True)
    evaluation, thesis = pair
    intent = resolve(
        evaluation,
        thesis,
        context,
        now=FIXTURE_TIME,
        position_policy_id=PositionPolicyIdV1.TREND_VWAP_OR_60M_V1,
    )
    assert not isinstance(intent, NoTradeRecordedV1)
    return market, intent


def _put_intent() -> tuple[MarketSnapshotV1, TradeIntentV1]:
    market, call_intent = _intent()
    return market, TradeIntentV1.model_validate(
        call_intent.model_dump(mode="json", exclude={"content_hash"})
        | {"template_id": "PUT_DEBIT_SPREAD_V1", "direction": "BEARISH"}
    )


def _option(*, expiration, right: str, strike: str) -> OptionContractV1:
    numeric_strike = Decimal(strike)
    root = "SPY"
    symbol = f"{root}{expiration:%y%m%d}{'C' if right == 'CALL' else 'P'}{int(numeric_strike * 1000):08d}"
    return OptionContractV1(
        symbol=symbol,
        underlying=root,
        right=right,  # type: ignore[arg-type]
        strike=numeric_strike,
        expiration=expiration,
        quote=QuoteV1(bid="1.00", ask="1.20", event_time=FIXTURE_TIME, available_time=FIXTURE_TIME),
    )


def _market_with_options(
    market,
    options: tuple[OptionContractV1, ...],
    *,
    spot: Decimal | None = None,
) -> MarketSnapshotV1:
    payload = market.model_dump(mode="json", exclude={"content_hash"}) | {"option_contracts": options}
    if spot is not None:
        payload["underlying_quotes"] = {
            "SPY": QuoteV1(
                bid=spot,
                ask=spot,
                event_time=FIXTURE_TIME,
                available_time=FIXTURE_TIME,
            ).model_dump(mode="json")
        }
    return MarketSnapshotV1.model_validate(payload)


def test_call_long_exact_spot_tie_prefers_the_otm_strike() -> None:
    market, intent = _intent()
    expiry = market.option_contracts[0].expiration
    adjusted = _market_with_options(
        market,
        (
            _option(expiration=expiry, right="CALL", strike="595"),
            _option(expiration=expiry, right="CALL", strike="605"),
            _option(expiration=expiry, right="CALL", strike="615"),
        ),
        spot=Decimal("600"),
    )

    long, short = select_vertical_contracts(intent, adjusted, now=FIXTURE_TIME)

    assert long.strike == Decimal("605")
    assert short.strike == Decimal("615")


def test_call_short_is_rounded_outward_instead_of_to_the_nearest_inward_strike() -> None:
    market, intent = _intent()
    expiry = market.option_contracts[0].expiration
    adjusted = _market_with_options(
        market,
        (
            _option(expiration=expiry, right="CALL", strike="600"),
            _option(expiration=expiry, right="CALL", strike="604"),
            _option(expiration=expiry, right="CALL", strike="606"),
        ),
    )

    _, short = select_vertical_contracts(intent, adjusted, now=FIXTURE_TIME)

    assert short.strike == Decimal("606")


def test_selector_refuses_when_no_outward_short_is_listed() -> None:
    market, intent = _intent()
    expiry = market.option_contracts[0].expiration
    adjusted = _market_with_options(
        market,
        (
            _option(expiration=expiry, right="CALL", strike="600"),
            _option(expiration=expiry, right="CALL", strike="604"),
        ),
    )

    with pytest.raises(PlanningError, match="PLAN_SHORT_STRIKE_OUTWARD_UNAVAILABLE"):
        select_vertical_contracts(intent, adjusted, now=FIXTURE_TIME)


def test_selector_rejects_a_direction_tampered_intent() -> None:
    market, intent = _intent()
    tampered = intent.model_copy(update={"direction": "BEARISH"})

    with pytest.raises(PlanningError, match="PLAN_INTENT_DIRECTION_MISMATCH"):
        select_vertical_contracts(tampered, market, now=FIXTURE_TIME)


def test_put_long_exact_spot_tie_prefers_the_otm_strike_and_rounds_short_outward() -> None:
    market, intent = _put_intent()
    expiry = market.option_contracts[0].expiration
    adjusted = _market_with_options(
        market,
        (
            _option(expiration=expiry, right="PUT", strike="605"),
            _option(expiration=expiry, right="PUT", strike="595"),
            _option(expiration=expiry, right="PUT", strike="590"),
        ),
        spot=Decimal("600"),
    )

    long, short = select_vertical_contracts(intent, adjusted, now=FIXTURE_TIME)

    assert long.strike == Decimal("595")
    assert short.strike == Decimal("590")


def test_put_short_is_rounded_outward_instead_of_to_the_nearest_inward_strike() -> None:
    market, intent = _put_intent()
    expiry = market.option_contracts[0].expiration
    adjusted = _market_with_options(
        market,
        (
            _option(expiration=expiry, right="PUT", strike="600"),
            _option(expiration=expiry, right="PUT", strike="594"),
            _option(expiration=expiry, right="PUT", strike="592"),
        ),
    )

    _, short = select_vertical_contracts(intent, adjusted, now=FIXTURE_TIME)

    assert short.strike == Decimal("592")
