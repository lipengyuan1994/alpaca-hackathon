"""Append-only event store, transactional outbox/inbox semantics, and projections."""

from .store import ClaimedOutbox, LedgerError, MemoryLedger, OutboxMessage

try:  # The API/decision images do not need the Postgres runtime adapter at import time.
    from .postgres import PostgresClaim, PostgresDecisionClaim, PostgresRuntimeLedger
except ImportError:  # pragma: no cover
    PostgresClaim = None  # type: ignore[assignment,misc]
    PostgresDecisionClaim = None  # type: ignore[assignment,misc]
    PostgresRuntimeLedger = None  # type: ignore[assignment,misc]

__all__ = [
    "ClaimedOutbox",
    "LedgerError",
    "MemoryLedger",
    "OutboxMessage",
    "PostgresClaim",
    "PostgresDecisionClaim",
    "PostgresRuntimeLedger",
]
