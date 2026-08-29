from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from apps.decision_worker.main import FIXTURE_TIME, _evaluate
from packages.contracts.models import OrderPlanV1, PositionPolicyIdV1
from packages.decision_core.resolver import resolve as resolve_intent
from packages.order_planner import build_plan


def resolve(evaluation, thesis, context, *, now):
    return resolve_intent(
        evaluation,
        thesis,
        context,
        now=now,
        position_policy_id=PositionPolicyIdV1.TREND_VWAP_OR_60M_V1,
    )


def test_planner_emits_atomic_defined_risk_vertical() -> None:
    market, account, positions, risk, context, _, pair = _evaluate("regime_momentum", momentum=True)
    evaluation, thesis = pair
    intent = resolve(evaluation, thesis, context, now=FIXTURE_TIME)
    plan = build_plan(intent, market, account, positions, risk, now=FIXTURE_TIME)
    assert len(plan.legs) == 2
    assert {leg.side for leg in plan.legs} == {"BUY", "SELL"}
    assert plan.maximum_loss == plan.limit_debit * 100 * plan.quantity
    assert plan.legs[0].expiration == plan.legs[1].expiration


@pytest.mark.parametrize(
    ("limit_debit", "declared_maximum_loss"),
    [
        (Decimal("4.00"), Decimal("1.00")),
        (Decimal("1.25"), Decimal("124.99")),
        (Decimal("1.25"), Decimal("125.01")),
    ],
)
def test_plan_contract_rejects_under_or_over_reported_maximum_loss(
    limit_debit: Decimal, declared_maximum_loss: Decimal
) -> None:
    market, account, positions, risk, context, _, pair = _evaluate(
        "regime_momentum", momentum=True
    )
    evaluation, thesis = pair
    intent = resolve(evaluation, thesis, context, now=FIXTURE_TIME)
    plan = build_plan(intent, market, account, positions, risk, now=FIXTURE_TIME)
    payload = plan.model_dump(exclude={"plan_hash"})
    payload.update(limit_debit=limit_debit, maximum_loss=declared_maximum_loss)

    with pytest.raises(ValidationError, match="maximum loss does not equal full order debit"):
        OrderPlanV1.model_validate(payload)


@pytest.mark.parametrize("limit_debit", [Decimal("5.00"), Decimal("5.01")])
def test_plan_contract_rejects_vertical_debit_at_or_above_width(
    limit_debit: Decimal,
) -> None:
    market, account, positions, risk, context, _, pair = _evaluate(
        "regime_momentum", momentum=True
    )
    evaluation, thesis = pair
    intent = resolve(evaluation, thesis, context, now=FIXTURE_TIME)
    plan = build_plan(intent, market, account, positions, risk, now=FIXTURE_TIME)
    payload = plan.model_dump(exclude={"plan_hash"})
    payload.update(limit_debit=limit_debit, maximum_loss=limit_debit * 100)

    with pytest.raises(ValidationError, match="vertical debit must be strictly below spread width"):
        OrderPlanV1.model_validate(payload)


def test_long_option_contract_derives_full_debit_maximum_loss() -> None:
    market, account, positions, risk, context, _, pair = _evaluate(
        "regime_momentum", momentum=True
    )
    evaluation, thesis = pair
    intent = resolve(evaluation, thesis, context, now=FIXTURE_TIME)
    spread = build_plan(intent, market, account, positions, risk, now=FIXTURE_TIME)
    payload = spread.model_dump(exclude={"plan_hash"})
    payload.update(
        template_id="LONG_CALL_V1",
        legs=(spread.legs[0].model_dump(),),
        limit_debit=Decimal("2.00"),
        maximum_loss=Decimal("2.00") * Decimal("100") * spread.quantity,
    )

    long_option = OrderPlanV1.model_validate(payload)

    assert long_option.recompute_maximum_loss() == Decimal("2.00") * Decimal(
        "100"
    ) * spread.quantity


def test_plan_contract_rejects_broker_price_rounding_and_ids_fit_alpaca() -> None:
    market, account, positions, risk, context, _, pair = _evaluate(
        "regime_momentum", momentum=True
    )
    evaluation, thesis = pair
    intent = resolve(evaluation, thesis, context, now=FIXTURE_TIME)
    plan = build_plan(intent, market, account, positions, risk, now=FIXTURE_TIME)
    assert len(plan.client_order_id) <= 48
    payload = plan.model_dump(exclude={"plan_hash"})
    payload.update(
        limit_debit=Decimal("1.235"),
        maximum_loss=Decimal("1.235") * Decimal("100") * plan.quantity,
    )

    with pytest.raises(ValidationError, match="cent precision"):
        OrderPlanV1.model_validate(payload)
