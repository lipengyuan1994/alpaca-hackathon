"""Independent execution-side replay of immutable risk and freshness invariants."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol
from urllib.parse import urlsplit

from packages.contracts.canonical import hash_without
from packages.contracts.models import (
    AccountSnapshotV1,
    BrokerEventV1,
    ContractError,
    ControlStateV1,
    ExecuteApprovedPlanV1,
    ExecuteReduceOnlyPlanV1,
    ExecutionDeploymentV1,
    ExecutionPreflightDecisionV1,
    ManagedPositionV1,
    MarketSnapshotV1,
    OperatingModeV1,
    OrderPlanV1,
    OrderRiskSnapshotV1,
    PositionSnapshotV1,
    ReduceOnlyOrderPlanV1,
    RiskInputV1,
    validate_occ_identity,
)
from packages.domain import reconciliation_hash
from packages.position_manager import reduce_only_violations


@dataclass(frozen=True)
class ExecutionResult:
    preflight: ExecutionPreflightDecisionV1
    broker_event: BrokerEventV1 | None


class ExecutionBroker(Protocol):
    def submit(
        self,
        plan: OrderPlanV1 | ReduceOnlyOrderPlanV1,
        *,
        now: datetime,
    ) -> BrokerEventV1: ...

    def reconcile(self, client_order_id: str, *, now: datetime) -> BrokerEventV1 | None: ...

    def runtime_state_violations(
        self,
        *,
        account: AccountSnapshotV1,
        positions: PositionSnapshotV1,
        order_risk: OrderRiskSnapshotV1,
        market: MarketSnapshotV1,
        now: datetime,
        quote_ttl_seconds: int,
    ) -> tuple[str, ...]: ...


def _is_exact_paper_endpoint(value: str) -> bool:
    """Allow only Alpaca's exact HTTPS paper origin, with no URL smuggling."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == "paper-api.alpaca.markets"
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )


def _recompute_plan_economics(command: ExecuteApprovedPlanV1) -> tuple[Decimal, bool]:
    plan = command.plan
    try:
        maximum_loss = plan.recompute_maximum_loss()
        width = plan.vertical_width()
    except (ContractError, IndexError, TypeError, ValueError):
        return plan.maximum_loss, False
    return maximum_loss, width is None or plan.limit_debit < width


def _market_clock_reasons(
    market: MarketSnapshotV1,
    *,
    now: datetime,
    ttl_seconds: int,
) -> list[str]:
    if market.clock is None:
        return ["PREFLIGHT_MARKET_CLOCK_MISSING"]
    reasons: list[str] = []
    if not market.clock.is_open:
        reasons.append("PREFLIGHT_MARKET_CLOSED")
    if market.clock.as_of > now or now - market.clock.as_of > timedelta(seconds=ttl_seconds):
        reasons.append("PREFLIGHT_MARKET_CLOCK_STALE")
    if market.clock.next_close <= now:
        reasons.append("PREFLIGHT_MARKET_CLOSE_PASSED")
    return reasons


def _deployment_reasons(risk_input: RiskInputV1, deployment: ExecutionDeploymentV1) -> list[str]:
    expected = {
        "risk_policy_hash": risk_input.risk_policy.policy_hash,
        "template_catalog_hash": risk_input.template_catalog_hash,
        "strategy_registry_hash": risk_input.strategy_registry_hash,
        "strategy_config_hash": risk_input.strategy_config_hash,
        "strategy_content_hash": risk_input.strategy_content_hash,
        "account_allowlist_hash": risk_input.account_allowlist_hash,
        "release_hash": risk_input.release_hash,
    }
    reasons = [
        f"PREFLIGHT_DEPLOYED_{name.removesuffix('_hash').upper()}_MISMATCH"
        for name, value in expected.items()
        if value != getattr(deployment, name)
    ]
    if risk_input.entry_cutoff_at != deployment.entry_cutoff_at:
        reasons.append("PREFLIGHT_DEPLOYED_ENTRY_CUTOFF_MISMATCH")
    if risk_input.flatten_at != deployment.flatten_at:
        reasons.append("PREFLIGHT_DEPLOYED_FLATTEN_TIME_MISMATCH")
    return reasons


def preflight_and_submit(
    command: ExecuteApprovedPlanV1,
    risk_input: RiskInputV1,
    market: MarketSnapshotV1,
    account: AccountSnapshotV1,
    positions: PositionSnapshotV1,
    order_risk: OrderRiskSnapshotV1,
    control: ControlStateV1,
    broker: ExecutionBroker,
    *,
    now: datetime,
    deployment: ExecutionDeploymentV1,
) -> ExecutionResult:
    """Reject before broker I/O unless all independently recomputed bindings match."""
    now = now.astimezone(UTC)
    reasons: list[str] = []
    plan = command.plan
    economic_maximum_loss, valid_vertical_debit = _recompute_plan_economics(command)
    if economic_maximum_loss != plan.maximum_loss:
        reasons.append("PREFLIGHT_MAXIMUM_LOSS_MISMATCH")
    if not valid_vertical_debit:
        reasons.append("PREFLIGHT_INVALID_VERTICAL_DEBIT")
    if command.command_hash != hash_without(command, "command_hash"):
        reasons.append("PREFLIGHT_COMMAND_HASH_MISMATCH")
    if plan.plan_hash != hash_without(plan, "plan_hash"):
        reasons.append("PREFLIGHT_PLAN_HASH_MISMATCH")
    if risk_input.risk_input_hash != hash_without(risk_input, "risk_input_hash"):
        reasons.append("PREFLIGHT_RISK_INPUT_MISMATCH")
    if risk_input.risk_policy.policy_hash != hash_without(
        risk_input.risk_policy, "policy_hash"
    ):
        reasons.append("PREFLIGHT_RISK_POLICY_HASH_MISMATCH")
    if command.approval.decision_hash != hash_without(command.approval, "decision_hash"):
        reasons.append("PREFLIGHT_APPROVAL_HASH_MISMATCH")
    if market.content_hash != hash_without(market, "content_hash"):
        reasons.append("PREFLIGHT_MARKET_CONTENT_HASH_MISMATCH")
    if account.content_hash != hash_without(account, "content_hash"):
        reasons.append("PREFLIGHT_ACCOUNT_CONTENT_HASH_MISMATCH")
    if positions.content_hash != hash_without(positions, "content_hash"):
        reasons.append("PREFLIGHT_POSITION_CONTENT_HASH_MISMATCH")
    if order_risk.content_hash != hash_without(order_risk, "content_hash"):
        reasons.append("PREFLIGHT_ORDER_RISK_CONTENT_HASH_MISMATCH")
    if control.content_hash != hash_without(control, "content_hash"):
        reasons.append("PREFLIGHT_CONTROL_CONTENT_HASH_MISMATCH")
    if deployment.content_hash != hash_without(deployment, "content_hash"):
        reasons.append("PREFLIGHT_DEPLOYMENT_CONTENT_HASH_MISMATCH")
    if not _is_exact_paper_endpoint(deployment.paper_base_url):
        reasons.append("PREFLIGHT_NON_PAPER_HOST")
    if account.account_id != deployment.expected_account_id or plan.account_id != deployment.expected_account_id:
        reasons.append("PREFLIGHT_ACCOUNT_MISMATCH")
    if positions.account_id != account.account_id or order_risk.account_id != account.account_id:
        reasons.append("PREFLIGHT_CROSS_ACCOUNT_SNAPSHOT")
    if positions.legs:
        reasons.append("PREFLIGHT_EXISTING_POSITION")
    if risk_input.risk_input_hash != command.risk_input_hash:
        reasons.append("PREFLIGHT_RISK_INPUT_MISMATCH")
    if risk_input.plan.plan_hash != plan.plan_hash:
        reasons.append("PREFLIGHT_RISK_PLAN_BINDING_MISMATCH")
    if command.approval.plan_hash != plan.plan_hash or command.approval.risk_input_hash != command.risk_input_hash:
        reasons.append("PREFLIGHT_APPROVAL_BINDING_MISMATCH")
    if command.approval.maximum_loss != economic_maximum_loss:
        reasons.append("PREFLIGHT_APPROVAL_LOSS_MISMATCH")
    if now > command.approval.expires_at:
        reasons.append("PREFLIGHT_APPROVAL_EXPIRED")
    if risk_input.mode not in {OperatingModeV1.PAPER_ARMED, OperatingModeV1.PAPER_DEMO_ARMED}:
        reasons.append("PREFLIGHT_MODE_NOT_ARMED")
    if now >= deployment.entry_cutoff_at:
        reasons.append("PREFLIGHT_ENTRY_CUTOFF_REACHED")
    if now + timedelta(minutes=60) > deployment.flatten_at:
        reasons.append("PREFLIGHT_HOLD_WINDOW_CROSSES_FLATTEN")
    if control.mode != risk_input.mode or control.mode not in {
        OperatingModeV1.PAPER_ARMED,
        OperatingModeV1.PAPER_DEMO_ARMED,
    }:
        reasons.append("PREFLIGHT_CURRENT_CONTROL_MODE_MISMATCH")
    if (
        risk_input.control_state_hash != control.content_hash
        or command.control_state_hash != control.content_hash
        or risk_input.control_state_version != control.version
        or command.control_state_version != control.version
    ):
        reasons.append("PREFLIGHT_CONTROL_STATE_MISMATCH")
    if control.account_id != deployment.expected_account_id:
        reasons.append("PREFLIGHT_CONTROL_ACCOUNT_MISMATCH")
    if control.release_hash != deployment.release_hash:
        reasons.append("PREFLIGHT_DEPLOYED_RELEASE_MISMATCH")
    if control.config_hash != deployment.strategy_config_hash:
        reasons.append("PREFLIGHT_DEPLOYED_STRATEGY_CONFIG_MISMATCH")
    if control.account_allowlist_hash != deployment.account_allowlist_hash:
        reasons.append("PREFLIGHT_DEPLOYED_ACCOUNT_ALLOWLIST_MISMATCH")
    if control.reconciled_at > now or now - control.reconciled_at > timedelta(seconds=15):
        reasons.append("PREFLIGHT_RECONCILIATION_STALE")
    if control.reconciliation_hash != reconciliation_hash(account, positions, order_risk):
        reasons.append("PREFLIGHT_RECONCILIATION_HASH_MISMATCH")
    reasons.extend(_deployment_reasons(risk_input, deployment))
    if command.account_snapshot_version != account.version or command.position_snapshot_version != positions.version:
        reasons.append("PREFLIGHT_STALE_RECONCILIATION_VERSION")
    if command.order_risk_snapshot_version != order_risk.version:
        reasons.append("PREFLIGHT_ORDER_RISK_VERSION")
    if (
        command.market_snapshot_hash != market.content_hash
        or risk_input.market_snapshot_hash != market.content_hash
    ):
        reasons.append("PREFLIGHT_MARKET_HASH_MISMATCH")
    if risk_input.account_snapshot_hash != account.content_hash:
        reasons.append("PREFLIGHT_ACCOUNT_HASH_MISMATCH")
    if risk_input.position_snapshot_hash != positions.content_hash:
        reasons.append("PREFLIGHT_POSITION_HASH_MISMATCH")
    if risk_input.order_risk_snapshot_hash != order_risk.content_hash:
        reasons.append("PREFLIGHT_ORDER_RISK_HASH_MISMATCH")
    if economic_maximum_loss > risk_input.risk_policy.max_per_trade_loss:
        reasons.append("PREFLIGHT_PER_TRADE_LIMIT")
    if economic_maximum_loss > risk_input.risk_policy.max_total_reserved_loss:
        reasons.append("PREFLIGHT_TOTAL_RESERVATION_LIMIT")
    if economic_maximum_loss > account.buying_power:
        reasons.append("PREFLIGHT_INSUFFICIENT_BUYING_POWER")
    if account.day_start_equity is None:
        reasons.append("PREFLIGHT_DAY_START_EQUITY_MISSING")
    else:
        realized_day_loss = max(account.day_start_equity - account.equity, Decimal("0"))
        if realized_day_loss + economic_maximum_loss > risk_input.risk_policy.max_daily_loss:
            reasons.append("PREFLIGHT_DAILY_LOSS_LIMIT")
    own_reservations = [item for item in order_risk.reservations if item.plan_hash == plan.plan_hash]
    foreign_reservations = [item for item in order_risk.reservations if item.plan_hash != plan.plan_hash]
    if (
        len(own_reservations) != 1
        or foreign_reservations
        or own_reservations[0].maximum_loss != economic_maximum_loss
        or own_reservations[0].remaining_quantity != plan.quantity
        or own_reservations[0].reservation_id != command.approval.reservation_id
        or own_reservations[0].status != "APPROVED"
        or own_reservations[0].expires_at != command.approval.expires_at
        or own_reservations[0].expires_at < now
    ):
        reasons.append("PREFLIGHT_RESERVATION_MISMATCH")
    quote = market.underlying_quotes.get(plan.underlying)
    if (
        quote is None
        or quote.event_time > now
        or quote.available_time > now
        or now - quote.event_time > timedelta(seconds=risk_input.risk_policy.quote_ttl_seconds)
    ):
        reasons.append("PREFLIGHT_STALE_QUOTE")
    option_contracts = {contract.symbol: contract for contract in market.option_contracts}
    for leg in plan.legs:
        contract = option_contracts.get(leg.symbol)
        if contract is None:
            reasons.append("PREFLIGHT_OPTION_QUOTE_MISSING")
            continue
        try:
            validate_occ_identity(
                symbol=leg.symbol,
                underlying=plan.underlying,
                right=leg.right,
                strike=leg.strike,
                expiration=leg.expiration,
            )
            validate_occ_identity(
                symbol=contract.symbol,
                underlying=contract.underlying,
                right=contract.right,
                strike=contract.strike,
                expiration=contract.expiration,
            )
        except ContractError:
            reasons.append("PREFLIGHT_OCC_IDENTITY_MISMATCH")
        if (
            contract.underlying != plan.underlying
            or contract.right != leg.right
            or contract.strike != leg.strike
            or contract.expiration != leg.expiration
        ):
            reasons.append("PREFLIGHT_OPTION_CONTRACT_MISMATCH")
        if contract.expiration <= now:
            reasons.append("PREFLIGHT_OPTION_EXPIRED")
        if contract.quote.ask <= 0 or contract.quote.ask < contract.quote.bid:
            reasons.append("PREFLIGHT_OPTION_QUOTE_INVALID")
        elif (
            contract.quote.event_time > now
            or contract.quote.available_time > now
            or now - contract.quote.event_time
            > timedelta(seconds=risk_input.risk_policy.quote_ttl_seconds)
        ):
            reasons.append("PREFLIGHT_STALE_OPTION_QUOTE")
    if plan.client_order_id in order_risk.working_client_order_ids:
        reasons.append("PREFLIGHT_CLIENT_ORDER_ALREADY_WORKING")
    reasons.extend(
        _market_clock_reasons(
            market,
            now=now,
            ttl_seconds=risk_input.risk_policy.quote_ttl_seconds,
        )
    )
    if market.clock is not None and now + timedelta(minutes=60) > market.clock.next_close:
        reasons.append("PREFLIGHT_HOLD_WINDOW_CROSSES_MARKET_CLOSE")
    reasons = list(dict.fromkeys(reasons))
    if not reasons:
        reasons.extend(
            broker.runtime_state_violations(
                account=account,
                positions=positions,
                order_risk=order_risk,
                market=market,
                now=now,
                quote_ttl_seconds=risk_input.risk_policy.quote_ttl_seconds,
            )
        )
        reasons = list(dict.fromkeys(reasons))
    allowed = not reasons
    preflight = ExecutionPreflightDecisionV1(
        command_hash=command.command_hash,
        allowed=allowed,
        reason_codes=tuple(reasons or ["PREFLIGHT_APPROVED"]),
        checked_at=now,
    )
    if not allowed:
        return ExecutionResult(preflight=preflight, broker_event=None)
    return ExecutionResult(preflight=preflight, broker_event=broker.submit(plan, now=now))


def preflight_and_submit_reduce_only(
    command: ExecuteReduceOnlyPlanV1,
    market: MarketSnapshotV1,
    account: AccountSnapshotV1,
    positions: PositionSnapshotV1,
    order_risk: OrderRiskSnapshotV1,
    control: ControlStateV1,
    managed: ManagedPositionV1,
    broker: ExecutionBroker,
    *,
    now: datetime,
    deployment: ExecutionDeploymentV1,
    quote_ttl_seconds: int,
) -> ExecutionResult:
    """Submit only after independently proving exact-leg exposure reduction."""
    now = now.astimezone(UTC)
    plan = command.plan
    reasons: list[str] = []
    if command.command_hash != hash_without(command, "command_hash"):
        reasons.append("PREFLIGHT_COMMAND_HASH_MISMATCH")
    if plan.plan_hash != hash_without(plan, "plan_hash"):
        reasons.append("PREFLIGHT_PLAN_HASH_MISMATCH")
    if command.approval.decision_hash != hash_without(command.approval, "decision_hash"):
        reasons.append("PREFLIGHT_APPROVAL_HASH_MISMATCH")
    if market.content_hash != hash_without(market, "content_hash"):
        reasons.append("PREFLIGHT_MARKET_CONTENT_HASH_MISMATCH")
    if account.content_hash != hash_without(account, "content_hash"):
        reasons.append("PREFLIGHT_ACCOUNT_CONTENT_HASH_MISMATCH")
    if positions.content_hash != hash_without(positions, "content_hash"):
        reasons.append("PREFLIGHT_POSITION_CONTENT_HASH_MISMATCH")
    if order_risk.content_hash != hash_without(order_risk, "content_hash"):
        reasons.append("PREFLIGHT_ORDER_RISK_CONTENT_HASH_MISMATCH")
    if control.content_hash != hash_without(control, "content_hash"):
        reasons.append("PREFLIGHT_CONTROL_CONTENT_HASH_MISMATCH")
    if deployment.content_hash != hash_without(deployment, "content_hash"):
        reasons.append("PREFLIGHT_DEPLOYMENT_CONTENT_HASH_MISMATCH")
    if not _is_exact_paper_endpoint(deployment.paper_base_url):
        reasons.append("PREFLIGHT_NON_PAPER_HOST")
    if account.account_id != deployment.expected_account_id or plan.account_id != deployment.expected_account_id:
        reasons.append("PREFLIGHT_ACCOUNT_MISMATCH")
    if positions.account_id != account.account_id or order_risk.account_id != account.account_id:
        reasons.append("PREFLIGHT_CROSS_ACCOUNT_SNAPSHOT")
    if control.account_id != deployment.expected_account_id:
        reasons.append("PREFLIGHT_CONTROL_ACCOUNT_MISMATCH")
    if control.reconciliation_hash != reconciliation_hash(account, positions, order_risk):
        reasons.append("PREFLIGHT_RECONCILIATION_HASH_MISMATCH")
    if control.mode not in {
        OperatingModeV1.PAPER_ARMED,
        OperatingModeV1.PAPER_DEMO_ARMED,
        OperatingModeV1.FLATTENING,
        OperatingModeV1.HALTED,
    }:
        reasons.append("PREFLIGHT_REDUCE_ONLY_MODE_NOT_ALLOWED")
    if (
        command.control_state_hash != control.content_hash
        or command.control_state_version != control.version
        or plan.control_state_version != control.version
        or command.approval.control_state_hash != control.content_hash
    ):
        reasons.append("PREFLIGHT_CONTROL_STATE_MISMATCH")
    if control.release_hash != deployment.release_hash:
        reasons.append("PREFLIGHT_DEPLOYED_RELEASE_MISMATCH")
    if control.config_hash != deployment.strategy_config_hash:
        reasons.append("PREFLIGHT_DEPLOYED_STRATEGY_CONFIG_MISMATCH")
    if control.account_allowlist_hash != deployment.account_allowlist_hash:
        reasons.append("PREFLIGHT_DEPLOYED_ACCOUNT_ALLOWLIST_MISMATCH")
    if now > command.approval.expires_at:
        reasons.append("PREFLIGHT_APPROVAL_EXPIRED")
    if command.approval.plan_hash != plan.plan_hash:
        reasons.append("PREFLIGHT_APPROVAL_BINDING_MISMATCH")
    if command.approval.position_snapshot_hash != positions.content_hash:
        reasons.append("PREFLIGHT_POSITION_HASH_MISMATCH")
    if command.approval.managed_position_hash != managed.content_hash:
        reasons.append("PREFLIGHT_MANAGED_POSITION_HASH_MISMATCH")
    if command.market_snapshot_hash != market.content_hash or plan.market_snapshot_hash != market.content_hash:
        reasons.append("PREFLIGHT_MARKET_HASH_MISMATCH")
    if command.account_snapshot_version != account.version:
        reasons.append("PREFLIGHT_ACCOUNT_VERSION_MISMATCH")
    if command.position_snapshot_version != positions.version or plan.position_snapshot_version != positions.version:
        reasons.append("PREFLIGHT_POSITION_VERSION_MISMATCH")
    reasons.extend(reduce_only_violations(plan, managed, positions))
    contracts = {item.symbol: item for item in market.option_contracts}
    recomputed_close_value = Decimal("0")
    close_quotes_complete = True
    for leg in plan.legs:
        contract = contracts.get(leg.symbol)
        if contract is None:
            reasons.append("PREFLIGHT_OPTION_QUOTE_MISSING")
            close_quotes_complete = False
            continue
        try:
            validate_occ_identity(
                symbol=leg.symbol,
                underlying=plan.underlying,
                right=leg.right,
                strike=leg.strike,
                expiration=leg.expiration,
            )
            validate_occ_identity(
                symbol=contract.symbol,
                underlying=contract.underlying,
                right=contract.right,
                strike=contract.strike,
                expiration=contract.expiration,
            )
        except ContractError:
            reasons.append("PREFLIGHT_OCC_IDENTITY_MISMATCH")
        if (
            contract.quote.event_time > now
            or contract.quote.available_time > now
            or now - contract.quote.event_time > timedelta(seconds=quote_ttl_seconds)
        ):
            reasons.append("PREFLIGHT_STALE_OPTION_QUOTE")
        recomputed_close_value += (
            contract.quote.bid if leg.side == "SELL" else -contract.quote.ask
        )
    if close_quotes_complete:
        recomputed_effect = "CREDIT" if recomputed_close_value >= 0 else "DEBIT"
        recomputed_limit = max(abs(recomputed_close_value), Decimal("0.01"))
        if plan.price_effect != recomputed_effect or plan.limit_price != recomputed_limit:
            reasons.append("PREFLIGHT_REDUCE_ONLY_PRICE_MISMATCH")
    if plan.client_order_id in order_risk.working_client_order_ids:
        reasons.append("PREFLIGHT_CLIENT_ORDER_ALREADY_WORKING")
    reasons.extend(_market_clock_reasons(market, now=now, ttl_seconds=quote_ttl_seconds))
    reasons = list(dict.fromkeys(reasons))
    if not reasons:
        reasons.extend(
            broker.runtime_state_violations(
                account=account,
                positions=positions,
                order_risk=order_risk,
                market=market,
                now=now,
                quote_ttl_seconds=quote_ttl_seconds,
            )
        )
        reasons = list(dict.fromkeys(reasons))
    allowed = not reasons
    preflight = ExecutionPreflightDecisionV1(
        command_hash=command.command_hash,
        allowed=allowed,
        reason_codes=tuple(reasons or ["PREFLIGHT_REDUCE_ONLY_APPROVED"]),
        checked_at=now,
    )
    if not allowed:
        return ExecutionResult(preflight=preflight, broker_event=None)
    return ExecutionResult(preflight=preflight, broker_event=broker.submit(plan, now=now))
