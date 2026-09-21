"""Durable, single-writer SQLite state for the L11 runtime."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

from packages.contracts.canonical import canonical_hash, canonical_json

TERMINAL_ORDER_STATUSES = frozenset({"filled", "canceled", "cancelled", "rejected", "expired", "done_for_day"})


class LiveState:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def close(self) -> None:
        self._conn.close()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
              sequence INTEGER PRIMARY KEY AUTOINCREMENT,
              occurred_at TEXT NOT NULL,
              event_type TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              previous_hash TEXT,
              event_hash TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS decisions (
              decision_id TEXT PRIMARY KEY,
              signal_cutoff TEXT NOT NULL,
              execution_session TEXT NOT NULL,
              review_kind TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              decision_hash TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS orders (
              client_order_id TEXT PRIMARY KEY,
              decision_id TEXT NOT NULL,
              symbol TEXT NOT NULL,
              side TEXT NOT NULL,
              order_type TEXT NOT NULL,
              requested_qty TEXT NOT NULL,
              limit_price TEXT,
              status TEXT NOT NULL,
              broker_order_id TEXT,
              reserved_cash TEXT NOT NULL DEFAULT '0',
              payload_json TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS fills (
              fill_id TEXT PRIMARY KEY,
              client_order_id TEXT NOT NULL REFERENCES orders(client_order_id),
              broker_order_id TEXT,
              symbol TEXT NOT NULL,
              side TEXT NOT NULL,
              quantity TEXT NOT NULL,
              price TEXT NOT NULL,
              occurred_at TEXT NOT NULL,
              payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notifications (
              incident_id TEXT PRIMARY KEY,
              kind TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              status TEXT NOT NULL,
              attempts INTEGER NOT NULL DEFAULT 0,
              updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS orders_status_idx ON orders(status);
            CREATE INDEX IF NOT EXISTS fills_order_idx ON fills(client_order_id);
            """
        )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def set_meta(self, key: str, value: str) -> None:
        with self.transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)", (key, value))

    def bind_config(self, config_hash: str) -> None:
        current = self.get_meta("config_hash")
        if current is not None and current != config_hash:
            raise RuntimeError("ETF_LIVE_CONFIG_HASH_CHANGED_MIGRATION_REQUIRED")
        self.set_meta("config_hash", config_hash)

    def set_activation(self, *, account_id_hash: str, config_hash: str, operator_reason: str, at: datetime) -> str:
        if len(operator_reason.strip()) < 8:
            raise ValueError("ETF_LIVE_ACTIVATION_REASON_INVALID")
        payload = {"account_id_hash": account_id_hash, "config_hash": config_hash, "reason": operator_reason.strip(), "at": at.astimezone(UTC).isoformat()}
        token_hash = canonical_hash(payload)
        with self.transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES ('activation_json',?)", (canonical_json({**payload, "token_hash": token_hash}),))
            conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES ('enabled','1')")
        self.append_event("LIVE_ACTIVATED", payload)
        return token_hash

    def activation(self) -> dict[str, Any] | None:
        text = self.get_meta("activation_json")
        return None if text is None else json.loads(text)

    def policy_state(self) -> dict[str, Any]:
        text = self.get_meta("policy_state_json")
        return {} if text is None else json.loads(text)

    def save_policy_state(self, payload: dict[str, Any]) -> None:
        self.set_meta("policy_state_json", canonical_json(payload))

    def settled_cash(self) -> str | None:
        return self.get_meta("settled_cash")

    def set_settled_cash(self, value: str) -> None:
        self.set_meta("settled_cash", value)

    def append_event(self, event_type: str, payload: dict[str, Any], *, at: datetime | None = None) -> str:
        occurred = (at or datetime.now(UTC)).astimezone(UTC).isoformat()
        previous = self._conn.execute("SELECT event_hash FROM events ORDER BY sequence DESC LIMIT 1").fetchone()
        previous_hash = None if previous is None else str(previous[0])
        body = {"occurred_at": occurred, "event_type": event_type, "payload": payload, "previous_hash": previous_hash}
        event_hash = canonical_hash(body)
        with self.transaction() as conn:
            conn.execute("INSERT INTO events(occurred_at,event_type,payload_json,previous_hash,event_hash) VALUES (?,?,?,?,?)", (occurred, event_type, canonical_json(payload), previous_hash, event_hash))
        return event_hash

    def save_decision(self, decision: dict[str, Any]) -> str:
        decision_hash = canonical_hash(decision)
        decision_id = str(decision["decision_id"])
        with self.transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO decisions(decision_id,signal_cutoff,execution_session,review_kind,payload_json,decision_hash) VALUES (?,?,?,?,?,?)", (decision_id, str(decision["signal_cutoff"]), str(decision["execution_session"]), str(decision.get("review_kind", "daily")), canonical_json(decision), decision_hash))
        return decision_hash

    def save_order(self, order: dict[str, Any]) -> None:
        with self.transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO orders(client_order_id,decision_id,symbol,side,order_type,requested_qty,limit_price,status,broker_order_id,reserved_cash,payload_json,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (order["client_order_id"], order["decision_id"], order["symbol"], order["side"], order["order_type"], str(order["requested_qty"]), None if order.get("limit_price") is None else str(order["limit_price"]), order["status"], order.get("broker_order_id"), str(order.get("reserved_cash", "0")), canonical_json(order), str(order["updated_at"])))

    def update_order(self, client_order_id: str, *, status: str, broker_order_id: str | None = None, payload: dict[str, Any] | None = None, reserved_cash: str | None = None) -> None:
        row = self._conn.execute("SELECT payload_json FROM orders WHERE client_order_id=?", (client_order_id,)).fetchone()
        if row is None:
            raise RuntimeError("ETF_LIVE_ORDER_UNKNOWN")
        body = json.loads(row[0])
        if payload:
            body.update(payload)
        now = datetime.now(UTC).isoformat()
        with self.transaction() as conn:
            conn.execute("UPDATE orders SET status=?,broker_order_id=COALESCE(?,broker_order_id),reserved_cash=COALESCE(?,reserved_cash),payload_json=?,updated_at=? WHERE client_order_id=?", (status, broker_order_id, reserved_cash, canonical_json(body), now, client_order_id))

    def order(self, client_order_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM orders WHERE client_order_id=?", (client_order_id,)).fetchone()
        return None if row is None else dict(row)

    def open_orders(self) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM orders WHERE status NOT IN ('filled','canceled','cancelled','rejected','expired','done_for_day') ORDER BY updated_at").fetchall()
        return [dict(row) for row in rows]

    def all_orders(self) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM orders ORDER BY updated_at").fetchall()
        return [dict(row) for row in rows]

    def save_fill(self, fill: dict[str, Any]) -> None:
        with self.transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO fills(fill_id,client_order_id,broker_order_id,symbol,side,quantity,price,occurred_at,payload_json) VALUES (?,?,?,?,?,?,?,?,?)", (fill["fill_id"], fill["client_order_id"], fill.get("broker_order_id"), fill["symbol"], fill["side"], str(fill["quantity"]), str(fill["price"]), str(fill["occurred_at"]), canonical_json(fill)))

    def filled_quantity(self, client_order_id: str) -> str:
        rows = self._conn.execute("SELECT quantity FROM fills WHERE client_order_id=?", (client_order_id,)).fetchall()
        total = sum((Decimal(str(row[0])) for row in rows), Decimal("0"))
        return format(total, "f")

    def queue_notification(self, incident_id: str, kind: str, payload: dict[str, Any]) -> None:
        with self.transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO notifications(incident_id,kind,payload_json,status,updated_at) VALUES (?,?,?,?,?)", (incident_id, kind, canonical_json(payload), "pending", datetime.now(UTC).isoformat()))

    def pending_notifications(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._conn.execute("SELECT * FROM notifications WHERE status='pending' ORDER BY updated_at").fetchall()]

    def mark_notification(self, incident_id: str, *, status: str) -> None:
        with self.transaction() as conn:
            conn.execute("UPDATE notifications SET status=?,attempts=attempts+1,updated_at=? WHERE incident_id=?", (status, datetime.now(UTC).isoformat(), incident_id))
