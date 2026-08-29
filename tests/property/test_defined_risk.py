from __future__ import annotations

from apps.decision_worker.main import FIXTURE_TIME, _evaluate
from packages.decision_core.resolver import resolve
from packages.order_planner import build_plan


def test_planner_emits_atomic_defined_risk_vertical() -> None:
    market, account, positions, risk, context, _, pair = _evaluate("regime_momentum", momentum=True)
    evaluation, thesis = pair
    intent = resolve(evaluation, thesis, context, now=FIXTURE_TIME)
    plan = build_plan(intent, market, account, positions, risk, now=FIXTURE_TIME)
    assert len(plan.legs) == 2
    assert {leg.side for leg in plan.legs} == {"BUY", "SELL"}
    assert plan.maximum_loss == plan.limit_debit * 100 * plan.quantity
    assert plan.legs[0].expiration == plan.legs[1].expiration
