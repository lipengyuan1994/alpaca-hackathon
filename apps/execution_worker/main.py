"""Private claimed-outbox execution worker with reconcile-before-submit semantics."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from packages.alpaca_execution_mcp import AlpacaPaperExecutionAdapter
from packages.contracts.models import (
    AccountSnapshotV1,
    BrokerEventV1,
    ControlStateV1,
    ExecuteApprovedPlanV1,
    ExecuteReduceOnlyPlanV1,
    ExecutionBundleV1,
    ExecutionDeploymentV1,
    MarketSnapshotV1,
    OrderRiskSnapshotV1,
    PositionSnapshotV1,
    RiskInputV1,
)
from packages.execution_core import (
    ExecutionResult,
    preflight_and_submit,
    preflight_and_submit_reduce_only,
)
from packages.ledger import PostgresRuntimeLedger


@dataclass(frozen=True)
class WorkerOutcome:
    message_id: str | None
    status: str
    broker_event: BrokerEventV1 | None = None


class _SubmissionFenceBroker:
    """Persist reconcile-only state immediately before the external submit call."""

    def __init__(self, broker: Any, ledger: Any, *, message_id: str, worker_id: str) -> None:
        self._broker = broker
        self._ledger = ledger
        self._message_id = message_id
        self._worker_id = worker_id

    def runtime_state_violations(self, **kwargs: Any) -> tuple[str, ...]:
        return self._broker.runtime_state_violations(**kwargs)

    def reconcile(self, client_order_id: str, *, now: datetime) -> BrokerEventV1 | None:
        return self._broker.reconcile(client_order_id, now=now)

    def submit(self, plan: Any, *, now: datetime) -> BrokerEventV1:
        self._ledger.mark_submission_started(
            message_id=self._message_id,
            worker_id=self._worker_id,
            now=now,
        )
        return self._broker.submit(plan, now=now)


def process_approved_command(
    command: ExecuteApprovedPlanV1,
    risk_input: RiskInputV1,
    market: MarketSnapshotV1,
    account: AccountSnapshotV1,
    positions: PositionSnapshotV1,
    order_risk: OrderRiskSnapshotV1,
    control: ControlStateV1,
    *,
    broker: Any,
    now: datetime,
    deployment: ExecutionDeploymentV1,
) -> ExecutionResult:
    """The sole entry mutation boundary, preceded by independent preflight."""
    return preflight_and_submit(
        command,
        risk_input,
        market,
        account,
        positions,
        order_risk,
        control,
        broker,
        now=now,
        deployment=deployment,
    )


def process_reduce_only_command(
    bundle: ExecutionBundleV1,
    *,
    broker: Any,
    now: datetime,
    deployment: ExecutionDeploymentV1,
    quote_ttl_seconds: int,
) -> ExecutionResult:
    if not isinstance(bundle.command, ExecuteReduceOnlyPlanV1) or bundle.managed_position is None:
        raise ValueError("REDUCE_ONLY_EXECUTION_BUNDLE_REQUIRED")
    return preflight_and_submit_reduce_only(
        bundle.command,
        bundle.market,
        bundle.account,
        bundle.positions,
        bundle.order_risk,
        bundle.control_state,
        bundle.managed_position,
        broker,
        now=now,
        deployment=deployment,
        quote_ttl_seconds=quote_ttl_seconds,
    )


class ExecutionWorker:
    """One account-leased, idempotent worker over a durable ledger protocol."""

    def __init__(
        self,
        *,
        ledger: Any,
        broker: Any,
        deployment: ExecutionDeploymentV1,
        worker_id: str,
        quote_ttl_seconds: int,
    ) -> None:
        self._ledger = ledger
        self._broker = broker
        self._deployment = deployment
        self._worker_id = worker_id
        self._quote_ttl_seconds = quote_ttl_seconds

    @staticmethod
    def _claim_parts(claim: Any) -> tuple[str, ExecutionBundleV1, str | None, str]:
        if hasattr(claim, "bundle"):
            return claim.message_id, claim.bundle, claim.last_error, claim.submission_state
        message = claim.message
        if message.bundle is None:
            raise ValueError("EXECUTION_OUTBOX_BUNDLE_MISSING")
        return message.message_id, message.bundle, claim.last_error, claim.submission_state

    def process_once(self, *, now: datetime) -> WorkerOutcome:
        now = now.astimezone(UTC)
        claim = self._ledger.claim_next_outbox(
            worker_id=self._worker_id,
            now=now,
            lease_seconds=120,
        )
        if claim is None:
            return WorkerOutcome(message_id=None, status="IDLE")
        message_id, bundle, last_error, submission_state = self._claim_parts(claim)
        account_id = bundle.account.account_id
        if not self._ledger.acquire_account_lease(
            account_id=account_id,
            worker_id=self._worker_id,
            now=now,
            lease_seconds=120,
        ):
            self._ledger.release_outbox(
                message_id=message_id,
                worker_id=self._worker_id,
                error="EXECUTION_ACCOUNT_LEASE_BUSY",
                now=now,
                retry_after_seconds=1,
            )
            return WorkerOutcome(message_id=message_id, status="LEASE_BUSY")
        try:
            current_control = self._ledger.current_control_state(account_id)
            if current_control is None:
                self._ledger.release_outbox(
                    message_id=message_id,
                    worker_id=self._worker_id,
                    error="EXECUTION_CONTROL_STATE_UNAVAILABLE",
                    now=now,
                    retry_after_seconds=5,
                )
                return WorkerOutcome(message_id=message_id, status="CONTROL_STATE_UNAVAILABLE")
            client_order_id = bundle.command.plan.client_order_id
            reconciled = self._broker.reconcile(client_order_id, now=now)
            if reconciled is not None:
                self._ledger.record_broker_event(reconciled)
                if reconciled.status == "UNKNOWN":
                    self._ledger.release_outbox(
                        message_id=message_id,
                        worker_id=self._worker_id,
                        error="EXECUTION_RECONCILIATION_UNKNOWN",
                        now=now,
                        retry_after_seconds=5,
                    )
                    return WorkerOutcome(
                        message_id=message_id,
                        status="RECONCILIATION_REQUIRED",
                        broker_event=reconciled,
                    )
                if (
                    isinstance(bundle.command, ExecuteApprovedPlanV1)
                    and reconciled.status in {"ACCEPTED", "PARTIAL"}
                    and now >= self._deployment.flatten_at
                ):
                    cancelled = self._broker.cancel(client_order_id, now=now)
                    self._ledger.record_broker_event(cancelled)
                    if cancelled.status in {"UNKNOWN", "ACCEPTED", "PARTIAL"}:
                        self._ledger.release_outbox(
                            message_id=message_id,
                            worker_id=self._worker_id,
                            error="EXECUTION_FLATTEN_CANCEL_RECONCILIATION_REQUIRED",
                            now=now,
                            retry_after_seconds=1,
                        )
                        return WorkerOutcome(
                            message_id=message_id,
                            status="RECONCILIATION_REQUIRED",
                            broker_event=cancelled,
                        )
                    self._ledger.complete_outbox(
                        message_id=message_id,
                        worker_id=self._worker_id,
                        broker_event=cancelled,
                        bundle=bundle,
                    )
                    return WorkerOutcome(
                        message_id=message_id,
                        status=(
                            "ENTRY_FILLED_DURING_FLATTEN"
                            if cancelled.status == "FILLED"
                            else "ENTRY_CANCELLED_FOR_FLATTEN"
                        ),
                        broker_event=cancelled,
                    )
                if reconciled.status in {"ACCEPTED", "PARTIAL"}:
                    self._ledger.release_outbox(
                        message_id=message_id,
                        worker_id=self._worker_id,
                        error="EXECUTION_AWAITING_TERMINAL",
                        now=now,
                        retry_after_seconds=5,
                    )
                    return WorkerOutcome(
                        message_id=message_id,
                        status="AWAITING_TERMINAL",
                        broker_event=reconciled,
                    )
                self._ledger.complete_outbox(
                    message_id=message_id,
                    worker_id=self._worker_id,
                    broker_event=reconciled,
                    bundle=bundle,
                )
                return WorkerOutcome(message_id=message_id, status="RECONCILED", broker_event=reconciled)

            if submission_state != "READY" or last_error in {
                "EXECUTION_SUBMISSION_UNKNOWN",
                "EXECUTION_RECONCILIATION_UNKNOWN",
                "EXECUTION_RECONCILIATION_NOT_FOUND",
            }:
                self._ledger.release_outbox(
                    message_id=message_id,
                    worker_id=self._worker_id,
                    error="EXECUTION_RECONCILIATION_NOT_FOUND",
                    now=now,
                    retry_after_seconds=5,
                )
                return WorkerOutcome(message_id=message_id, status="RECONCILIATION_REQUIRED")

            with self._ledger.control_state_guard(account_id) as guarded_control:
                if guarded_control is None:
                    raise RuntimeError("EXECUTION_CONTROL_STATE_UNAVAILABLE")
                if isinstance(bundle.command, ExecuteApprovedPlanV1):
                    if bundle.risk_input is None:
                        raise ValueError("EXECUTION_RISK_INPUT_MISSING")
                    fenced_broker = _SubmissionFenceBroker(
                        self._broker,
                        self._ledger,
                        message_id=message_id,
                        worker_id=self._worker_id,
                    )
                    result = process_approved_command(
                        bundle.command,
                        bundle.risk_input,
                        bundle.market,
                        bundle.account,
                        bundle.positions,
                        bundle.order_risk,
                        guarded_control,
                        broker=fenced_broker,
                        now=now,
                        deployment=self._deployment,
                    )
                else:
                    current_bundle = bundle.model_copy(update={"control_state": guarded_control})
                    fenced_broker = _SubmissionFenceBroker(
                        self._broker,
                        self._ledger,
                        message_id=message_id,
                        worker_id=self._worker_id,
                    )
                    result = process_reduce_only_command(
                        current_bundle,
                        broker=fenced_broker,
                        now=now,
                        deployment=self._deployment,
                        quote_ttl_seconds=self._quote_ttl_seconds,
                    )
            if not result.preflight.allowed:
                rejected = BrokerEventV1(
                    client_order_id=client_order_id,
                    status="REJECTED",
                    occurred_at=now,
                    reason_code="EXECUTION_PREFLIGHT_REJECTED",
                )
                self._ledger.complete_outbox(
                    message_id=message_id,
                    worker_id=self._worker_id,
                    broker_event=rejected,
                    bundle=bundle,
                )
                return WorkerOutcome(message_id=message_id, status="PREFLIGHT_REJECTED", broker_event=rejected)
            if result.broker_event is None:  # pragma: no cover - result invariant
                raise RuntimeError("EXECUTION_ALLOWED_WITHOUT_BROKER_EVENT")
            self._ledger.record_broker_event(result.broker_event)
            if result.broker_event.status == "UNKNOWN":
                self._ledger.release_outbox(
                    message_id=message_id,
                    worker_id=self._worker_id,
                    error="EXECUTION_SUBMISSION_UNKNOWN",
                    now=now,
                    retry_after_seconds=5,
                )
                return WorkerOutcome(
                    message_id=message_id,
                    status="RECONCILIATION_REQUIRED",
                    broker_event=result.broker_event,
                )
            if result.broker_event.status in {"ACCEPTED", "PARTIAL"}:
                self._ledger.release_outbox(
                    message_id=message_id,
                    worker_id=self._worker_id,
                    error="EXECUTION_AWAITING_TERMINAL",
                    now=now,
                    retry_after_seconds=5,
                )
                return WorkerOutcome(
                    message_id=message_id,
                    status="AWAITING_TERMINAL",
                    broker_event=result.broker_event,
                )
            self._ledger.complete_outbox(
                message_id=message_id,
                worker_id=self._worker_id,
                broker_event=result.broker_event,
                bundle=bundle,
            )
            return WorkerOutcome(message_id=message_id, status="SUBMITTED", broker_event=result.broker_event)
        except Exception as exc:
            self._ledger.release_outbox(
                message_id=message_id,
                worker_id=self._worker_id,
                error=type(exc).__name__,
                now=now,
                retry_after_seconds=5,
            )
            raise
        finally:
            self._ledger.release_account_lease(account_id=account_id, worker_id=self._worker_id)


def deployment_from_environment() -> ExecutionDeploymentV1:
    required = {
        "expected_account_id": "PAPER_ACCOUNT_ID",
        "risk_policy_hash": "RISK_POLICY_HASH",
        "template_catalog_hash": "TEMPLATE_CATALOG_HASH",
        "strategy_registry_hash": "STRATEGY_REGISTRY_HASH",
        "strategy_config_hash": "STRATEGY_CONFIG_HASH",
        "strategy_content_hash": "STRATEGY_CONTENT_HASH",
        "account_allowlist_hash": "ACCOUNT_ALLOWLIST_HASH",
        "release_hash": "RELEASE_HASH",
        "entry_cutoff_at": "ENTRY_CUTOFF_AT",
        "flatten_at": "FLATTEN_AT",
        "flat_deadline_at": "FLAT_DEADLINE_AT",
    }
    values = {field: os.environ.get(env_name) for field, env_name in required.items()}
    missing = [env_name for field, env_name in required.items() if not values[field]]
    if missing:
        raise RuntimeError(f"EXECUTION_DEPLOYMENT_ENV_MISSING:{','.join(sorted(missing))}")
    return ExecutionDeploymentV1(
        **values,
        paper_base_url=os.environ.get("PAPER_API_BASE_URL", "https://paper-api.alpaca.markets"),
    )


def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required for the durable execution worker")
    worker_id = os.environ.get("EXECUTION_WORKER_ID", "execution-worker-1")
    deployment = deployment_from_environment()
    ledger = PostgresRuntimeLedger.from_dsn(database_url)
    broker = AlpacaPaperExecutionAdapter.from_environment()
    worker = ExecutionWorker(
        ledger=ledger,
        broker=broker,
        deployment=deployment,
        worker_id=worker_id,
        quote_ttl_seconds=int(os.environ.get("OPTION_QUOTE_TTL_SECONDS", "30")),
    )
    while True:
        outcome = worker.process_once(now=datetime.now(UTC))
        if outcome.status == "IDLE":
            time.sleep(0.5)


if __name__ == "__main__":
    main()
