"""Fail-closed L11 execution runtime."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from packages.contracts.canonical import canonical_hash

from .broker import AlpacaLiveBroker, BrokerError, BrokerSubmissionUnknown, Quote
from .config import LiveConfig
from .notifications import TelegramNotifier
from .policy import L11Policy, PendingOrder, PolicyContext, PolicyState
from .state import LiveState


class LiveRuntime:
    def __init__(self, *, config: LiveConfig, broker: AlpacaLiveBroker, state: LiveState | None = None, notifier: TelegramNotifier | None = None) -> None:
        self.config = config
        self.broker = broker
        self.state = state or LiveState(config.state_path)
        self.notifier = notifier or TelegramNotifier(state=self.state, secrets_root=config.secrets_root, enabled=config.telegram_enabled)
        self.tz = ZoneInfo(config.timezone)
        self.policy = L11Policy(target_investment=config.target_investment, symbols=config.symbols, signal_symbols=config.signal_symbols)

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
        positions = self.broker.positions()
        orders = self.broker.open_orders()
        unexpected_positions = [str(x.get("symbol")) for x in positions if str(x.get("symbol")) not in self.config.symbols and Decimal(str(x.get("qty", "0"))) != 0]
        invalid_positions = [str(x.get("symbol")) for x in positions if str(x.get("symbol")) in self.config.symbols and Decimal(str(x.get("qty", "0"))) < 0]
        unexpected_orders = [str(x.get("symbol")) for x in orders if str(x.get("symbol")) not in self.config.symbols]
        unmanaged_orders = [str(x.get("client_order_id") or x.get("id") or x.get("symbol")) for x in orders if not str(x.get("client_order_id") or "").startswith("l11-")]
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
        available_cash = account.non_marginable_buying_power if account.non_marginable_buying_power is not None else account.cash
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
        self.notifier.queue(incident_id=incident_id, kind=kind, message=f"L11 {kind}: {detail}")
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
        order_id = f"l11-{decision_id}-{symbol.lower()}-{side}"
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
        self.state.save_order({"client_order_id": order_id, "decision_id": decision_id, "symbol": symbol, "side": side, "order_type": order_type, "requested_qty": requested, "limit_price": limit_price, "status": "submit_pending", "reserved_cash": reserve, "updated_at": now.astimezone(UTC).isoformat(), "payload": payload})
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
            already_recorded = Decimal(self.state.filled_quantity(client_id))
            delta = filled_qty - already_recorded
            if delta > 0:
                fill_price = Decimal(str(remote.get("filled_avg_price", "0") or "0"))
                if fill_price <= 0:
                    self._incident(client_id, "FILL_PRICE_MISSING", {"client_order_id": client_id, "status": remote_status})
                else:
                    occurred_at_text = str(remote.get("filled_at") or remote.get("updated_at") or now.astimezone(UTC).isoformat())
                    fill = {
                        "fill_id": f"{client_id}:filled:{format(filled_qty, 'f')}:{format(fill_price, 'f')}",
                        "client_order_id": client_id,
                        "broker_order_id": remote.get("id") or broker_order_id or None,
                        "symbol": str(local["symbol"]),
                        "side": str(local["side"]),
                        "quantity": format(delta, "f"),
                        "price": format(fill_price, "f"),
                        "occurred_at": occurred_at_text,
                    }
                    self.state.save_fill(fill)
                    self.state.append_event("ORDER_FILL_RECONCILED", fill, at=now)
                    reconciled_fills += 1
            self.state.update_order(client_id, status=remote_status, broker_order_id=str(remote.get("id") or broker_order_id or ""), payload=remote)

        broker_cash = account.non_marginable_buying_power
        if broker_cash is not None and broker_cash >= 0:
            self.state.set_settled_cash(format(broker_cash, "f"))
        return {"status": "RECONCILE_PASS", "account_id": account.account_id, "cash": str(account.cash), "equity": str(account.equity), "positions": positions, "open_orders": orders, "reconciled_fills": reconciled_fills, "at": now.astimezone(UTC).isoformat()}

    @staticmethod
    def _bar_date(row: dict[str, Any]) -> datetime:
        value = row.get("t", row.get("timestamp", row.get("date")))
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)

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

    def _live_features(self, *, now: datetime) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Decimal], datetime]:
        cutoff = now.astimezone(self.tz).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC) - timedelta(microseconds=1)
        bars = self.broker.daily_bars(self.config.signal_symbols + self.config.symbols, start=cutoff - timedelta(days=430), end=now.astimezone(UTC))
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

    def run_once(self, *, now: datetime) -> dict[str, Any]:
        local_now = now.astimezone(self.tz)
        if local_now.time() < time(9, 30):
            return {"status": "BEFORE_REGULAR_SESSION"}
        clock = self.broker.clock()
        if not bool(clock.get("is_open")):
            return {"status": "MARKET_CLOSED", "clock": clock}
        if self.config.mode == "live" and self.state.activation() is None:
            return {"status": "LIVE_NOT_ARMED"}
        if self.config.mode == "live":
            self.preflight(now=now)
        account = self.broker.account()
        if account.account_id != self.config.account_id:
            self._incident("account-mismatch", "ACCOUNT_ID_MISMATCH", {"expected": self.config.account_id, "actual": account.account_id})
            raise RuntimeError("ETF_LIVE_ACCOUNT_ID_MISMATCH")
        try:
            self.reconcile(now=now)
            features, prior_close, cutoff = self._live_features(now=now)
        except (BrokerError, RuntimeError) as exc:
            self._incident("market-data", "MARKET_DATA_OR_RECONCILIATION_FAILURE", {"error": str(exc)})
            raise
        positions = {str(row.get("symbol")): Decimal(str(row.get("qty", "0"))) for row in self.broker.positions()}
        settled_text = self.state.settled_cash()
        if settled_text is None:
            if positions or self.state.open_orders():
                raise RuntimeError("ETF_LIVE_SETTLED_CASH_LEDGER_UNINITIALIZED")
            initial_available = account.non_marginable_buying_power if account.non_marginable_buying_power is not None else account.cash
            self.state.set_settled_cash(format(initial_available, "f"))
            settled = initial_available
        else:
            settled = Decimal(settled_text)
        prior_close_equity = settled + sum((quantity * prior_close.get(symbol, Decimal("0")) for symbol, quantity in positions.items()), Decimal("0"))
        state = self._policy_state()
        context = PolicyContext(execution_session=local_now.date(), information_cutoff=cutoff, features=features, positions=positions, pending_orders=self._pending_for_policy(), prior_close_equity=prior_close_equity, prior_close=prior_close, state=state, activation_session=not state.last_execution_session and not any(value > 0 for value in positions.values()))
        decision = self.policy.decide(context)
        self.state.save_decision({**decision.as_dict(), "decision_id": decision.decision_id, "signal_cutoff": decision.signal_cutoff})
        self.state.save_policy_state(decision.next_state.as_dict())
        buy_symbols = tuple(intent.symbol for intent in decision.intents if intent.side == "buy")
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
        return {"status": decision.status, "decision_id": decision.decision_id, "signal_cutoff": cutoff.isoformat(), "review": decision.review, "intents": len(decision.intents), "submitted": submitted}

    def pause_buys(self, *, now: datetime, reason: str) -> None:
        self.state.set_meta("buys_paused", "1")
        self._incident("buys-paused", "BUYS_PAUSED", {"reason": reason, "at": now.astimezone(UTC).isoformat()})

    def resume_buys(self, *, now: datetime, reason: str) -> None:
        self.state.set_meta("buys_paused", "0")
        self._incident("buys-resumed", "BUYS_RESUMED", {"reason": reason, "at": now.astimezone(UTC).isoformat()})
