"""Fail-closed deterministic ETF execution runtime."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from packages.contracts.canonical import canonical_hash

from .broker import AlpacaLiveBroker, BrokerError, BrokerSubmissionUnknown, Quote
from .config import LiveConfig
from .notifications import TelegramNotifier
from .policy import L11Policy, PendingOrder, PolicyContext, PolicyState, T08Policy
from .state import LiveState


class LiveRuntime:
    def __init__(self, *, config: LiveConfig, broker: AlpacaLiveBroker, state: LiveState | None = None, notifier: TelegramNotifier | None = None) -> None:
        self.config = config
        self.broker = broker
        self.state = state or LiveState(config.state_path)
        self.notifier = notifier or TelegramNotifier(state=self.state, secrets_root=config.secrets_root, enabled=config.telegram_enabled)
        self.tz = ZoneInfo(config.timezone)
        if config.strategy_id == "T08":
            self.policy = T08Policy(target_investment=config.target_investment, symbols=config.symbols, signal_symbols=config.signal_symbols)
        else:
            self.policy = L11Policy(target_investment=config.target_investment, symbols=config.symbols, signal_symbols=config.signal_symbols)
        self.strategy_prefix = config.strategy_id.lower()

    def preflight(self, *, now: datetime) -> dict[str, Any]:
        account = self.broker.account()
        if account.account_id != self.config.account_id:
            raise RuntimeError("ETF_LIVE_ACCOUNT_ID_MISMATCH")
        if self.config.mode == "live" and account.status != "ACTIVE":
            raise RuntimeError(f"ETF_LIVE_ACCOUNT_NOT_ACTIVE:{account.status}")
        if account.trading_blocked or account.account_blocked or account.trade_suspended_by_user:
            raise RuntimeError("ETF_LIVE_ACCOUNT_RESTRICTED")
        if account.multiplier != Decimal("1"):
            raise RuntimeError("ETF_LIVE_MARGIN_MULTIPLIER_NOT_ONE")
        if self.config.mode == "live" and self.config.strategy_config_hash.endswith("0" * 64):
            raise RuntimeError("ETF_LIVE_STRATEGY_HASH_PLACEHOLDER")
        self.state.bind_config(self.config.config_hash)
        if self.config.strategy_id == "T08" and self.config.mode == "live":
            self._reconcile_account_cash_activities(now=now)
            self._maybe_initialize_t08_cash_ledger(now=now)
        positions = self.broker.positions()
        orders = self.broker.open_orders()
        unexpected_positions = [str(x.get("symbol")) for x in positions if str(x.get("symbol")) not in self.config.symbols and Decimal(str(x.get("qty", "0"))) != 0]
        invalid_positions = [str(x.get("symbol")) for x in positions if str(x.get("symbol")) in self.config.symbols and Decimal(str(x.get("qty", "0"))) < 0]
        unexpected_orders = [str(x.get("symbol")) for x in orders if str(x.get("symbol")) not in self.config.symbols]
        unmanaged_orders = [str(x.get("client_order_id") or x.get("id") or x.get("symbol")) for x in orders if not str(x.get("client_order_id") or "").startswith(f"{self.strategy_prefix}-")]
        if unexpected_positions or invalid_positions or unexpected_orders or unmanaged_orders:
            self._incident("unmanaged-account", "UNMANAGED_ACCOUNT_ACTIVITY", {"positions": unexpected_positions + invalid_positions, "orders": unexpected_orders + unmanaged_orders})
            raise RuntimeError("ETF_LIVE_UNMANAGED_ACCOUNT_ACTIVITY")
        if self.config.mode == "live" and self.state.activation() is None and any(Decimal(str(x.get("qty", "0"))) != 0 for x in positions):
            self._incident("activation-not-flat", "ACCOUNT_NOT_FLAT_AT_ACTIVATION", {"positions": positions})
            raise RuntimeError("ETF_LIVE_ACCOUNT_NOT_FLAT_AT_ACTIVATION")
        assets = {symbol: self.broker.asset(symbol) for symbol in self.config.symbols}
        unsupported = [symbol for symbol, asset in assets.items() if asset.get("tradable") is not True or asset.get("fractionable") is not True]
        if unsupported:
            raise RuntimeError("ETF_LIVE_ASSET_UNSUPPORTED:" + ",".join(unsupported))
        if self.config.strategy_id == "T08" and self.config.mode == "live":
            if self.state.activation() is None:
                snapshot = self.state.cash_ledger_snapshot(as_of=now.astimezone(self.tz).date().isoformat())
                if snapshot["blockers"]:
                    raise RuntimeError("ETF_LIVE_T08_CASH_LEDGER_BLOCKED:" + ",".join(snapshot["blockers"]))
                settled_limit = Decimal(snapshot["spendable_cash"])
                if account.non_marginable_buying_power is not None:
                    settled_limit = min(settled_limit, account.non_marginable_buying_power)
                if settled_limit < self.config.initial_cash:
                    raise RuntimeError("ETF_LIVE_INITIAL_SETTLED_CASH_INSUFFICIENT")
        else:
            if account.non_marginable_buying_power is None:
                raise RuntimeError("ETF_LIVE_SETTLED_CASH_FIELD_UNAVAILABLE")
            available_cash = account.non_marginable_buying_power
            if available_cash < self.config.initial_cash and self.config.mode == "live":
                raise RuntimeError("ETF_LIVE_INITIAL_SETTLED_CASH_INSUFFICIENT")
        return {"status": "PREFLIGHT_PASS", "account_id": account.account_id, "equity": str(account.equity), "cash": str(account.cash), "non_marginable_buying_power": None if account.non_marginable_buying_power is None else str(account.non_marginable_buying_power), "position_count": len(positions), "open_order_count": len(orders), "assets": {k: {"tradable": v.get("tradable"), "fractionable": v.get("fractionable")} for k, v in assets.items()}, "config_hash": self.config.config_hash, "at": now.astimezone(UTC).isoformat()}

    def arm_live(self, *, now: datetime, operator_reason: str) -> str:
        if self.config.mode != "live":
            raise RuntimeError("ETF_LIVE_CONFIG_NOT_LIVE")
        result = self.preflight(now=now)
        account_hash = canonical_hash({"account_id": result["account_id"]})
        return self.state.set_activation(account_id_hash=account_hash, config_hash=self.config.config_hash, operator_reason=operator_reason, at=now)

    def _incident(self, incident_id: str, kind: str, detail: dict[str, Any]) -> None:
        self.state.append_event(kind, detail)
        self.notifier.queue(incident_id=incident_id, kind=kind, message=f"{self.config.strategy_id} {kind}: {detail}")
        self.notifier.flush()

    def _round_qty(self, value: Decimal) -> Decimal:
        quantum = Decimal(1).scaleb(-self.config.quantity_decimals)
        return value.quantize(quantum, rounding=ROUND_DOWN)

    def _buy_limit(self, quote: Quote) -> Decimal:
        return (quote.ask * (Decimal("1") + self.config.buy_limit_adverse_bps / Decimal("10000"))).quantize(Decimal("0.01"), rounding=ROUND_DOWN)

    def submit_intent(self, *, intent: dict[str, Any], decision_id: str, quote: Quote | None, settled_cash: Decimal, now: datetime) -> dict[str, Any]:
        symbol = str(intent["symbol"])
        side = str(intent["side"]).lower()
        if self.config.mode != "live":
            raise RuntimeError("ETF_LIVE_OBSERVE_ONLY")
        activation = self.state.activation()
        if activation is None or activation.get("config_hash") != self.config.config_hash:
            raise RuntimeError("ETF_LIVE_NOT_ARMED")
        try:
            enabled = self.config.enabled_file.read_text(encoding="utf-8").strip() == "1"
        except OSError as exc:
            raise RuntimeError("ETF_LIVE_ENABLE_FILE_UNAVAILABLE") from exc
        if not enabled:
            raise RuntimeError("ETF_LIVE_ENABLE_FILE_NOT_ONE")
        if side == "buy" and self.state.get_meta("buys_paused") == "1":
            raise RuntimeError("ETF_LIVE_BUYS_PAUSED")
        if symbol not in self.config.symbols or side not in {"buy", "sell"}:
            raise RuntimeError("ETF_LIVE_INTENT_NOT_ALLOWED")
        if now.astimezone(self.tz).time() < time(9, 30):
            raise RuntimeError("ETF_LIVE_REGULAR_SESSION_NOT_OPEN")
        if side == "buy":
            if quote is None:
                raise RuntimeError("ETF_LIVE_QUOTE_MISSING")
            quote_age = (now - quote.timestamp).total_seconds()
            if quote_age < -1 or quote_age > self.config.quote_max_age_seconds:
                raise RuntimeError("ETF_LIVE_QUOTE_STALE")
            if quote.bid <= 0 or quote.ask <= 0 or quote.ask < quote.bid or quote.midpoint <= 0:
                raise RuntimeError("ETF_LIVE_QUOTE_UNSAFE")
            if (quote.ask - quote.bid) / quote.midpoint > self.config.buy_spread_limit:
                raise RuntimeError("ETF_LIVE_QUOTE_UNSAFE")
        requested = self._round_qty(Decimal(str(intent["quantity"])))
        if requested <= 0:
            raise RuntimeError("ETF_LIVE_QUANTITY_INVALID")
        order_id = f"{self.strategy_prefix}-{decision_id}-{symbol.lower()}-{side}"
        if self.state.order(order_id) is not None:
            return {"status": "ALREADY_RECORDED", "client_order_id": order_id}
        order_type = "market" if side == "sell" else "limit"
        limit_price = None if side == "sell" else self._buy_limit(quote)  # type: ignore[arg-type]
        reserve = Decimal("0") if side == "sell" else requested * limit_price
        if side == "buy" and reserve > settled_cash:
            requested = self._round_qty(settled_cash / limit_price)
            reserve = requested * limit_price
        if requested <= 0 or (side == "buy" and reserve < self.config.minimum_order_notional):
            raise RuntimeError("ETF_LIVE_SETTLED_CASH_OR_MINIMUM_ORDER")
        payload = {"symbol": symbol, "qty": format(requested, "f"), "side": side, "type": order_type, "time_in_force": "day", "client_order_id": order_id}
        if limit_price is not None:
            payload["limit_price"] = format(limit_price, "f")
        self.state.save_order({
            "client_order_id": order_id,
            "decision_id": decision_id,
            "symbol": symbol,
            "side": side,
            "order_type": order_type,
            "requested_qty": requested,
            "limit_price": limit_price,
            "status": "submit_pending",
            "reserved_cash": reserve,
            "updated_at": now.astimezone(UTC).isoformat(),
            "target_weight": intent.get("target_weight"),
            "target_quantity": intent.get("target_quantity"),
            "reason": intent.get("reason"),
            "payload": payload,
        })
        try:
            response = self.broker.submit_order(payload)
        except BrokerSubmissionUnknown:
            self.state.update_order(order_id, status="submission_unknown")
            self._incident(order_id, "ORDER_SUBMISSION_UNKNOWN", {"symbol": symbol, "side": side})
            raise
        except BrokerError as exc:
            self.state.update_order(order_id, status="rejected", payload={"error": str(exc)})
            self._incident(order_id, "ORDER_REJECTED", {"symbol": symbol, "error": str(exc)})
            raise
        self.state.update_order(order_id, status=str(response.get("status", "accepted")), broker_order_id=str(response.get("id", "")), payload=response)
        return {"status": "SUBMITTED", "client_order_id": order_id, "broker_order_id": response.get("id"), "requested_qty": str(requested), "limit_price": None if limit_price is None else str(limit_price)}

    def reconcile(self, *, now: datetime) -> dict[str, Any]:
        account = self.broker.account()
        positions = self.broker.positions()
        orders = self.broker.open_orders()
        remote_by_client = {str(item.get("client_order_id")): item for item in orders if item.get("client_order_id")}
        reconciled_fills = 0
        for local in self.state.open_orders():
            client_id = str(local["client_order_id"])
            remote = remote_by_client.get(client_id)
            broker_order_id = str(local.get("broker_order_id") or "")
            if remote is None and broker_order_id:
                remote = self.broker.order_by_id(broker_order_id)
            if remote is None:
                remote = self.broker.order_by_client_id(client_id)
            if remote is None:
                if local["status"] == "submit_pending":
                    self.state.update_order(client_id, status="submission_unknown", payload={"reconcile_at": now.astimezone(UTC).isoformat()})
                    self._incident(client_id, "ORDER_SUBMISSION_UNKNOWN", {"client_order_id": client_id})
                continue

            remote_status = str(remote.get("status", "unknown")).lower()
            filled_qty = Decimal(str(remote.get("filled_qty", "0") or "0"))
            source = str(local.get("payload_json", ""))
            try:
                stored_payload = json.loads(source) if isinstance(source, str) else dict(source)
            except (TypeError, ValueError, json.JSONDecodeError):
                stored_payload = {}
            fill_source = str(stored_payload.get("fill_source", ""))
            activities: list[dict[str, Any]] = []
            if fill_source != "cumulative" and callable(getattr(self.broker, "order_fill_activities", None)) and filled_qty > 0:
                activities = self.broker.order_fill_activities(str(remote.get("id") or broker_order_id))

            if activities or fill_source == "activities":
                for activity in activities:
                    activity_id = activity.get("id", activity.get("activity_id"))
                    quantity = Decimal(str(activity.get("qty", activity.get("quantity", "0")) or "0"))
                    price = Decimal(str(activity.get("price", "0") or "0"))
                    if activity_id is None or quantity <= 0 or price <= 0:
                        raise RuntimeError("ETF_LIVE_FILL_ACTIVITY_INVALID")
                    side = str(activity.get("side", local["side"])).lower()
                    symbol = str(activity.get("symbol", local["symbol"])).upper()
                    activity_order_id = str(activity.get("order_id", remote.get("id") or broker_order_id))
                    if side != str(local["side"]).lower() or symbol != str(local["symbol"]).upper() or activity_order_id != str(remote.get("id") or broker_order_id):
                        raise RuntimeError("ETF_LIVE_FILL_ACTIVITY_ORDER_MISMATCH")
                    occurred_at_text = str(activity.get("transaction_time") or activity.get("occurred_at") or activity.get("date") or now.astimezone(UTC).isoformat())
                    fill = {
                        "fill_id": str(activity_id),
                        "client_order_id": client_id,
                        "broker_order_id": activity_order_id,
                        "symbol": symbol,
                        "side": side,
                        "quantity": format(quantity, "f"),
                        "price": format(price, "f"),
                        "notional": format(quantity * price, "f"),
                        "occurred_at": occurred_at_text,
                        "source": "alpaca_account_activity",
                    }
                    if self.state.save_fill(fill):
                        self.state.append_event("ORDER_FILL_RECONCILED", fill, at=now)
                        reconciled_fills += 1
                recorded = Decimal(self.state.filled_quantity(client_id))
                if recorded != filled_qty:
                    raise RuntimeError(f"ETF_LIVE_FILL_ACTIVITY_COVERAGE_MISMATCH:{format(recorded, 'f')}:{format(filled_qty, 'f')}")
                self.state.update_order(client_id, status=remote_status, broker_order_id=str(remote.get("id") or broker_order_id or ""), payload={"fill_source": "activities"})
            else:
                # The order endpoint exposes only a cumulative average price
                # when individual execution activities are not available.
                # Difference cumulative consideration to obtain the true
                # incremental consideration for this observation.
                already_recorded = Decimal(self.state.filled_quantity(client_id))
                previous_consideration = Decimal(self.state.filled_consideration(client_id))
                total_consideration = filled_qty * Decimal(str(remote.get("filled_avg_price", "0") or "0"))
                delta_quantity = filled_qty - already_recorded
                delta_consideration = total_consideration - previous_consideration
                if delta_quantity != 0 or delta_consideration != 0:
                    if total_consideration < 0:
                        raise RuntimeError("ETF_LIVE_CUMULATIVE_FILL_CORRECTION_INVALID")
                    fill_price = delta_consideration / delta_quantity if delta_quantity != 0 else Decimal("0")
                    occurred_at_text = str(remote.get("filled_at") or remote.get("updated_at") or now.astimezone(UTC).isoformat())
                    fill = {
                        "fill_id": f"{client_id}:cumulative:{format(filled_qty, 'f')}:{format(total_consideration, 'f')}",
                        "client_order_id": client_id,
                        "broker_order_id": remote.get("id") or broker_order_id or None,
                        "symbol": str(local["symbol"]),
                        "side": str(local["side"]),
                        "quantity": format(delta_quantity, "f"),
                        "price": format(fill_price, "f"),
                        "notional": format(delta_consideration, "f"),
                        "occurred_at": occurred_at_text,
                        "source": "cumulative_order_average",
                        "cumulative_quantity": format(filled_qty, "f"),
                        "cumulative_consideration": format(total_consideration, "f"),
                    }
                    if fill_price < 0 or delta_quantity > 0 and fill_price <= 0:
                        raise RuntimeError("ETF_LIVE_CUMULATIVE_FILL_PRICE_INVALID")
                    if self.state.save_fill(fill):
                        self.state.append_event("ORDER_FILL_RECONCILED", fill, at=now)
                        reconciled_fills += 1
                if filled_qty > 0 and Decimal(self.state.filled_quantity(client_id)) != filled_qty:
                    raise RuntimeError("ETF_LIVE_CUMULATIVE_FILL_QUANTITY_MISMATCH")
                self.state.update_order(client_id, status=remote_status, broker_order_id=str(remote.get("id") or broker_order_id or ""), payload={"fill_source": "cumulative"})

        if self.config.strategy_id == "T08" and self.config.mode == "live":
            for fill in self.state.all_fills():
                self._record_fill_cash_movement(fill)
            self._reconcile_account_cash_activities(now=now)
            self._maybe_initialize_t08_cash_ledger(now=now)

        # Do not overwrite the local settled-cash ledger with buying power.
        # That broker field can include amounts that are not settled cash.
        return {"status": "RECONCILE_PASS", "account_id": account.account_id, "cash": str(account.cash), "equity": str(account.equity), "positions": positions, "open_orders": orders, "reconciled_fills": reconciled_fills, "at": now.astimezone(UTC).isoformat()}

    def _local_date(self, value: Any) -> date:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(self.tz).date()

    def _settlement_date(self, trade_date: date) -> date:
        calendar_method = getattr(self.broker, "settlement_calendar", None)
        if not callable(calendar_method):
            raise RuntimeError("ETF_LIVE_EXPLICIT_SETTLEMENT_CALENDAR_REQUIRED")
        rows = calendar_method(start=trade_date, end=trade_date)
        matches = [row for row in rows if str(row.get("date", ""))[:10] == trade_date.isoformat()]
        if len(matches) != 1 or not matches[0].get("settlement_date"):
            raise RuntimeError(f"ETF_LIVE_SETTLEMENT_DATE_UNAVAILABLE:{trade_date.isoformat()}")
        return date.fromisoformat(str(matches[0]["settlement_date"])[:10])

    def _record_fill_cash_movement(self, fill: dict[str, Any]) -> None:
        fill_id = str(fill.get("fill_id", ""))
        if not fill_id:
            raise RuntimeError("ETF_LIVE_FILL_CASH_EVENT_ID_REQUIRED")
        trade_date = self._local_date(fill["occurred_at"])
        side = str(fill["side"]).lower()
        notional = Decimal(str(fill.get("notional") or (Decimal(str(fill["quantity"])) * Decimal(str(fill["price"])))))
        if notional < 0 or side not in {"buy", "sell"}:
            raise RuntimeError("ETF_LIVE_FILL_CASH_MOVEMENT_INVALID")
        category = "buy_fill" if side == "buy" else "sale_proceeds"
        amount = -notional if side == "buy" else notional
        spendable = trade_date if side == "buy" else self._settlement_date(trade_date)
        self.state.record_cash_event(
            event_id=fill_id,
            category=category,
            activity_type="FILL",
            amount=amount,
            effective_date=trade_date.isoformat(),
            spendable_date=spendable.isoformat(),
            payload={"fill_id": fill_id, "order_id": fill.get("broker_order_id"), "symbol": fill.get("symbol"), "side": side, "quantity": str(fill["quantity"]), "price": str(fill["price"]), "notional": format(notional, "f")},
        )

    def _cash_activity_distribution_id(self, activity: dict[str, Any], *, amount: Decimal, activity_date: date) -> str | None:
        symbol = str(activity.get("symbol", "")).upper()
        if not symbol:
            return None
        candidates: list[str] = []
        for entitlement in self.state.distribution_entitlements():
            if str(entitlement["symbol"]).upper() != symbol:
                continue
            payable = date.fromisoformat(str(entitlement["payable_date"])[:10])
            if not payable <= activity_date <= payable + timedelta(days=14):
                continue
            distribution_id = str(entitlement["distribution_id"])
            paid = sum((Decimal(str(row["amount"])) for row in self.state.cash_events() if row.get("category") == "distribution_payment" and row.get("distribution_id") == distribution_id), Decimal("0"))
            remaining = Decimal(str(entitlement["amount"])) - paid
            if (amount >= 0 and remaining >= amount - Decimal("0.01")) or (amount < 0 and paid > 0):
                candidates.append(distribution_id)
        return candidates[0] if len(candidates) == 1 else None

    def _reconcile_account_cash_activities(self, *, now: datetime) -> None:
        activity_method = getattr(self.broker, "activities", None)
        if not callable(activity_method):
            raise RuntimeError("ETF_LIVE_ACCOUNT_ACTIVITY_FEED_REQUIRED")
        cursor = self.state.get_meta("cash_activity_scan_after")
        after = None
        if cursor:
            parsed = datetime.fromisoformat(cursor.replace("Z", "+00:00"))
            after = parsed - timedelta(days=14)
        activities = activity_method(after=after)
        latest_seen = after or datetime(1970, 1, 1, tzinfo=UTC)
        unresolved_fill_ids = set(self.state.unmatched_fill_activity_ids())
        known_fills = {str(item["fill_id"]): item for item in self.state.all_fills()}
        local_orders_by_broker_id = {str(item.get("broker_order_id")): item for item in self.state.all_orders() if item.get("broker_order_id")}
        for activity in activities:
            event_id = str(activity.get("id", activity.get("activity_id", ""))).strip()
            kind = str(activity.get("activity_type", activity.get("type", ""))).upper()
            if not event_id or not kind:
                raise RuntimeError("ETF_LIVE_ACCOUNT_ACTIVITY_ID_OR_TYPE_MISSING")
            if kind == "FILL":
                if event_id in known_fills:
                    unresolved_fill_ids.discard(event_id)
                    continue
                order_id = str(activity.get("order_id", ""))
                local_order = local_orders_by_broker_id.get(order_id)
                if local_order is not None and str(json.loads(str(local_order["payload_json"])).get("fill_source", "")) == "cumulative":
                    unresolved_fill_ids.discard(event_id)
                    continue
                if local_order is not None:
                    # The individual order fill activity and consolidated
                    # activity feed may arrive in either order. Its durable
                    # trade event is already represented by the reconciled
                    # local fill ledger, so resolve by broker order identity.
                    known_order_fills = [row for row in known_fills.values() if row.get("broker_order_id") == order_id]
                    if known_order_fills:
                        unresolved_fill_ids.discard(event_id)
                        continue
                unresolved_fill_ids.add(event_id)
                continue

            raw_date = activity.get("date", activity.get("transaction_time", activity.get("created_at")))
            if raw_date is None:
                raise RuntimeError(f"ETF_LIVE_ACCOUNT_ACTIVITY_DATE_MISSING:{event_id}")
            effective = self._local_date(raw_date)
            stamp_text = activity.get("transaction_time", activity.get("created_at", raw_date))
            stamp = datetime.fromisoformat(str(stamp_text).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=UTC)
            latest_seen = max(latest_seen, stamp.astimezone(UTC))
            try:
                amount = Decimal(str(activity.get("net_amount", "0")))
            except Exception as exc:
                raise RuntimeError(f"ETF_LIVE_ACCOUNT_ACTIVITY_AMOUNT_INVALID:{event_id}") from exc
            if not amount.is_finite():
                raise RuntimeError(f"ETF_LIVE_ACCOUNT_ACTIVITY_AMOUNT_INVALID:{event_id}")

            if kind == "CSD" and amount > 0:
                category = "cleared_funding"
            elif kind == "CSW":
                category = "withdrawal"
            elif kind in {"DIV", "DIVCGL", "DIVCGS", "CGD", "DIVROC", "DIVTXEX"}:
                category = "distribution_payment"
            elif kind in {"FEE", "CFEE", "PTC", "DIVFT", "DIVNRA", "DIVTW", "DIVFEE"}:
                category = "fee"
            elif kind in {"SSP"}:
                # Stock splits change ownership units, not cash. The market
                # action is separately audited by the feature/position path.
                continue
            else:
                category = "other"
            distribution_id = None
            if category == "distribution_payment":
                distribution_id = self._cash_activity_distribution_id(activity, amount=amount, activity_date=effective)
            self.state.record_cash_event(
                event_id=event_id,
                category=category,
                activity_type=kind,
                amount=amount,
                effective_date=effective.isoformat(),
                spendable_date=effective.isoformat(),
                payload={"broker_activity": activity},
                distribution_id=distribution_id,
            )
        if activities:
            self.state.set_meta("cash_activity_scan_after", latest_seen.isoformat().replace("+00:00", "Z"))
        self.state.set_unmatched_fill_activity_ids(tuple(unresolved_fill_ids))

    def _maybe_initialize_t08_cash_ledger(self, *, now: datetime) -> None:
        if self.state.get_meta("cash_ledger_initialized") == "1":
            return
        as_of = now.astimezone(self.tz).date().isoformat()
        snapshot = self.state.cash_ledger_snapshot(as_of=as_of)
        blockers = set(snapshot["blockers"]) - {"CASH_LEDGER_UNINITIALIZED"}
        if blockers:
            return
        if Decimal(snapshot["cleared_funding"]) < self.config.initial_cash:
            return
        if self.broker.positions() or self.broker.open_orders():
            return
        funding_events = [row for row in self.state.cash_events() if row.get("category") == "cleared_funding" and Decimal(str(row["amount"])) > 0]
        if not funding_events:
            return
        account = self.broker.account()
        evidence = str(funding_events[-1]["event_id"])
        self.state.initialize_cash_ledger(
            evidence_id=evidence,
            account_id_hash=canonical_hash({"account_id": account.account_id}),
            as_of=as_of,
            minimum_initial_cash=self.config.initial_cash,
        )

    def _record_tecl_distribution_entitlements(self, actions: list[dict[str, Any]], *, through_date: date) -> None:
        fills = [row for row in self.state.all_fills() if str(row.get("symbol", "")).upper() == "TECL"]
        action_rows = [row for row in actions if str(row.get("symbol", "")).upper() == "TECL"]
        split_rows: list[tuple[date, Decimal]] = []
        distribution_rows: list[dict[str, Any]] = []
        blockers: list[str] = []
        for action in action_rows:
            kind = str(action.get("type", action.get("action_type", action.get("ca_type", "")))).lower()
            raw_ex = action.get("ex_date", action.get("date", action.get("effective_date")))
            if raw_ex is None:
                blockers.append(str(action.get("id", "action-without-ex-date")))
                continue
            ex_date = date.fromisoformat(str(raw_ex).replace("Z", "")[:10])
            if ex_date > through_date:
                continue
            if "split" in kind or "stock" in kind:
                old = action.get("old_rate", action.get("old_shares"))
                new = action.get("new_rate", action.get("new_shares"))
                try:
                    ratio = Decimal(str(new)) / Decimal(str(old)) if old is not None and new is not None else Decimal(str(action.get("ratio")))
                except Exception:
                    ratio = Decimal("0")
                if not ratio.is_finite() or ratio <= 0:
                    blockers.append(str(action.get("id", f"split-{ex_date}")))
                else:
                    split_rows.append((ex_date, ratio))
            elif any(token in kind for token in ("dividend", "distribution", "income")):
                distribution_rows.append(action)

        for action in distribution_rows:
            raw_ex = action.get("ex_date", action.get("date", action.get("effective_date")))
            ex_date = date.fromisoformat(str(raw_ex).replace("Z", "")[:10])
            shares = Decimal("0")
            for fill in fills:
                fill_date = self._local_date(fill["occurred_at"])
                if fill_date >= ex_date:
                    continue
                quantity = Decimal(str(fill["quantity"]))
                if str(fill["side"]).lower() == "sell":
                    quantity = -quantity
                for split_date, factor in split_rows:
                    if fill_date < split_date <= ex_date:
                        quantity *= factor
                shares += quantity
            if shares <= 0:
                continue
            event_id = str(action.get("id", "")).strip()
            if not event_id:
                event_id = "tecl-action-" + canonical_hash(action)
            raw_payable = action.get("payable_date", action.get("payment_date"))
            amount_value = next((action.get(key) for key in ("cash_amount", "amount", "rate", "value", "per_share_amount") if action.get(key) not in (None, "")), None)
            if raw_payable is None or amount_value is None:
                blockers.append(event_id)
                continue
            try:
                payable = date.fromisoformat(str(raw_payable).replace("Z", "")[:10])
                per_share = Decimal(str(amount_value))
                total = shares * per_share
                if payable < ex_date or not per_share.is_finite() or per_share < 0:
                    raise ValueError
                self.state.record_distribution_entitlement(
                    distribution_id=event_id,
                    symbol="TECL",
                    ex_date=ex_date.isoformat(),
                    payable_date=payable.isoformat(),
                    amount=total,
                    payload={"provider_action": action, "shares_entitled": format(shares, "f"), "per_share_amount": format(per_share, "f"), "amount_units": "provider_per_post_effective_share"},
                )
            except (ValueError, ArithmeticError, RuntimeError):
                blockers.append(event_id)
        self.state.set_distribution_input_blockers(blockers)

    @staticmethod
    def _bar_date(row: dict[str, Any]) -> datetime:
        value = row.get("t", row.get("timestamp", row.get("date")))
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    @classmethod
    def _feature_rows(cls, rows: list[dict[str, Any]], *, ratios: dict[str, Decimal] | None = None) -> list[dict[str, Any]]:
        ordered = sorted(rows, key=cls._bar_date)
        closes = [Decimal(str(row.get("c", row.get("close")))) for row in ordered]
        ratio_values = ratios or {}
        output: list[dict[str, Any]] = []
        for index, row in enumerate(ordered):
            close = closes[index]
            if index < 199:
                sma200 = None
            else:
                sma200 = sum(closes[index - 199:index + 1], Decimal("0")) / Decimal("200")
            r126 = None if index < 126 else close / closes[index - 126] - Decimal("1")
            r63 = None if index < 63 else close / closes[index - 63] - Decimal("1")
            item: dict[str, Any] = {"asof": cls._bar_date(row), "close": close, "sma200": sma200, "r126": r126, "r63": r63}
            key = cls._bar_date(row).date().isoformat()
            if key in ratio_values:
                item["ratio"] = ratio_values[key]
            output.append(item)
        if output:
            valid_ratios = [item.get("ratio") for item in output]
            for index, item in enumerate(output):
                if item.get("ratio") is not None and index >= 19:
                    window = [value for value in valid_ratios[index - 19:index + 1] if value is not None]
                    if len(window) == 20:
                        item["ratio_sma20"] = sum(window, Decimal("0")) / Decimal("20")
        return output

    @classmethod
    def _t08_feature_rows(cls, rows: list[dict[str, Any]], actions: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        """Build causal XLK trend and turbulence features.

        The caller supplies a distribution-consistent signal series.  The
        executable TECL series is deliberately kept separate and never enters
        this transformation.
        """

        ordered = sorted(rows, key=cls._bar_date)
        action_by_date: dict[str, list[dict[str, Any]]] = {}
        for action in actions or []:
            raw_date = action.get("ex_date", action.get("date", action.get("effective_date")))
            if raw_date is None:
                continue
            action_date = str(raw_date).replace("Z", "")[:10]
            action_by_date.setdefault(action_date, []).append(action)
        # Convert the raw signal closes to forward, distribution-consistent
        # feature units.  This is intentionally separate from account cash.
        feature_closes: list[Decimal] = []
        for index, row in enumerate(ordered):
            raw_close = Decimal(str(row.get("c", row.get("close"))))
            if index == 0:
                feature_closes.append(Decimal("100"))
                continue
            previous_raw = Decimal(str(ordered[index - 1].get("c", ordered[index - 1].get("close"))))
            q = Decimal("1")
            distribution = Decimal("0")
            for action in action_by_date.get(cls._bar_date(row).date().isoformat(), []):
                kind = str(action.get("type", action.get("action_type", action.get("ca_type", "")))).lower()
                if "split" in kind or "stock" in kind:
                    old = action.get("old_rate", action.get("old_shares"))
                    new = action.get("new_rate", action.get("new_shares"))
                    if old is not None and new is not None and Decimal(str(old)) > 0:
                        q *= Decimal(str(new)) / Decimal(str(old))
                if any(token in kind for token in ("dividend", "distribution", "income")):
                    amount = action.get("cash_amount", action.get("amount", action.get("rate", action.get("value", "0"))))
                    try:
                        distribution += Decimal(str(amount))
                    except Exception:
                        continue
            previous_feature = feature_closes[-1]
            feature_closes.append(previous_feature * q * (raw_close + distribution) / previous_raw)
        returns: list[Decimal | None] = [None]
        for index in range(1, len(feature_closes)):
            previous = feature_closes[index - 1]
            returns.append(None if previous <= 0 else feature_closes[index] / previous - Decimal("1"))

        def sample_sigma(values: list[Decimal]) -> Decimal | None:
            if len(values) < 2:
                return None
            mean = sum(values, Decimal("0")) / Decimal(len(values))
            variance = sum(((item - mean) ** 2 for item in values), Decimal("0")) / Decimal(len(values) - 1)
            return variance.sqrt()

        output: list[dict[str, Any]] = []
        for index, row in enumerate(ordered):
            close = feature_closes[index]
            sma200 = None if index < 199 else sum(feature_closes[index - 199:index + 1], Decimal("0")) / Decimal("200")
            r20 = [value for value in returns[max(0, index - 19):index + 1] if value is not None]
            r60 = [value for value in returns[max(0, index - 59):index + 1] if value is not None]
            sigma20 = sample_sigma(r20) if len(r20) == 20 else None
            sigma60 = sample_sigma(r60) if len(r60) == 60 else None
            downside = None
            upside = None
            asymmetry = None
            vol_ratio = None
            if len(r20) == 20:
                downside = (sum((min(value, Decimal("0")) ** 2 for value in r20), Decimal("0")) / Decimal("20")).sqrt()
                upside = (sum((max(value, Decimal("0")) ** 2 for value in r20), Decimal("0")) / Decimal("20")).sqrt()
                if downside == 0 and upside == 0:
                    asymmetry = Decimal("1")
                elif upside == 0:
                    asymmetry = Decimal("999999") if downside > 0 else Decimal("1")
                else:
                    asymmetry = downside / upside
            if sigma20 is not None and sigma60 is not None:
                if sigma60 == 0:
                    vol_ratio = Decimal("0") if sigma20 == 0 else None
                else:
                    vol_ratio = sigma20 / sigma60
            output.append({
                "asof": cls._bar_date(row),
                "close": close,
                "sma200": sma200,
                "downside_rms20": downside,
                "upside_rms20": upside,
                "asymmetry": asymmetry,
                "sigma20": sigma20,
                "sigma60": sigma60,
                "vol_ratio": vol_ratio,
            })
        return output

    def _daily_bars(self, symbols: tuple[str, ...], *, start: datetime, end: datetime, adjustment: str) -> dict[str, list[dict[str, Any]]]:
        """Call the explicit-adjustment broker API with fixture compatibility."""

        try:
            return self.broker.daily_bars(symbols, start=start, end=end, adjustment=adjustment)
        except TypeError as exc:
            # Older fixture brokers used by the L11 compatibility tests did
            # not yet expose the adjustment keyword.  The production broker
            # always takes the explicit path above.
            if "adjustment" not in str(exc):
                raise
            return self.broker.daily_bars(symbols, start=start, end=end)

    def _live_features(self, *, now: datetime) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Decimal], datetime]:
        cutoff = now.astimezone(self.tz).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC) - timedelta(microseconds=1)
        if self.config.strategy_id == "T08":
            signal_bars = self._daily_bars(self.config.signal_symbols, start=cutoff - timedelta(days=430), end=now.astimezone(UTC), adjustment="raw")
            trade_bars = self._daily_bars(self.config.symbols, start=cutoff - timedelta(days=430), end=now.astimezone(UTC), adjustment="raw")
            signal_rows = {symbol: [row for row in signal_bars.get(symbol, []) if self._bar_date(row) <= cutoff] for symbol in self.config.signal_symbols}
            trade_rows = {symbol: [row for row in trade_bars.get(symbol, []) if self._bar_date(row) <= cutoff] for symbol in self.config.symbols}
            for symbol in self.config.signal_symbols + self.config.symbols:
                rows = signal_rows.get(symbol, trade_rows.get(symbol, []))
                if len(rows) < 201:
                    raise RuntimeError(f"ETF_LIVE_HISTORY_INSUFFICIENT:{symbol}")
            if hasattr(self.broker, "corporate_actions"):
                actions = self.broker.corporate_actions(self.config.signal_symbols, start=cutoff - timedelta(days=430), end=now.astimezone(UTC))
                trade_actions = self.broker.corporate_actions(self.config.symbols, start=cutoff - timedelta(days=430), end=now.astimezone(UTC))
                if self.config.mode == "live":
                    self._record_tecl_distribution_entitlements(trade_actions, through_date=self._bar_date(trade_rows["TECL"][-1]).date())
            else:
                actions = []
                trade_actions = []
                if self.config.mode == "live":
                    self.state.set_distribution_input_blockers(("TECL_CORPORATE_ACTION_FEED_UNAVAILABLE",))
            features = {"XLK": self._t08_feature_rows(signal_rows["XLK"], actions), "TECL": self._feature_rows(trade_rows["TECL"])}
            latest_signal = self._bar_date(signal_rows["XLK"][-1])
            eligible_trade = [row for row in trade_rows["TECL"] if self._bar_date(row) <= latest_signal]
            if not eligible_trade:
                raise RuntimeError("ETF_LIVE_TRADE_HISTORY_CUTOFF_MISMATCH")
            prior_close = {"TECL": Decimal(str(eligible_trade[-1].get("c", eligible_trade[-1].get("close"))))}
            return features, prior_close, latest_signal

        bars = self._daily_bars(self.config.signal_symbols + self.config.symbols, start=cutoff - timedelta(days=430), end=now.astimezone(UTC), adjustment="all")
        signal_rows: dict[str, list[dict[str, Any]]] = {}
        raw_by_date: dict[str, dict[str, Decimal]] = {}
        for symbol in self.config.signal_symbols + self.config.symbols:
            rows = [row for row in bars.get(symbol, []) if self._bar_date(row) <= cutoff]
            if len(rows) < 201:
                raise RuntimeError(f"ETF_LIVE_HISTORY_INSUFFICIENT:{symbol}")
            signal_rows[symbol] = rows
            for row in rows:
                raw_by_date.setdefault(self._bar_date(row).date().isoformat(), {})[symbol] = Decimal(str(row.get("c", row.get("close"))))
        ratios = {key: values["SOXX"] / values["QQQ"] for key, values in raw_by_date.items() if "SOXX" in values and "QQQ" in values}
        features = {symbol: self._feature_rows(rows, ratios=ratios if symbol == "SOXX" else None) for symbol, rows in signal_rows.items()}
        prior_close: dict[str, Decimal] = {}
        for symbol in self.config.symbols:
            prior_close[symbol] = Decimal(str(signal_rows[symbol][-1].get("c", signal_rows[symbol][-1].get("close"))))
        latest_cutoff = self._bar_date(signal_rows["QQQ"][-1])
        return features, prior_close, latest_cutoff

    def _policy_state(self) -> PolicyState:
        return PolicyState.from_dict(self.state.policy_state())

    def _pending_for_policy(self) -> list[PendingOrder]:
        output: list[PendingOrder] = []
        for row in self.state.open_orders():
            output.append(PendingOrder(symbol=row["symbol"], side=row["side"], quantity=Decimal(row["requested_qty"]), status=row["status"], decision_id=row["decision_id"]))
        return output

    def _sync_t08_allocation_feedback(self) -> None:
        """Commit allocation state only after a terminal, reconciled transition."""

        if self.config.strategy_id != "T08":
            return
        positions = {str(row.get("symbol", "")).upper(): Decimal(str(row.get("qty", "0"))) for row in self.broker.positions()}
        state = PolicyState.from_dict(self.state.policy_state())
        accepted = state.allocation_target_weight
        for order in self.state.all_orders():
            status = str(order.get("status", "")).lower()
            if status not in {"filled", "canceled", "cancelled", "expired", "done_for_day"}:
                continue
            try:
                payload = json.loads(str(order.get("payload_json", "{}")))
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {}
            target = payload.get("target_weight")
            if target is None:
                continue
            target_weight = Decimal(str(target))
            filled = Decimal(self.state.filled_quantity(str(order["client_order_id"])))
            side = str(order.get("side", "")).lower()
            if side == "buy" and filled > 0:
                # A terminal partial buy is an accepted transition. The
                # acquired shares are held without daily top-ups.
                accepted = target_weight
            elif side == "sell":
                held = positions.get(str(order.get("symbol", "")).upper(), Decimal("0"))
                target_quantity = Decimal(str(payload.get("target_quantity") or "0"))
                if target_weight == 0 and held == 0 or target_weight > 0 and held <= target_quantity:
                    accepted = target_weight
        if accepted != state.allocation_target_weight:
            self.state.save_policy_state({**state.as_dict(), "allocation_target_weight": None if accepted is None else format(accepted, "f")})

    def _cancel_t08_buy_remainders(self, *, now: datetime) -> set[str]:
        """Request DAY buy cancellation at cutoff; retain reservation until confirmed."""

        if self.config.strategy_id != "T08" or now.astimezone(self.tz).time() < time.fromisoformat(self.config.buy_cutoff):
            return set()
        blocked: set[str] = set()
        for order in self.state.open_orders():
            if str(order.get("side", "")).lower() != "buy":
                continue
            symbol = str(order.get("symbol", "")).upper()
            blocked.add(symbol)
            if str(order.get("status", "")).lower() == "cancel_pending":
                continue
            broker_id = str(order.get("broker_order_id") or "")
            if not broker_id:
                self._incident(str(order["client_order_id"]), "BUY_CUTOFF_ORDER_ID_UNRESOLVED", {"client_order_id": order["client_order_id"]})
                continue
            self.broker.cancel_order(broker_id)
            self.state.update_order(str(order["client_order_id"]), status="cancel_pending", payload={"cancel_requested_at": now.astimezone(UTC).isoformat()})
        return blocked

    def _cancel_t08_conflicting_orders(self, *, intents: Any, now: datetime) -> set[str]:
        """Cancel opposing T08 orders and defer replacements until terminal confirmation."""

        if self.config.strategy_id != "T08":
            return set()
        requested = {(str(item.symbol).upper(), str(item.side).lower()) for item in intents}
        blocked: set[str] = set()
        for order in self.state.open_orders():
            symbol, side = str(order.get("symbol", "")).upper(), str(order.get("side", "")).lower()
            if not any(wanted_symbol == symbol and wanted_side != side for wanted_symbol, wanted_side in requested):
                continue
            blocked.add(symbol)
            if str(order.get("status", "")).lower() == "cancel_pending":
                continue
            broker_id = str(order.get("broker_order_id") or "")
            if not broker_id:
                self._incident(str(order["client_order_id"]), "CONFLICTING_ORDER_ID_UNRESOLVED", {"client_order_id": order["client_order_id"]})
                continue
            self.broker.cancel_order(broker_id)
            self.state.update_order(str(order["client_order_id"]), status="cancel_pending", payload={"cancel_requested_at": now.astimezone(UTC).isoformat()})
        return blocked

    def _evaluate(self, *, now: datetime, local_now: datetime) -> tuple[Any, datetime, Decimal, dict[str, Decimal]]:
        """Reconcile and evaluate one causal session without submitting orders."""

        account = self.broker.account()
        if account.account_id != self.config.account_id:
            self._incident("account-mismatch", "ACCOUNT_ID_MISMATCH", {"expected": self.config.account_id, "actual": account.account_id})
            raise RuntimeError("ETF_LIVE_ACCOUNT_ID_MISMATCH")
        self.reconcile(now=now)
        self._sync_t08_allocation_feedback()
        features, prior_close, cutoff = self._live_features(now=now)
        positions = {str(row.get("symbol")): Decimal(str(row.get("qty", "0"))) for row in self.broker.positions()}
        purchase_allowed = True
        if self.config.strategy_id == "T08" and self.config.mode == "live":
            ledger = self.state.cash_ledger_snapshot(as_of=local_now.date().isoformat())
            settled = Decimal(ledger["settled_cash"])
            if account.non_marginable_buying_power is not None:
                settled = min(settled, account.non_marginable_buying_power)
            purchase_allowed = not ledger["blockers"] and settled >= self.config.minimum_order_notional
            cash_equity = Decimal(ledger["cash_equity"])
        else:
            # Observe-only sizing is informational. Never use broker buying
            # power as a substitute for settled cash.
            settled_text = self.state.settled_cash()
            settled = Decimal(settled_text) if settled_text is not None else account.cash
            cash_equity = settled
        prior_close_equity = cash_equity + sum((quantity * prior_close.get(symbol, Decimal("0")) for symbol, quantity in positions.items()), Decimal("0"))
        state = self._policy_state()
        execution_session = local_now.replace(hour=9, minute=30, second=0, microsecond=0)
        context = PolicyContext(
            execution_session=execution_session,
            information_cutoff=cutoff,
            features=features,
            positions=positions,
            pending_orders=self._pending_for_policy(),
            prior_close_equity=prior_close_equity,
            prior_close=prior_close,
            state=state,
            activation_session=not state.last_execution_session and not any(value > 0 for value in positions.values()),
            settled_cash=settled,
            purchase_allowed=purchase_allowed,
        )
        return self.policy.decide(context), cutoff, settled, positions

    def run_once(self, *, now: datetime) -> dict[str, Any]:
        local_now = now.astimezone(self.tz)
        if local_now.time() < time(9, 20):
            return {"status": "BEFORE_PREMARKET_DECISION"}
        if self.config.mode == "live" and self.state.activation() is None:
            return {"status": "LIVE_NOT_ARMED"}
        if self.config.mode == "live":
            self.preflight(now=now)
        if local_now.time() < time(9, 30) and self.config.mode == "live":
            # A premarket call is allowed only for the current exchange
            # session.  On a weekend or holiday, Alpaca's next_open date
            # prevents a stale signal from being recorded.
            clock = self.broker.clock()
            next_open = clock.get("next_open")
            if next_open:
                parsed_next_open = datetime.fromisoformat(str(next_open).replace("Z", "+00:00"))
                if parsed_next_open.tzinfo is None:
                    parsed_next_open = parsed_next_open.replace(tzinfo=UTC)
                if parsed_next_open.astimezone(self.tz).date() != local_now.date():
                    return {"status": "MARKET_CLOSED", "clock": clock}
        try:
            decision, cutoff, settled, _positions = self._evaluate(now=now, local_now=local_now)
        except (BrokerError, RuntimeError) as exc:
            self._incident("market-data", "MARKET_DATA_OR_RECONCILIATION_FAILURE", {"error": str(exc)})
            raise
        self.state.save_decision({
            **decision.as_dict(),
            "decision_id": decision.decision_id,
            "signal_cutoff": decision.signal_cutoff,
            "execution_session": local_now.replace(hour=9, minute=30, second=0, microsecond=0).isoformat(),
            "strategy_id": self.config.strategy_id,
        })
        if local_now.time() < time(9, 30):
            self.state.save_policy_state(decision.next_state.as_dict())
            return {"status": "PREMARKET_DECISION_RECORDED", "decision_id": decision.decision_id, "signal_cutoff": cutoff.isoformat(), "intents": len(decision.intents)}

        clock = self.broker.clock()
        if not bool(clock.get("is_open")):
            return {"status": "MARKET_CLOSED", "clock": clock, "decision_id": decision.decision_id}
        if self.config.mode != "live":
            self.state.save_policy_state(decision.next_state.as_dict())
            return {"status": "OBSERVE_ONLY", "decision_id": decision.decision_id, "signal_cutoff": cutoff.isoformat(), "intents": len(decision.intents), "submitted": []}
        # An old target transition is canceled and confirmed before a
        # conflicting replacement can be submitted.
        conflicting = self._cancel_t08_conflicting_orders(intents=decision.intents, now=now)
        cutoff_blocked = self._cancel_t08_buy_remainders(now=now)
        blocked_symbols = conflicting | cutoff_blocked
        buy_symbols = tuple(intent.symbol for intent in decision.intents if intent.side == "buy" and intent.symbol not in blocked_symbols)
        quotes: dict[str, Quote] = {}
        if buy_symbols:
            try:
                quotes = self.broker.latest_quotes(buy_symbols)
            except BrokerError as exc:
                self._incident(f"{decision.decision_id}:quotes", "BUY_QUOTE_UNAVAILABLE", {"error": str(exc), "symbols": buy_symbols})
        reserved = sum((Decimal(row["reserved_cash"]) for row in self.state.open_orders() if row["side"] == "buy"), Decimal("0"))
        available = max(Decimal("0"), settled - reserved)
        submitted: list[dict[str, Any]] = []
        for intent in decision.intents:
            if intent.symbol in blocked_symbols:
                continue
            if intent.side == "buy" and local_now.time() > time.fromisoformat(self.config.buy_cutoff):
                continue
            if intent.side == "buy" and self.state.get_meta("buys_paused") == "1":
                continue
            if intent.side == "buy" and intent.symbol not in quotes:
                self._incident(f"{decision.decision_id}:{intent.symbol}:quote", "BUY_QUOTE_MISSING", {"symbol": intent.symbol})
                continue
            result = self.submit_intent(intent=intent.to_dict(), decision_id=decision.decision_id, quote=quotes.get(intent.symbol), settled_cash=available, now=now)
            submitted.append(result)
            if intent.side == "buy":
                available -= Decimal(str(result.get("requested_qty", "0"))) * Decimal(str(result.get("limit_price", "0")))
        # Persist the deterministic evaluation only after all intents have
        # been attempted.  T08 compares future targets with reconciled actual
        # positions, so a rejected or missed buy cannot become ownership.
        self.state.save_policy_state(decision.next_state.as_dict())
        return {"status": decision.status, "decision_id": decision.decision_id, "signal_cutoff": cutoff.isoformat(), "review": decision.review, "intents": len(decision.intents), "submitted": submitted}

    def pause_buys(self, *, now: datetime, reason: str) -> None:
        self.state.set_meta("buys_paused", "1")
        self._incident("buys-paused", "BUYS_PAUSED", {"reason": reason, "at": now.astimezone(UTC).isoformat()})

    def resume_buys(self, *, now: datetime, reason: str) -> None:
        self.state.set_meta("buys_paused", "0")
        self._incident("buys-resumed", "BUYS_RESUMED", {"reason": reason, "at": now.astimezone(UTC).isoformat()})
