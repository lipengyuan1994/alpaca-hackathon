"""Durable, single-writer SQLite state for the isolated ETF runtime."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, date, datetime
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
              notional TEXT NOT NULL DEFAULT '0',
              occurred_at TEXT NOT NULL,
              payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cash_events (
              event_id TEXT PRIMARY KEY,
              category TEXT NOT NULL,
              activity_type TEXT NOT NULL,
              amount TEXT NOT NULL,
              effective_date TEXT NOT NULL,
              spendable_date TEXT NOT NULL,
              distribution_id TEXT,
              payload_hash TEXT NOT NULL,
              payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS distribution_entitlements (
              distribution_id TEXT PRIMARY KEY,
              symbol TEXT NOT NULL,
              ex_date TEXT NOT NULL,
              payable_date TEXT NOT NULL,
              amount TEXT NOT NULL,
              payload_hash TEXT NOT NULL,
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
        fill_columns = {str(row[1]) for row in self._conn.execute("PRAGMA table_info(fills)").fetchall()}
        if "notional" not in fill_columns:
            self._conn.execute("ALTER TABLE fills ADD COLUMN notional TEXT NOT NULL DEFAULT '0'")

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

    def record_cash_event(
        self,
        *,
        event_id: str,
        category: str,
        activity_type: str,
        amount: str | Decimal,
        effective_date: str,
        spendable_date: str,
        payload: dict[str, Any],
        distribution_id: str | None = None,
    ) -> bool:
        """Append one idempotent broker or execution cash movement.

        The external activity id is the deduplication key. Re-delivery with
        identical content is harmless; re-use of an id for altered content is
        an accounting incident and fails closed.
        """

        event_key = str(event_id).strip()
        kind = str(category).strip().lower()
        if not event_key or kind not in {
            "cleared_funding", "buy_fill", "sale_proceeds", "distribution_payment", "fee", "withdrawal", "other"
        }:
            raise ValueError("ETF_LIVE_CASH_EVENT_ID_OR_CATEGORY_INVALID")
        value = Decimal(str(amount))
        if not value.is_finite():
            raise ValueError("ETF_LIVE_CASH_EVENT_AMOUNT_INVALID")
        effective = str(effective_date)[:10]
        spendable = str(spendable_date)[:10]
        try:
            date.fromisoformat(effective)
            date.fromisoformat(spendable)
        except ValueError as exc:
            raise ValueError("ETF_LIVE_CASH_EVENT_DATE_INVALID") from exc
        if spendable < effective:
            raise ValueError("ETF_LIVE_CASH_EVENT_SPENDABLE_BEFORE_EFFECTIVE")
        body = {
            "event_id": event_key,
            "category": kind,
            "activity_type": str(activity_type),
            "amount": format(value, "f"),
            "effective_date": effective,
            "spendable_date": spendable,
            "distribution_id": distribution_id,
            "payload": payload,
        }
        body_hash = canonical_hash(body)
        with self.transaction() as conn:
            existing = conn.execute("SELECT payload_hash FROM cash_events WHERE event_id=?", (event_key,)).fetchone()
            if existing is not None:
                if str(existing[0]) != body_hash:
                    raise RuntimeError("ETF_LIVE_CASH_EVENT_ID_CONFLICT")
                return False
            conn.execute(
                "INSERT INTO cash_events(event_id,category,activity_type,amount,effective_date,spendable_date,distribution_id,payload_hash,payload_json) VALUES (?,?,?,?,?,?,?,?,?)",
                (event_key, kind, str(activity_type), format(value, "f"), effective, spendable, distribution_id, body_hash, canonical_json(body)),
            )
        return True

    def record_distribution_entitlement(
        self,
        *,
        distribution_id: str,
        symbol: str,
        ex_date: str,
        payable_date: str,
        amount: str | Decimal,
        payload: dict[str, Any],
    ) -> bool:
        """Record a verified distribution entitlement without spending it."""

        event_key = str(distribution_id).strip()
        value = Decimal(str(amount))
        effective = str(ex_date)[:10]
        payable = str(payable_date)[:10]
        try:
            date.fromisoformat(effective)
            date.fromisoformat(payable)
        except ValueError as exc:
            raise ValueError("ETF_LIVE_DISTRIBUTION_DATE_INVALID") from exc
        if not event_key or value < 0 or not value.is_finite() or payable < effective:
            raise ValueError("ETF_LIVE_DISTRIBUTION_ENTITLEMENT_INVALID")
        body = {
            "distribution_id": event_key,
            "symbol": str(symbol).upper(),
            "ex_date": effective,
            "payable_date": payable,
            "amount": format(value, "f"),
            "payload": payload,
        }
        body_hash = canonical_hash(body)
        with self.transaction() as conn:
            existing = conn.execute("SELECT payload_hash FROM distribution_entitlements WHERE distribution_id=?", (event_key,)).fetchone()
            if existing is not None:
                if str(existing[0]) != body_hash:
                    raise RuntimeError("ETF_LIVE_DISTRIBUTION_ID_CONFLICT")
                return False
            conn.execute(
                "INSERT INTO distribution_entitlements(distribution_id,symbol,ex_date,payable_date,amount,payload_hash,payload_json) VALUES (?,?,?,?,?,?,?)",
                (event_key, str(symbol).upper(), effective, payable, format(value, "f"), body_hash, canonical_json(body)),
            )
        return True

    def cash_ledger_snapshot(self, *, as_of: str, reserved_cash: str | Decimal = "0") -> dict[str, Any]:
        """Return reconciled cash buckets; broker buying power is never used."""

        asof = str(as_of)[:10]
        try:
            date.fromisoformat(asof)
        except ValueError as exc:
            raise ValueError("ETF_LIVE_CASH_SNAPSHOT_DATE_INVALID") from exc
        reserve = Decimal(str(reserved_cash))
        if not reserve.is_finite() or reserve < 0:
            raise ValueError("ETF_LIVE_CASH_RESERVATION_INVALID")
        events = [dict(row) for row in self._conn.execute("SELECT * FROM cash_events WHERE effective_date<=? ORDER BY effective_date,event_id", (asof,)).fetchall()]
        settled = sum((Decimal(row["amount"]) for row in events if row["spendable_date"] <= asof), Decimal("0"))
        unsettled = sum((Decimal(row["amount"]) for row in events if row["category"] == "sale_proceeds" and row["spendable_date"] > asof), Decimal("0"))
        funding = sum((Decimal(row["amount"]) for row in events if row["category"] == "cleared_funding"), Decimal("0"))
        paid_by_distribution: dict[str, Decimal] = {}
        for row in events:
            if row["category"] == "distribution_payment" and row["distribution_id"]:
                paid_by_distribution[str(row["distribution_id"])] = paid_by_distribution.get(str(row["distribution_id"]), Decimal("0")) + Decimal(row["amount"])
        entitlements = [dict(row) for row in self._conn.execute("SELECT * FROM distribution_entitlements WHERE ex_date<=? ORDER BY ex_date,distribution_id", (asof,)).fetchall()]
        receivable = Decimal("0")
        unresolved: list[str] = []
        for item in entitlements:
            entitlement = Decimal(item["amount"])
            paid = paid_by_distribution.get(str(item["distribution_id"]), Decimal("0"))
            if paid > entitlement:
                unresolved.append(str(item["distribution_id"]))
            receivable += max(Decimal("0"), entitlement - paid)
            if item["payable_date"] <= asof and paid < entitlement:
                unresolved.append(str(item["distribution_id"]))
        fees = -sum((Decimal(row["amount"]) for row in events if row["category"] == "fee"), Decimal("0"))
        other = sum((Decimal(row["amount"]) for row in events if row["category"] in {"withdrawal", "other"}), Decimal("0"))
        unresolved_activity_ids = [
            str(row["event_id"])
            for row in events
            if row["category"] == "other" and row["effective_date"] <= asof
        ]
        available = settled - reserve
        blockers: list[str] = []
        if self.get_meta("cash_ledger_initialized") != "1":
            blockers.append("CASH_LEDGER_UNINITIALIZED")
        if available < 0:
            blockers.append("CASH_LEDGER_RESERVED_OVER_SETTLED")
        if unresolved:
            blockers.append("DISTRIBUTION_RECONCILIATION_REQUIRED")
        if unresolved_activity_ids:
            blockers.append("UNRESOLVED_ACCOUNT_ACTIVITY")
        distribution_inputs = self.get_meta("distribution_input_blockers")
        try:
            distribution_input_ids = [] if distribution_inputs is None else json.loads(distribution_inputs)
        except (TypeError, ValueError, json.JSONDecodeError):
            distribution_input_ids = ["invalid-distribution-input-blocker-state"]
        if distribution_input_ids:
            blockers.append("DISTRIBUTION_INPUTS_UNRESOLVED")
        fill_blockers_text = self.get_meta("unmatched_fill_activity_ids")
        try:
            unmatched_fill_ids = [] if fill_blockers_text is None else json.loads(fill_blockers_text)
        except (TypeError, ValueError, json.JSONDecodeError):
            unmatched_fill_ids = ["invalid-fill-blocker-state"]
        if unmatched_fill_ids:
            blockers.append("UNMATCHED_FILL_ACTIVITY")
        return {
            "as_of": asof,
            "cleared_funding": format(funding, "f"),
            "settled_cash": format(settled, "f"),
            "reserved_cash": format(reserve, "f"),
            "spendable_cash": format(max(Decimal("0"), available), "f"),
            "unsettled_sale_proceeds": format(unsettled, "f"),
            "distribution_receivables": format(receivable, "f"),
            "broker_confirmed_distribution_payments": format(sum(paid_by_distribution.values(), Decimal("0")), "f"),
            "fees": format(fees, "f"),
            "other_cash_movements": format(other, "f"),
            "unresolved_account_activity_ids": unresolved_activity_ids,
            "distribution_input_blocker_ids": distribution_input_ids,
            "unmatched_fill_activity_ids": unmatched_fill_ids,
            "cash_equity": format(settled + unsettled + receivable, "f"),
            "blockers": sorted(set(blockers)),
        }

    def initialize_cash_ledger(self, *, evidence_id: str, account_id_hash: str, as_of: str, minimum_initial_cash: str | Decimal = "2000") -> None:
        """Mark the cash ledger usable only after persisted funding evidence."""

        if not evidence_id.strip() or not account_id_hash.strip():
            raise ValueError("ETF_LIVE_CASH_LEDGER_EVIDENCE_REQUIRED")
        snapshot = self.cash_ledger_snapshot(as_of=as_of)
        minimum = Decimal(str(minimum_initial_cash))
        if not minimum.is_finite() or minimum <= 0 or Decimal(snapshot["cleared_funding"]) < minimum:
            raise RuntimeError("ETF_LIVE_CLEARED_FUNDING_BELOW_REQUIREMENT")
        self.set_meta("cash_ledger_evidence", canonical_json({"evidence_id": evidence_id, "account_id_hash": account_id_hash, "as_of": as_of[:10]}))
        self.set_meta("cash_ledger_initialized", "1")
        self.append_event("CASH_LEDGER_INITIALIZED", {"evidence_id": evidence_id, "account_id_hash": account_id_hash, "as_of": as_of[:10]})

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

    def all_fills(self) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM fills ORDER BY occurred_at,fill_id").fetchall()
        return [dict(row) for row in rows]

    def cash_events(self) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM cash_events ORDER BY effective_date,event_id").fetchall()
        return [dict(row) for row in rows]

    def distribution_entitlements(self) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM distribution_entitlements ORDER BY ex_date,distribution_id").fetchall()
        return [dict(row) for row in rows]

    def set_distribution_input_blockers(self, event_ids: list[str] | tuple[str, ...]) -> None:
        normalized = sorted({str(item) for item in event_ids if str(item)})
        self.set_meta("distribution_input_blockers", canonical_json(normalized))

    def unmatched_fill_activity_ids(self) -> list[str]:
        text = self.get_meta("unmatched_fill_activity_ids")
        try:
            return [] if text is None else [str(item) for item in json.loads(text)]
        except (TypeError, ValueError, json.JSONDecodeError):
            return ["invalid-fill-blocker-state"]

    def set_unmatched_fill_activity_ids(self, event_ids: list[str] | tuple[str, ...]) -> None:
        normalized = sorted({str(item) for item in event_ids if str(item)})
        self.set_meta("unmatched_fill_activity_ids", canonical_json(normalized))

    def save_fill(self, fill: dict[str, Any]) -> bool:
        quantity = Decimal(str(fill["quantity"]))
        price = Decimal(str(fill.get("price", "0")))
        notional = Decimal(str(fill.get("notional", quantity * price)))
        with self.transaction() as conn:
            cursor = conn.execute("INSERT OR IGNORE INTO fills(fill_id,client_order_id,broker_order_id,symbol,side,quantity,price,notional,occurred_at,payload_json) VALUES (?,?,?,?,?,?,?,?,?,?)", (fill["fill_id"], fill["client_order_id"], fill.get("broker_order_id"), fill["symbol"], fill["side"], str(quantity), str(price), str(notional), str(fill["occurred_at"]), canonical_json(fill)))
            return cursor.rowcount == 1

    def filled_quantity(self, client_order_id: str) -> str:
        rows = self._conn.execute("SELECT quantity FROM fills WHERE client_order_id=?", (client_order_id,)).fetchall()
        total = sum((Decimal(str(row[0])) for row in rows), Decimal("0"))
        return format(total, "f")

    def filled_consideration(self, client_order_id: str) -> str:
        rows = self._conn.execute("SELECT notional FROM fills WHERE client_order_id=?", (client_order_id,)).fetchall()
        total = sum((Decimal(str(row[0])) for row in rows), Decimal("0"))
        return format(total, "f")

    def fill_records(self, client_order_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM fills WHERE client_order_id=? ORDER BY occurred_at,fill_id", (client_order_id,)).fetchall()
        return [dict(row) for row in rows]

    def queue_notification(self, incident_id: str, kind: str, payload: dict[str, Any]) -> None:
        with self.transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO notifications(incident_id,kind,payload_json,status,updated_at) VALUES (?,?,?,?,?)", (incident_id, kind, canonical_json(payload), "pending", datetime.now(UTC).isoformat()))

    def pending_notifications(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._conn.execute("SELECT * FROM notifications WHERE status='pending' ORDER BY updated_at").fetchall()]

    def mark_notification(self, incident_id: str, *, status: str) -> None:
        with self.transaction() as conn:
            conn.execute("UPDATE notifications SET status=?,attempts=attempts+1,updated_at=? WHERE incident_id=?", (status, datetime.now(UTC).isoformat(), incident_id))
