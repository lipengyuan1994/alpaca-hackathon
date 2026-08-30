"""PostgreSQL persistence for daily contexts and per-signal decision audits."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from packages.contracts.canonical import canonical_json
from packages.contracts.models import DailyEconomicContextV1, SignalDecisionAuditV1

from .collector import EconomicContextError


class EconomicContextStoreError(ValueError):
    """Stable database-boundary errors for context/audit persistence."""


def upsert_signal_audit_row(connection: Any, record: SignalDecisionAuditV1) -> None:
    """Upsert the full immutable audit payload and query-friendly projections.

    The caller owns the surrounding transaction.  Execution uses this helper to
    update whether an enqueued order actually reached an accepted broker state.
    """

    connection.execute(
        """
        INSERT INTO signal_decision_audit_v1(
            record_id, run_id, trading_date, recorded_at,
            strategy_evaluation_hash, agent_thesis_hash, trade_intent_hash,
            economic_context_hash, economic_assessment_hash,
            decision_status, placement_state, order_placed, reason_code,
            plan_hash, client_order_id, signal_payload, supplemental, payload, content_hash
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s
        )
        ON CONFLICT (record_id) DO UPDATE SET
            recorded_at = EXCLUDED.recorded_at,
            agent_thesis_hash = EXCLUDED.agent_thesis_hash,
            trade_intent_hash = EXCLUDED.trade_intent_hash,
            economic_context_hash = EXCLUDED.economic_context_hash,
            economic_assessment_hash = EXCLUDED.economic_assessment_hash,
            decision_status = EXCLUDED.decision_status,
            placement_state = EXCLUDED.placement_state,
            order_placed = EXCLUDED.order_placed,
            reason_code = EXCLUDED.reason_code,
            plan_hash = EXCLUDED.plan_hash,
            client_order_id = EXCLUDED.client_order_id,
            signal_payload = EXCLUDED.signal_payload,
            supplemental = EXCLUDED.supplemental,
            payload = EXCLUDED.payload,
            content_hash = EXCLUDED.content_hash,
            updated_at = now()
        """,
        (
            record.record_id,
            record.run_id,
            record.trading_date,
            record.recorded_at,
            record.strategy_evaluation_hash,
            record.agent_thesis_hash,
            record.trade_intent_hash,
            record.economic_context_hash,
            record.economic_assessment_hash,
            record.decision_status.value,
            record.placement_state.value,
            record.order_placed,
            record.reason_code,
            record.plan_hash,
            record.client_order_id,
            canonical_json(record.signal_payload),
            canonical_json(record.supplemental),
            canonical_json(record),
            record.content_hash,
        ),
    )


class PostgresEconomicContextStore:
    """Small DB-API adapter; it has no Alpaca or LLM dependency."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    @classmethod
    def from_dsn(cls, dsn: str) -> "PostgresEconomicContextStore":
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - deployment dependency check
            raise EconomicContextStoreError("POSTGRES_DRIVER_NOT_INSTALLED") from exc
        return cls(psycopg.connect(dsn))

    @staticmethod
    def _json(value: Any) -> str:
        return canonical_json(value)

    def load_daily_context(self, trading_date: date) -> DailyEconomicContextV1 | None:
        row = self._connection.execute(
            """
            SELECT status, payload, failure_reason
            FROM daily_economic_context_v1
            WHERE trading_date = %s
            """,
            (trading_date,),
        ).fetchone()
        if row is None:
            return None
        status, payload, failure_reason = row
        if status == "READY":
            try:
                return DailyEconomicContextV1.model_validate(payload)
            except Exception as exc:
                raise EconomicContextStoreError("POSTGRES_ECONOMIC_CONTEXT_PAYLOAD_INVALID") from exc
        if status == "FAILED":
            raise EconomicContextError(failure_reason or "ECONOMIC_CONTEXT_PREVIOUSLY_FAILED")
        return None

    def claim_daily_collection(
        self,
        *,
        trading_date: date,
        config_hash: str,
        now: datetime,
    ) -> str:
        now = now.astimezone(UTC)
        with self._connection.transaction():
            inserted = self._connection.execute(
                """
                INSERT INTO daily_economic_context_v1(
                    trading_date, status, collection_config_hash, collection_started_at
                ) VALUES (%s, 'COLLECTING', %s, %s)
                ON CONFLICT (trading_date) DO NOTHING
                RETURNING trading_date
                """,
                (trading_date, config_hash, now),
            ).fetchone()
            if inserted is not None:
                return "CLAIMED"
            existing = self._connection.execute(
                "SELECT status FROM daily_economic_context_v1 WHERE trading_date = %s",
                (trading_date,),
            ).fetchone()
        if existing is None:  # pragma: no cover - transaction invariant
            raise EconomicContextStoreError("POSTGRES_ECONOMIC_CONTEXT_CLAIM_LOST")
        return {"READY": "READY", "FAILED": "FAILED", "COLLECTING": "IN_PROGRESS"}.get(
            existing[0], "UNAVAILABLE"
        )

    def complete_daily_collection(self, context: DailyEconomicContextV1) -> None:
        updated = self._connection.execute(
            """
            UPDATE daily_economic_context_v1
            SET status = 'READY', context_hash = %s, collected_at = %s,
                expires_at = %s, payload = %s::jsonb, failure_reason = NULL,
                updated_at = now()
            WHERE trading_date = %s AND status = 'COLLECTING'
            RETURNING trading_date
            """,
            (
                context.content_hash,
                context.collected_at,
                context.expires_at,
                self._json(context),
                context.trading_date,
            ),
        ).fetchone()
        self._connection.commit()
        if updated is None:
            raise EconomicContextStoreError("POSTGRES_ECONOMIC_CONTEXT_COMPLETE_CLAIM_MISMATCH")

    def fail_daily_collection(self, *, trading_date: date, reason_code: str, now: datetime) -> None:
        self._connection.execute(
            """
            UPDATE daily_economic_context_v1
            SET status = 'FAILED', failure_reason = %s, failed_at = %s, updated_at = now()
            WHERE trading_date = %s AND status = 'COLLECTING'
            """,
            (reason_code, now.astimezone(UTC), trading_date),
        )
        self._connection.commit()

    def upsert_signal_audit(self, record: SignalDecisionAuditV1) -> None:
        self._upsert_signal_audit(record)
        self._connection.commit()

    def _upsert_signal_audit(self, record: SignalDecisionAuditV1) -> None:
        upsert_signal_audit_row(self._connection, record)

    def record_signal_and_complete_decision_job(
        self,
        *,
        record: SignalDecisionAuditV1,
        job_id: str,
        worker_id: str,
        result_status: str,
    ) -> None:
        """Atomically retain a no-trade/risk-rejection log and release the job lease."""

        with self._connection.transaction():
            self._upsert_signal_audit(record)
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
            if updated is None:
                raise EconomicContextStoreError("POSTGRES_DECISION_JOB_CLAIM_MISMATCH")

    def signal_audit(self, record_id: str) -> SignalDecisionAuditV1 | None:
        row = self._connection.execute(
            "SELECT payload FROM signal_decision_audit_v1 WHERE record_id = %s",
            (record_id,),
        ).fetchone()
        if row is None:
            return None
        payload = row[0]
        if isinstance(payload, dict):
            return SignalDecisionAuditV1.model_validate(payload)
        # The table intentionally stores columns for operational queries. Build
        # the canonical record from the columns only if a legacy deployment has
        # not yet added a full payload column.
        raise EconomicContextStoreError("POSTGRES_SIGNAL_AUDIT_PAYLOAD_UNAVAILABLE")


@dataclass
class InMemoryEconomicContextStore:
    """Test/replay equivalent with the same one-claim-per-date behavior."""

    contexts: dict[date, DailyEconomicContextV1] = field(default_factory=dict)
    states: dict[date, str] = field(default_factory=dict)
    failures: dict[date, str] = field(default_factory=dict)
    audits: dict[str, SignalDecisionAuditV1] = field(default_factory=dict)

    def load_daily_context(self, trading_date: date) -> DailyEconomicContextV1 | None:
        state = self.states.get(trading_date)
        if state == "FAILED":
            raise EconomicContextError(self.failures[trading_date])
        return self.contexts.get(trading_date)

    def claim_daily_collection(
        self,
        *,
        trading_date: date,
        config_hash: str,
        now: datetime,
    ) -> str:
        del config_hash, now
        if trading_date not in self.states:
            self.states[trading_date] = "COLLECTING"
            return "CLAIMED"
        return {"READY": "READY", "FAILED": "FAILED", "COLLECTING": "IN_PROGRESS"}[self.states[trading_date]]

    def complete_daily_collection(self, context: DailyEconomicContextV1) -> None:
        if self.states.get(context.trading_date) != "COLLECTING":
            raise EconomicContextStoreError("MEMORY_ECONOMIC_CONTEXT_COMPLETE_CLAIM_MISMATCH")
        self.contexts[context.trading_date] = context
        self.states[context.trading_date] = "READY"

    def fail_daily_collection(self, *, trading_date: date, reason_code: str, now: datetime) -> None:
        del now
        if self.states.get(trading_date) == "COLLECTING":
            self.states[trading_date] = "FAILED"
            self.failures[trading_date] = reason_code

    def upsert_signal_audit(self, record: SignalDecisionAuditV1) -> None:
        self.audits[record.record_id] = record
