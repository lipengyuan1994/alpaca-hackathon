"""Deterministic defined-risk selector driven by the shared YAML catalog."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

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

from .catalog import TemplateCatalogV1, load_template_catalog


class PlanningError(ValueError):
    """Stable fail-closed selector refusal."""


template_catalog = load_template_catalog()
template_catalog_hash = template_catalog.content_hash


def _eligible(
    snapshot: MarketSnapshotV1,
    intent: TradeIntentV1,
    now: datetime,
    catalog: TemplateCatalogV1,
) -> list[OptionContractV1]:
    now = now.astimezone(UTC)
    definition = catalog.templates.get(intent.template_id)  # type: ignore[arg-type]
    if definition is None or not definition.enabled:
        raise PlanningError("PLAN_TEMPLATE_NOT_ENABLED")
    if intent.direction != definition.direction:
        raise PlanningError("PLAN_INTENT_DIRECTION_MISMATCH")
    candidates = []
    for option in snapshot.option_contracts:
        dte = (option.expiration - now).total_seconds() / 86_400
        quote = option.quote
        if (
            option.underlying == intent.underlying
            and option.right == definition.right
            and option.deliverable == "STANDARD"
            and definition.min_dte <= dte <= definition.max_dte
            and quote.bid > 0
            and quote.ask >= quote.bid
            and quote.event_time <= now
            and quote.available_time <= now
            and now - quote.event_time <= timedelta(seconds=catalog.quote_ttl_seconds)
        ):
            candidates.append(option)
    return sorted(candidates, key=lambda option: (option.expiration, option.strike, option.symbol))


def select_vertical_contracts(
    intent: TradeIntentV1,
    market: MarketSnapshotV1,
    *,
    now: datetime,
    catalog: TemplateCatalogV1 = template_catalog,
) -> tuple[OptionContractV1, OptionContractV1]:
    """Select the frozen O2 long and outward short strikes deterministically."""
    candidates = _eligible(market, intent, now, catalog)
    if len(candidates) < 2:
        raise PlanningError("PLAN_INSUFFICIENT_SPREAD_LEGS")
    underlying = market.underlying_quotes.get(intent.underlying)
    if underlying is None:
        raise PlanningError("PLAN_UNDERLYING_NOT_IN_SNAPSHOT")
    spot = (underlying.bid + underlying.ask) / Decimal("2")
    expiration = candidates[0].expiration
    same_expiry = [item for item in candidates if item.expiration == expiration]
    if len(same_expiry) < 2:
        raise PlanningError("PLAN_INSUFFICIENT_SPREAD_LEGS")
    definition = catalog.templates[intent.template_id]  # type: ignore[index]
    # The published O2 policy resolves an exact nearest-spot tie toward OTM:
    # a higher strike for calls and a lower strike for puts.  Symbol is merely
    # a stable final tie-breaker for duplicate contract records.
    if definition.right == "CALL":
        long = min(
            same_expiry,
            key=lambda item: (abs(item.strike - spot), -item.strike, item.symbol),
        )
    else:
        long = min(
            same_expiry,
            key=lambda item: (abs(item.strike - spot), item.strike, item.symbol),
        )
    offset = Decimal(str(definition.target_short_offset_fraction))
    if intent.template_id == "CALL_DEBIT_SPREAD_V1":
        eligible_shorts = [item for item in same_expiry if item.strike > long.strike]
        target = spot * (Decimal("1") + offset)
    elif intent.template_id == "PUT_DEBIT_SPREAD_V1":
        eligible_shorts = [item for item in same_expiry if item.strike < long.strike]
        target = spot * (Decimal("1") - offset)
    else:
        raise PlanningError("PLAN_TEMPLATE_NOT_ENABLED")
    if not eligible_shorts:
        raise PlanningError("PLAN_INSUFFICIENT_SPREAD_LEGS")
    # A debit spread's short must be rounded *outward* to the next listed
    # standard strike, never inward merely because that strike is closer to
    # the one-percent target.
    if definition.right == "CALL":
        outward = [item for item in eligible_shorts if item.strike >= target]
        if not outward:
            raise PlanningError("PLAN_SHORT_STRIKE_OUTWARD_UNAVAILABLE")
        short = min(outward, key=lambda item: (item.strike, item.symbol))
    else:
        outward = [item for item in eligible_shorts if item.strike <= target]
        if not outward:
            raise PlanningError("PLAN_SHORT_STRIKE_OUTWARD_UNAVAILABLE")
        short = min(outward, key=lambda item: (-item.strike, item.symbol))
    return long, short


def _quantity(
    *,
    debit: Decimal,
    risk_tier: str,
    account: AccountSnapshotV1,
    catalog: TemplateCatalogV1,
) -> int:
    definition = catalog.risk_tiers[risk_tier]  # type: ignore[index]
    fixed = Decimal(str(definition.max_loss_dollars))
    equity_budget = account.equity * Decimal(str(definition.max_equity_fraction))
    budget = min(fixed, equity_budget, account.buying_power)
    per_contract = debit * Decimal("100")
    quantity = int(budget // per_contract)
    if quantity < 1:
        raise PlanningError("PLAN_RISK_BUDGET_TOO_SMALL")
    return quantity


def build_plan(
    intent: TradeIntentV1,
    market: MarketSnapshotV1,
    account: AccountSnapshotV1,
    positions: PositionSnapshotV1,
    order_risk: OrderRiskSnapshotV1,
    *,
    now: datetime,
    catalog: TemplateCatalogV1 = template_catalog,
) -> OrderPlanV1:
    """Build an immutable debit vertical from the exact shared selector/sizer."""
    now = now.astimezone(UTC)
    if intent.expires_at <= now:
        raise PlanningError("PLAN_INTENT_EXPIRED")
    long, short = select_vertical_contracts(intent, market, now=now, catalog=catalog)
    debit = long.quote.ask - short.quote.bid
    width = abs(short.strike - long.strike)
    if debit <= 0 or debit >= width:
        raise PlanningError("PLAN_INVALID_DEBIT")
    quantity = _quantity(debit=debit, risk_tier=intent.risk_tier, account=account, catalog=catalog)
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
        "catalog_hash": catalog.content_hash,
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
        template_id=intent.template_id,
        position_policy_id=intent.position_policy_id,
        legs=legs,
        quantity=quantity,
        limit_debit=debit,
        client_order_id=client_order_id,
        market_snapshot_hash=market.content_hash,
        account_snapshot_version=account.version,
        position_snapshot_version=positions.version,
        order_risk_snapshot_version=order_risk.version + 1,
        maximum_loss=maximum_loss,
    )
