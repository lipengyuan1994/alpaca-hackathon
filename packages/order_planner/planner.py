"""Select a defined-risk debit structure from normalized snapshots only."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from packages.contracts.canonical import canonical_hash
from packages.contracts.models import (
    AccountSnapshotV1,
    MarketSnapshotV1,
    OptionContractV1,
    OrderLegV1,
    OrderPlanV1,
    OrderRiskSnapshotV1,
    PositionSnapshotV1,
    TradeIntentV1,
)
from packages.domain.identifiers import deterministic_client_order_id


class PlanningError(ValueError):
    pass


_CATALOG = {
    "CALL_DEBIT_SPREAD_V1": {"right": "CALL", "direction": "BULLISH"},
    "PUT_DEBIT_SPREAD_V1": {"right": "PUT", "direction": "BEARISH"},
    "LONG_CALL_V1": {"right": "CALL", "direction": "BULLISH"},
    "LONG_PUT_V1": {"right": "PUT", "direction": "BEARISH"},
}
template_catalog_hash = canonical_hash(_CATALOG)


def _eligible(snapshot: MarketSnapshotV1, intent: TradeIntentV1, now: datetime) -> list[OptionContractV1]:
    now = now.astimezone(UTC)
    candidates = [
        option
        for option in snapshot.option_contracts
        if option.underlying == intent.underlying
        and option.right == _CATALOG[intent.template_id]["right"]
        and option.deliverable == "STANDARD"
        and option.expiration > now
        and option.quote.bid > 0
        and option.quote.ask >= option.quote.bid
    ]
    return sorted(candidates, key=lambda option: (option.expiration, option.strike, option.symbol))


def build_plan(
    intent: TradeIntentV1,
    market: MarketSnapshotV1,
    account: AccountSnapshotV1,
    positions: PositionSnapshotV1,
    order_risk: OrderRiskSnapshotV1,
    *,
    now: datetime,
) -> OrderPlanV1:
    """Build an immutable defined-risk plan or return one stable planning refusal."""
    now = now.astimezone(UTC)
    if intent.expires_at <= now:
        raise PlanningError("PLAN_INTENT_EXPIRED")
    if intent.underlying not in market.underlying_quotes:
        raise PlanningError("PLAN_UNDERLYING_NOT_IN_SNAPSHOT")
    candidates = _eligible(market, intent, now)
    if not candidates:
        raise PlanningError("PLAN_NO_ELIGIBLE_CONTRACTS")
    quantity = 1 if intent.risk_tier == "TINY" else 2
    template = intent.template_id
    if template in {"LONG_CALL_V1", "LONG_PUT_V1"}:
        long = candidates[0]
        legs = (
            OrderLegV1(
                symbol=long.symbol,
                side="BUY",
                quantity=quantity,
                right=long.right,
                strike=long.strike,
                expiration=long.expiration,
            ),
        )
        debit = long.quote.ask
    else:
        expiration = candidates[0].expiration
        same_expiry = [item for item in candidates if item.expiration == expiration]
        if len(same_expiry) < 2:
            raise PlanningError("PLAN_INSUFFICIENT_SPREAD_LEGS")
        if template == "CALL_DEBIT_SPREAD_V1":
            long, short = same_expiry[0], same_expiry[1]
        else:
            long, short = same_expiry[-1], same_expiry[-2]
        debit = long.quote.ask - short.quote.bid
        if debit <= 0 or debit >= abs(short.strike - long.strike):
            raise PlanningError("PLAN_INVALID_DEBIT")
        legs = (
            OrderLegV1(
                symbol=long.symbol,
                side="BUY",
                quantity=quantity,
                right=long.right,
                strike=long.strike,
                expiration=long.expiration,
            ),
            OrderLegV1(
                symbol=short.symbol,
                side="SELL",
                quantity=quantity,
                right=short.right,
                strike=short.strike,
                expiration=short.expiration,
            ),
        )
    maximum_loss = debit * Decimal("100") * quantity
    material = {
        "intent_id": intent.intent_id,
        "legs": [leg.model_dump(mode="json") for leg in legs],
        "limit_debit": debit,
        "versions": [account.version, positions.version, order_risk.version],
    }
    client_order_id = deterministic_client_order_id(intent.intent_id, material)
    return OrderPlanV1(
        plan_id=f"plan-{client_order_id.removeprefix('paper-')[:24]}",
        intent_id=intent.intent_id,
        account_id=account.account_id,
        underlying=intent.underlying,
        template_id=template,
        legs=legs,
        quantity=quantity,
        limit_debit=debit,
        client_order_id=client_order_id,
        market_snapshot_hash=market.content_hash,
        account_snapshot_version=account.version,
        position_snapshot_version=positions.version,
        # The plan binds the prospective order-risk version that will be atomically
        # persisted with its reservation, not the stale pre-planning observation.
        order_risk_snapshot_version=order_risk.version + 1,
        maximum_loss=maximum_loss,
    )
