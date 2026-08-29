"""Append-only event store, transactional outbox/inbox semantics, and projections."""

from .store import LedgerError, MemoryLedger

__all__ = ["LedgerError", "MemoryLedger"]
