"""Postgres runtime ledger with transactional risk/outbox and leased consumption."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Iterator

from packages.contracts.canonical import canonical_json
from packages.contracts.models import (
    AccountSnapshotV1,
    BrokerEventV1,
    ControlCommandV1,
    ControlStateV1,
    DecisionJobV1,
    EventEnvelopeV1,
    ExecuteApprovedPlanV1,
    ExecutionBundleV1,
    ManagedPositionV1,
    OrderRiskSnapshotV1,
    PositionSnapshotV1,
)
from packages.domain import (
    apply_control_command as transition_control_state,
)
from packages.domain import (
    reconciliation_hash,
)

from .store import LedgerError


@dataclass(frozen=True)
class PostgresClaim:
    message_id: str
    bundle: ExecutionBundleV1
    worker_id: str
    lease_until: datetime
    last_error: str | None = None
    submission_state: str = "READY"


@dataclass(frozen=True)
class PostgresDecisionClaim:
    job: DecisionJobV1
    worker_id: str
    lease_until: datetime


class PostgresRuntimeLedger:
    """Small DB-API wrapper; the execution image supplies the psycopg connection."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    @classmethod
    def from_dsn(cls, dsn: str) -> "PostgresRuntimeLedger":
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - deployment dependency check
            raise LedgerError("POSTGRES_DRIVER_NOT_INSTALLED") from exc
        return cls(psycopg.connect(dsn))

    @staticmethod
    def _json(value: Any) -> str:
        return canonical_json(value)

    def reserve_and_enqueue(
        self,
        *,
        bundle: ExecutionBundleV1,
        prospective_order_risk: OrderRiskSnapshotV1,
        expected_prior_order_risk_version: int,
        expected_prior_control_state: ControlStateV1,
        event: EventEnvelopeV1,
    ) -> str:
        """CAS risk capacity and persist reservation/event/outbox in one transaction."""
        if not isinstance(bundle.command, ExecuteApprovedPlanV1):
            raise LedgerError("POSTGRES_APPROVED_ENTRY_COMMAND_REQUIRED")
        command = bundle.command
        account_id = command.plan.account_id
        if prospective_order_risk.account_id != account_id:
            raise LedgerError("POSTGRES_ORDER_RISK_ACCOUNT_MISMATCH")
        if bundle.positions.account_id != account_id or bundle.account.account_id != account_id:
            raise LedgerError("POSTGRES_CROSS_ACCOUNT_BUNDLE")
        with self._connection.transaction():
            self._connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (account_id,),
            )
            self._require_control_state(expected_prior_control_state)
            self._validate_reservation_control_transition(
                expected_prior_control_state,
                bundle.control_state,
                bundle,
                prospective_order_risk,
            )
            row = self._connection.execute(
                "SELECT version FROM order_risk_state_v1 WHERE account_id = %s FOR UPDATE",
                (account_id,),
            ).fetchone()
            if row is None:
                raise LedgerError("POSTGRES_ORDER_RISK_STATE_UNAVAILABLE")
            actual_prior = row[0]
            if actual_prior != expected_prior_order_risk_version:
                raise LedgerError("POSTGRES_ORDER_RISK_CAS_CONFLICT")
            if prospective_order_risk.version != expected_prior_order_risk_version + 1:
                raise LedgerError("POSTGRES_ORDER_RISK_VERSION_NOT_NEXT")
            own = [
                item
                for item in prospective_order_risk.reservations
                if item.plan_hash == command.plan.plan_hash
            ]
            if (
                len(own) != 1
                or own[0].maximum_loss != command.plan.maximum_loss
                or own[0].reservation_id != command.approval.reservation_id
                or own[0].status != "APPROVED"
                or own[0].expires_at != command.approval.expires_at
            ):
                raise LedgerError("POSTGRES_RESERVATION_NOT_BOUND")

            updated = self._connection.execute(
                """
                INSERT INTO order_risk_state_v1(account_id, version, content_hash, payload, updated_at)
                VALUES (%s, %s, %s, %s::jsonb, now())
                ON CONFLICT (account_id) DO UPDATE SET
                    version = EXCLUDED.version,
                    content_hash = EXCLUDED.content_hash,
                    payload = EXCLUDED.payload,
                    updated_at = now()
                WHERE order_risk_state_v1.version = %s
                RETURNING version
                """,
                (
                    account_id,
                    prospective_order_risk.version,
                    prospective_order_risk.content_hash,
                    self._json(prospective_order_risk),
                    expected_prior_order_risk_version,
                ),
            ).fetchone()
            if updated is None:
                raise LedgerError("POSTGRES_ORDER_RISK_CAS_CONFLICT")
            control_updated = self._connection.execute(
                """
                UPDATE control_state_v1 SET
                    version = %s,
                    reconciliation_hash = %s,
                    reconciled_at = %s,
                    content_hash = %s,
                    payload = %s::jsonb,
                    updated_at = now()
                WHERE account_id = %s AND version = %s AND content_hash = %s
                RETURNING version
                """,
                (
                    bundle.control_state.version,
                    bundle.control_state.reconciliation_hash,
                    bundle.control_state.reconciled_at,
                    bundle.control_state.content_hash,
                    self._json(bundle.control_state),
                    account_id,
                    expected_prior_control_state.version,
                    expected_prior_control_state.content_hash,
                ),
            ).fetchone()
            if control_updated is None:
                raise LedgerError("POSTGRES_CONTROL_STATE_CAS_CONFLICT")
            reservation = own[0]
            self._connection.execute(
                """
                INSERT INTO risk_reservations_v1(
                    reservation_id, account_id, plan_hash, maximum_loss,
                    remaining_quantity, status, expires_at, payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    reservation.reservation_id,
                    account_id,
                    reservation.plan_hash,
                    reservation.maximum_loss,
                    reservation.remaining_quantity,
                    reservation.status,
                    reservation.expires_at,
                    self._json(reservation),
                ),
            )
            self._connection.execute(
                """
                INSERT INTO order_uniqueness_v1(account_id, client_order_id, intent_id, plan_hash)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    account_id,
                    command.plan.client_order_id,
                    command.plan.intent_id,
                    command.plan.plan_hash,
                ),
            )
            self._insert_event(event)
            message_id = f"outbox-{command.command_hash.removeprefix('sha256:')[:24]}"
            self._connection.execute(
                """
                INSERT INTO outbox_v1(message_id, command_hash, topic, payload)
                VALUES (%s, %s, 'execute-approved-plan/v1', %s::jsonb)
                """,
                (message_id, command.command_hash, self._json(bundle)),
            )
        return message_id

    def enqueue_decision_job(self, job: DecisionJobV1) -> str:
        """Publish one sanitized, credential-free decision input."""
        self._connection.execute(
            """
            INSERT INTO decision_jobs_v1(job_id, job_hash, payload)
            VALUES (%s, %s, %s::jsonb)
            """,
            (job.job_id, job.job_hash, self._json(job)),
        )
        self._connection.commit()
        return job.job_id

    def claim_next_decision_job(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int = 120,
    ) -> PostgresDecisionClaim | None:
        now = now.astimezone(UTC)
        lease_until = now + timedelta(seconds=lease_seconds)
        with self._connection.transaction():
            row = self._connection.execute(
                """
                SELECT job_id, payload FROM decision_jobs_v1
                WHERE processed_at IS NULL
                  AND next_attempt_at <= %s
                  AND (lease_until IS NULL OR lease_until <= %s)
                ORDER BY next_attempt_at, created_at, job_id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """,
                (now, now),
            ).fetchone()
            if row is None:
                return None
            job_id, payload = row
            self._connection.execute(
                """
                UPDATE decision_jobs_v1
                SET lease_owner = %s, lease_until = %s, attempts = attempts + 1
                WHERE job_id = %s
                """,
                (worker_id, lease_until, job_id),
            )
        return PostgresDecisionClaim(
            job=DecisionJobV1.model_validate(payload),
            worker_id=worker_id,
            lease_until=lease_until,
        )

    def complete_decision_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        result_status: str,
    ) -> None:
        updated = self._connection.execute(
            """
            UPDATE decision_jobs_v1
            SET processed_at = now(), result_status = %s,
                lease_owner = NULL, lease_until = NULL, last_error = NULL
            WHERE job_id = %s AND lease_owner = %s AND processed_at IS NULL
            RETURNING job_id
            """,
            (result_status, job_id, worker_id),
        ).fetchone()
        self._connection.commit()
        if updated is None:
            raise LedgerError("POSTGRES_DECISION_JOB_CLAIM_MISMATCH")

    def release_decision_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        error: str,
        now: datetime,
        retry_after_seconds: int = 5,
    ) -> None:
        self._connection.execute(
            """
            UPDATE decision_jobs_v1
            SET lease_owner = NULL, lease_until = NULL, last_error = %s, next_attempt_at = %s
            WHERE job_id = %s AND lease_owner = %s AND processed_at IS NULL
            """,
            (
                error,
                now.astimezone(UTC) + timedelta(seconds=retry_after_seconds),
                job_id,
                worker_id,
            ),
        )
        self._connection.commit()

    def enqueue_reduce_only(self, *, bundle: ExecutionBundleV1, event: EventEnvelopeV1) -> str:
        command = bundle.command
        if isinstance(command, ExecuteApprovedPlanV1):
            raise LedgerError("POSTGRES_REDUCE_ONLY_COMMAND_REQUIRED")
        with self._connection.transaction():
            self._connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (command.plan.account_id,),
            )
            self._require_control_state(bundle.control_state)
            managed = bundle.managed_position
            if managed is None:
                raise LedgerError("POSTGRES_MANAGED_POSITION_REQUIRED")
            row = self._connection.execute(
                """
                SELECT content_hash, status FROM managed_positions_v1
                WHERE strategy_position_id = %s FOR UPDATE
                """,
                (managed.strategy_position_id,),
            ).fetchone()
            if row is None or row[0] != managed.content_hash:
                raise LedgerError("POSTGRES_MANAGED_POSITION_STALE")
            if row[1] != "OPEN":
                raise LedgerError("POSTGRES_MANAGED_POSITION_NOT_OPEN")
            closing = ManagedPositionV1.model_validate(
                managed.model_dump(mode="json", exclude={"content_hash"}) | {"status": "CLOSING"}
            )
            self._connection.execute(
                """
                UPDATE managed_positions_v1
                SET status = 'CLOSING', content_hash = %s, payload = %s::jsonb, updated_at = now()
                WHERE strategy_position_id = %s AND content_hash = %s
                """,
                (
                    closing.content_hash,
                    self._json(closing),
                    managed.strategy_position_id,
                    managed.content_hash,
                ),
            )
            self._connection.execute(
                """
                INSERT INTO order_uniqueness_v1(account_id, client_order_id, intent_id, plan_hash)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    command.plan.account_id,
                    command.plan.client_order_id,
                    f"exit-{command.plan.strategy_position_id}",
                    command.plan.plan_hash,
                ),
            )
            self._insert_event(event)
            message_id = f"outbox-{command.command_hash.removeprefix('sha256:')[:24]}"
            self._connection.execute(
                """
                INSERT INTO outbox_v1(message_id, command_hash, topic, payload, priority)
                VALUES (%s, %s, 'execute-reduce-only-plan/v1', %s::jsonb, 0)
                """,
                (message_id, command.command_hash, self._json(bundle)),
            )
        return message_id

    def _insert_event(self, event: EventEnvelopeV1) -> None:
        self._connection.execute(
            """
            INSERT INTO events_v1(
                event_id, aggregate_id, aggregate_version, event_type,
                run_id, occurred_at, payload, content_hash
            ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                event.event_id,
                event.aggregate_id,
                event.aggregate_version,
                event.event_type,
                event.run_id,
                event.occurred_at,
                self._json(event.payload),
                event.content_hash,
            ),
        )

    def _require_control_state(self, expected: ControlStateV1) -> None:
        row = self._connection.execute(
            "SELECT version, content_hash FROM control_state_v1 WHERE account_id = %s FOR SHARE",
            (expected.account_id,),
        ).fetchone()
        if row is None or row[0] != expected.version or row[1] != expected.content_hash:
            raise LedgerError("POSTGRES_CONTROL_STATE_STALE")

    @staticmethod
    def _validate_reservation_control_transition(
        prior: ControlStateV1,
        prospective: ControlStateV1,
        bundle: ExecutionBundleV1,
        order_risk: OrderRiskSnapshotV1,
    ) -> None:
        authority_fields = ("account_id", "mode", "release_hash", "config_hash", "account_allowlist_hash")
        if any(getattr(prior, field) != getattr(prospective, field) for field in authority_fields):
            raise LedgerError("POSTGRES_CONTROL_AUTHORITY_CHANGED_BY_RESERVATION")
        if prospective.version != prior.version + 1:
            raise LedgerError("POSTGRES_CONTROL_STATE_VERSION_NOT_NEXT")
        expected_reconciliation = reconciliation_hash(bundle.account, bundle.positions, order_risk)
        if prospective.reconciliation_hash != expected_reconciliation:
            raise LedgerError("POSTGRES_PROSPECTIVE_RECONCILIATION_MISMATCH")

    def initialize_control_state(self, control: ControlStateV1) -> None:
        """Create the sole version-zero DISARMED state during deployment bootstrap."""
        if control.version != 0 or control.mode.value != "DISARMED":
            raise LedgerError("POSTGRES_CONTROL_BOOTSTRAP_MUST_BE_DISARMED_V0")
        self._connection.execute(
            """
            INSERT INTO control_state_v1(
                account_id, version, mode, release_hash, config_hash,
                account_allowlist_hash, reconciliation_hash, reconciled_at,
                content_hash, payload
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                control.account_id,
                control.version,
                control.mode,
                control.release_hash,
                control.config_hash,
                control.account_allowlist_hash,
                control.reconciliation_hash,
                control.reconciled_at,
                control.content_hash,
                self._json(control),
            ),
        )
        self._connection.commit()

    def initialize_order_risk_state(self, order_risk: OrderRiskSnapshotV1) -> None:
        """Bootstrap the first reconciled order-risk snapshot before arming."""
        self._connection.execute(
            """
            INSERT INTO order_risk_state_v1(account_id, version, content_hash, payload)
            VALUES (%s, %s, %s, %s::jsonb)
            """,
            (
                order_risk.account_id,
                order_risk.version,
                order_risk.content_hash,
                self._json(order_risk),
            ),
        )
        self._connection.commit()

    def apply_control_command(
        self,
        command: ControlCommandV1,
        *,
        now: datetime,
        account_is_flat: bool,
        no_working_or_unknown_orders: bool,
    ) -> ControlStateV1:
        """Persist only a nonce-protected legal transition from current state."""
        with self._connection.transaction():
            self._connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (command.account_id,),
            )
            current = self._connection.execute(
                "SELECT payload FROM control_state_v1 WHERE account_id = %s FOR UPDATE",
                (command.account_id,),
            ).fetchone()
            if current is None:
                raise LedgerError("POSTGRES_CONTROL_STATE_UNAVAILABLE")
            state = ControlStateV1.model_validate(current[0])
            nonce_seen = self._connection.execute(
                "SELECT 1 FROM control_nonces_v1 WHERE nonce = %s",
                (command.nonce,),
            ).fetchone()
            used_nonces = {str(command.nonce)} if nonce_seen is not None else set()
            next_state = transition_control_state(
                state,
                command,
                now=now,
                used_nonces=used_nonces,
                account_is_flat=account_is_flat,
                no_working_or_unknown_orders=no_working_or_unknown_orders,
            )
            self._connection.execute(
                """
                INSERT INTO control_nonces_v1(nonce, command_hash, operator_id)
                VALUES (%s, %s, %s)
                """,
                (command.nonce, command.command_hash, command.operator_id),
            )
            updated = self._connection.execute(
                """
                UPDATE control_state_v1 SET
                    version = %s,
                    mode = %s,
                    release_hash = %s,
                    config_hash = %s,
                    account_allowlist_hash = %s,
                    reconciliation_hash = %s,
                    reconciled_at = %s,
                    content_hash = %s,
                    payload = %s::jsonb,
                    updated_at = now()
                WHERE account_id = %s AND version = %s AND content_hash = %s
                RETURNING version
                """,
                (
                    next_state.version,
                    next_state.mode,
                    next_state.release_hash,
                    next_state.config_hash,
                    next_state.account_allowlist_hash,
                    next_state.reconciliation_hash,
                    next_state.reconciled_at,
                    next_state.content_hash,
                    self._json(next_state),
                    state.account_id,
                    state.version,
                    state.content_hash,
                ),
            ).fetchone()
            if updated is None:
                raise LedgerError("POSTGRES_CONTROL_STATE_CAS_CONFLICT")
        return next_state

    def current_control_state(self, account_id: str) -> ControlStateV1 | None:
        row = self._connection.execute(
            "SELECT payload FROM control_state_v1 WHERE account_id = %s",
            (account_id,),
        ).fetchone()
        if row is None:
            return None
        return ControlStateV1.model_validate(row[0])

    @contextmanager
    def control_state_guard(self, account_id: str) -> Iterator[ControlStateV1 | None]:
        """Hold a session lock across fence commit and the external submit call."""
        self._connection.execute(
            "SELECT pg_advisory_lock(hashtextextended(%s, 0))",
            (account_id,),
        )
        self._connection.commit()
        try:
            row = self._connection.execute(
                "SELECT payload FROM control_state_v1 WHERE account_id = %s",
                (account_id,),
            ).fetchone()
            self._connection.commit()
            yield None if row is None else ControlStateV1.model_validate(row[0])
        finally:
            self._connection.execute(
                "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
                (account_id,),
            )
            self._connection.commit()

    def claim_next_outbox(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int = 30,
    ) -> PostgresClaim | None:
        now = now.astimezone(UTC)
        lease_until = now + timedelta(seconds=lease_seconds)
        with self._connection.transaction():
            row = self._connection.execute(
                """
                SELECT message_id, payload, last_error, submission_state
                FROM outbox_v1
                WHERE processed_at IS NULL
                  AND (lease_until IS NULL OR lease_until <= %s)
                  AND next_attempt_at <= %s
                ORDER BY priority, next_attempt_at, created_at, message_id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """,
                (now, now),
            ).fetchone()
            if row is None:
                return None
            message_id, payload, last_error, submission_state = row
            self._connection.execute(
                """
                UPDATE outbox_v1
                SET lease_owner = %s, lease_until = %s, attempts = attempts + 1
                WHERE message_id = %s
                """,
                (worker_id, lease_until, message_id),
            )
        return PostgresClaim(
            message_id=message_id,
            bundle=ExecutionBundleV1.model_validate(payload),
            worker_id=worker_id,
            lease_until=lease_until,
            last_error=last_error,
            submission_state=submission_state,
        )

    def acquire_account_lease(
        self,
        *,
        account_id: str,
        worker_id: str,
        now: datetime,
        lease_seconds: int = 30,
    ) -> bool:
        now = now.astimezone(UTC)
        row = self._connection.execute(
            """
            INSERT INTO execution_leases_v1(account_id, worker_id, lease_until)
            VALUES (%s, %s, %s)
            ON CONFLICT (account_id) DO UPDATE SET
                worker_id = EXCLUDED.worker_id,
                lease_until = EXCLUDED.lease_until,
                version = execution_leases_v1.version + 1
            WHERE execution_leases_v1.lease_until <= %s
               OR execution_leases_v1.worker_id = %s
            RETURNING account_id
            """,
            (account_id, worker_id, now + timedelta(seconds=lease_seconds), now, worker_id),
        ).fetchone()
        self._connection.commit()
        return row is not None

    def release_account_lease(self, *, account_id: str, worker_id: str) -> None:
        self._connection.execute(
            "DELETE FROM execution_leases_v1 WHERE account_id = %s AND worker_id = %s",
            (account_id, worker_id),
        )
        self._connection.commit()

    def complete_outbox(
        self,
        *,
        message_id: str,
        worker_id: str,
        broker_event: BrokerEventV1,
        bundle: ExecutionBundleV1,
    ) -> None:
        with self._connection.transaction():
            self._connection.execute(
                """
                INSERT INTO broker_events_v1(
                    content_hash, client_order_id, broker_order_id, status, occurred_at, payload
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (content_hash) DO NOTHING
                """,
                (
                    broker_event.content_hash,
                    broker_event.client_order_id,
                    broker_event.broker_order_id,
                    broker_event.status,
                    broker_event.occurred_at,
                    self._json(broker_event),
                ),
            )
            self._apply_terminal_projection(bundle, broker_event)
            self._connection.execute(
                "INSERT INTO inbox_v1(message_id) VALUES (%s) ON CONFLICT DO NOTHING",
                (message_id,),
            )
            updated = self._connection.execute(
                """
                UPDATE outbox_v1
                SET processed_at = now(), lease_owner = NULL, lease_until = NULL, last_error = NULL
                WHERE message_id = %s AND lease_owner = %s
                RETURNING message_id
                """,
                (message_id, worker_id),
            ).fetchone()
            if updated is None:
                raise LedgerError("POSTGRES_OUTBOX_CLAIM_MISMATCH")

    def _apply_terminal_projection(
        self,
        bundle: ExecutionBundleV1,
        broker_event: BrokerEventV1,
    ) -> None:
        if broker_event.status not in {"FILLED", "REJECTED", "CANCELLED", "EXPIRED"}:
            raise LedgerError("POSTGRES_OUTBOX_COMPLETION_REQUIRES_TERMINAL_EVENT")
        command = bundle.command
        if isinstance(command, ExecuteApprovedPlanV1):
            self._connection.execute(
                "DELETE FROM risk_reservations_v1 WHERE plan_hash = %s",
                (command.plan.plan_hash,),
            )
            if broker_event.filled_quantity > command.plan.quantity:
                raise LedgerError("POSTGRES_ENTRY_FILL_QUANTITY_EXCEEDS_PLAN")
            if broker_event.status == "FILLED" and broker_event.filled_quantity != command.plan.quantity:
                raise LedgerError("POSTGRES_ENTRY_FILL_QUANTITY_MISMATCH")
            if broker_event.filled_quantity == 0:
                return
            position_id = f"position-{command.plan.plan_hash.removeprefix('sha256:')[:24]}"
            direction = (
                "BULLISH"
                if command.plan.template_id in {"CALL_DEBIT_SPREAD_V1", "LONG_CALL_V1"}
                else "BEARISH"
            )
            managed = ManagedPositionV1(
                strategy_position_id=position_id,
                account_id=command.plan.account_id,
                underlying=command.plan.underlying,
                direction=direction,
                opened_at=broker_event.occurred_at,
                current_quantity=broker_event.filled_quantity,
                position_policy_id=command.plan.position_policy_id,
                entry_plan=command.plan,
            )
            self._connection.execute(
                """
                INSERT INTO managed_positions_v1(
                    strategy_position_id, account_id, entry_client_order_id,
                    status, content_hash, payload
                ) VALUES (%s, %s, %s, 'OPEN', %s, %s::jsonb)
                """,
                (
                    managed.strategy_position_id,
                    managed.account_id,
                    command.plan.client_order_id,
                    managed.content_hash,
                    self._json(managed),
                ),
            )
            return

        managed = bundle.managed_position
        if managed is None:
            raise LedgerError("POSTGRES_MANAGED_POSITION_REQUIRED")
        if broker_event.filled_quantity > managed.current_quantity:
            raise LedgerError("POSTGRES_EXIT_FILL_QUANTITY_EXCEEDS_POSITION")
        remaining = managed.current_quantity - broker_event.filled_quantity
        if broker_event.status == "FILLED" and remaining != 0:
            raise LedgerError("POSTGRES_EXIT_FILL_QUANTITY_MISMATCH")
        if remaining == 0:
            self._connection.execute(
                "DELETE FROM managed_positions_v1 WHERE strategy_position_id = %s",
                (managed.strategy_position_id,),
            )
            return
        reopened = ManagedPositionV1.model_validate(
            managed.model_dump(mode="json", exclude={"content_hash"})
            | {"status": "OPEN", "current_quantity": remaining}
        )
        self._connection.execute(
            """
            UPDATE managed_positions_v1
            SET status = 'OPEN', content_hash = %s, payload = %s::jsonb, updated_at = now()
            WHERE strategy_position_id = %s
            """,
            (reopened.content_hash, self._json(reopened), reopened.strategy_position_id),
        )

    def active_managed_positions(self, account_id: str) -> tuple[ManagedPositionV1, ...]:
        rows = self._connection.execute(
            """
            SELECT payload FROM managed_positions_v1
            WHERE account_id = %s ORDER BY strategy_position_id
            """,
            (account_id,),
        ).fetchall()
        return tuple(ManagedPositionV1.model_validate(row[0]) for row in rows)

    def refresh_reconciliation(
        self,
        account: AccountSnapshotV1,
        positions: PositionSnapshotV1,
        order_risk: OrderRiskSnapshotV1,
        *,
        now: datetime,
    ) -> ControlStateV1:
        """CAS a credential-zone broker snapshot while preserving control authority."""
        now = now.astimezone(UTC)
        account_id = account.account_id
        if positions.account_id != account_id or order_risk.account_id != account_id:
            raise LedgerError("POSTGRES_RECONCILIATION_ACCOUNT_MISMATCH")
        if any(snapshot.as_of > now for snapshot in (account, positions, order_risk)):
            raise LedgerError("POSTGRES_RECONCILIATION_FROM_FUTURE")
        with self._connection.transaction():
            self._connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (account_id,),
            )
            control_row = self._connection.execute(
                "SELECT payload FROM control_state_v1 WHERE account_id = %s FOR UPDATE",
                (account_id,),
            ).fetchone()
            risk_row = self._connection.execute(
                "SELECT version FROM order_risk_state_v1 WHERE account_id = %s FOR UPDATE",
                (account_id,),
            ).fetchone()
            if control_row is None or risk_row is None:
                raise LedgerError("POSTGRES_RECONCILIATION_STATE_UNAVAILABLE")
            current_control = ControlStateV1.model_validate(control_row[0])
            if order_risk.version != risk_row[0] + 1:
                raise LedgerError("POSTGRES_RECONCILIATION_RISK_VERSION_NOT_NEXT")
            next_control = ControlStateV1(
                account_id=account_id,
                version=current_control.version + 1,
                mode=current_control.mode,
                release_hash=current_control.release_hash,
                config_hash=current_control.config_hash,
                account_allowlist_hash=current_control.account_allowlist_hash,
                reconciliation_hash=reconciliation_hash(account, positions, order_risk),
                reconciled_at=now,
            )
            self._connection.execute(
                """
                UPDATE order_risk_state_v1
                SET version = %s, content_hash = %s, payload = %s::jsonb, updated_at = now()
                WHERE account_id = %s AND version = %s
                """,
                (
                    order_risk.version,
                    order_risk.content_hash,
                    self._json(order_risk),
                    account_id,
                    risk_row[0],
                ),
            )
            updated = self._connection.execute(
                """
                UPDATE control_state_v1
                SET version = %s, reconciliation_hash = %s, reconciled_at = %s,
                    content_hash = %s, payload = %s::jsonb, updated_at = now()
                WHERE account_id = %s AND version = %s AND content_hash = %s
                RETURNING version
                """,
                (
                    next_control.version,
                    next_control.reconciliation_hash,
                    next_control.reconciled_at,
                    next_control.content_hash,
                    self._json(next_control),
                    account_id,
                    current_control.version,
                    current_control.content_hash,
                ),
            ).fetchone()
            if updated is None:
                raise LedgerError("POSTGRES_RECONCILIATION_CONTROL_CAS_CONFLICT")
        return next_control

    def release_outbox(
        self,
        *,
        message_id: str,
        worker_id: str,
        error: str | None = None,
        now: datetime | None = None,
        retry_after_seconds: int = 0,
    ) -> None:
        retry_at = (now or datetime.now(UTC)).astimezone(UTC) + timedelta(
            seconds=retry_after_seconds
        )
        self._connection.execute(
            """
            UPDATE outbox_v1
            SET lease_owner = NULL, lease_until = NULL, last_error = %s, next_attempt_at = %s
            WHERE message_id = %s AND lease_owner = %s
            """,
            (error, retry_at, message_id, worker_id),
        )
        self._connection.commit()

    def record_broker_event(self, broker_event: BrokerEventV1) -> None:
        self._connection.execute(
            """
            INSERT INTO broker_events_v1(
                content_hash, client_order_id, broker_order_id, status, occurred_at, payload
            ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (content_hash) DO NOTHING
            """,
            (
                broker_event.content_hash,
                broker_event.client_order_id,
                broker_event.broker_order_id,
                broker_event.status,
                broker_event.occurred_at,
                self._json(broker_event),
            ),
        )
        self._connection.commit()

    def mark_submission_started(
        self,
        *,
        message_id: str,
        worker_id: str,
        now: datetime,
    ) -> None:
        """Durably fence retries before the first external broker mutation."""
        updated = self._connection.execute(
            """
            UPDATE outbox_v1
            SET submission_state = 'RECONCILE_ONLY', lease_until = %s
            WHERE message_id = %s AND lease_owner = %s AND submission_state = 'READY'
            RETURNING message_id
            """,
            (now.astimezone(UTC) + timedelta(seconds=120), message_id, worker_id),
        ).fetchone()
        self._connection.commit()
        if updated is None:
            raise LedgerError("POSTGRES_SUBMISSION_FENCE_FAILED")
