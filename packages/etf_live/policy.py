"""Pure, deterministic policies for the isolated ETF live service.

The policy is intentionally smaller than the research engine.  It accepts a
point-in-time feature snapshot and reconciled account state, then returns
declarative order intents.  It has no broker, filesystem, clock, or network
dependency.  A caller persists :attr:`PolicyDecision.next_state` after it
persists the decision and its intents.

The archived L11 policy has two independent cadences:

* allocation targets are evaluated on the first exchange session of an ISO
  week (or once for an explicitly designated activation review), and
* proxy trend exits are evaluated on every decision session.

All signal values must describe the completed session in
``PolicyContext.information_cutoff``.  The policy never reads a current
session price for a signal or for sizing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from hashlib import sha256
from typing import Any, Iterable, Literal, Mapping, Sequence

TRADE_SYMBOLS = ("TQQQ", "SOXL")
SIGNAL_SYMBOLS = ("QQQ", "SOXX")
_TERMINAL_ORDER_STATUSES = frozenset(
    {"filled", "canceled", "cancelled", "rejected", "expired", "done_for_day", "complete"}
)
_ZERO = Decimal("0")
_BUY = "buy"
_SELL = "sell"


def _stamp(value: date | datetime | Any) -> datetime:
    """Return a timezone-aware UTC timestamp for dates and datetime-like values."""

    if value is None:
        raise ValueError("ETF_LIVE_TIMESTAMP_REQUIRED")
    if isinstance(value, str):
        text = value.strip()
        try:
            value = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                value = date.fromisoformat(text)
            except ValueError as exc:
                raise TypeError("ETF_LIVE_TIMESTAMP_INVALID") from exc
    # pandas.Timestamp and similar values expose to_pydatetime without making
    # pandas a runtime dependency of this isolated package.
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, time.min)
    else:
        raise TypeError("ETF_LIVE_TIMESTAMP_INVALID")
    if result.tzinfo is None:
        # Fixture callers often use date-like UTC values.  Naive timestamps
        # are interpreted as UTC, while all output timestamps are explicit.
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def _iso(value: date | datetime | Any) -> str:
    return _stamp(value).isoformat().replace("+00:00", "Z")


def _week_key(value: date | datetime | Any) -> str:
    stamp = _stamp(value)
    year, week, _ = stamp.isocalendar()
    return f"{year:04d}-W{week:02d}"


def _decimal(value: Any, *, default: Decimal | None = None) -> Decimal | None:
    if value is None:
        return default
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default
    return result if result.is_finite() else default


def _positive(value: Any) -> Decimal | None:
    result = _decimal(value)
    return result if result is not None and result > _ZERO else None


def _canonical(value: Any) -> Any:
    """Convert policy inputs to a stable JSON-compatible representation."""

    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return _iso(value)
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in sorted(value.items(), key=lambda p: str(p[0]))}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_canonical(item) for item in value]
    if hasattr(value, "as_dict"):
        return _canonical(value.as_dict())
    if hasattr(value, "__dict__"):
        return _canonical(vars(value))
    return value


def _digest(payload: Mapping[str, Any]) -> str:
    body = json.dumps(_canonical(payload), ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)
    return sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Position:
    """Actual reconciled ownership supplied to the policy."""

    symbol: str
    quantity: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", str(self.symbol).upper())
        quantity = _decimal(self.quantity)
        if quantity is None or quantity < _ZERO:
            raise ValueError("ETF_LIVE_POSITION_QUANTITY_INVALID")
        object.__setattr__(self, "quantity", quantity)


@dataclass(frozen=True)
class PendingOrder:
    """An order that may still affect ownership or reserved cash."""

    symbol: str
    side: Literal["buy", "sell"] | str
    quantity: Decimal = _ZERO
    status: str = "accepted"
    decision_id: str | None = None
    session: date | datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", str(self.symbol).upper())
        side = str(self.side).lower()
        if side not in {_BUY, _SELL}:
            raise ValueError("ETF_LIVE_PENDING_ORDER_SIDE_INVALID")
        object.__setattr__(self, "side", side)
        quantity = _decimal(self.quantity)
        if quantity is None or quantity < _ZERO:
            raise ValueError("ETF_LIVE_PENDING_ORDER_QUANTITY_INVALID")
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "status", str(self.status).lower())

    @property
    def active(self) -> bool:
        return self.status not in _TERMINAL_ORDER_STATUSES


@dataclass(frozen=True)
class PolicyState:
    """Small persisted state needed for review and retry semantics.

    ``last_review_week`` is advanced even when a weekly buy is not filled.
    Therefore a canceled or missed Monday buy cannot be silently retried on
    Tuesday.  ``exited_session`` and ``exited_symbols`` make the no-reentry
    rule survive a same-session restart.
    """

    activation_review_consumed: bool = False
    last_execution_session: datetime | None = None
    last_review_week: str | None = None
    exited_session: datetime | None = None
    exited_symbols: tuple[str, ...] = ()
    last_decision_id: str | None = None
    allocation_target_weight: Decimal | None = None

    def __post_init__(self) -> None:
        if self.last_execution_session is not None:
            object.__setattr__(self, "last_execution_session", _stamp(self.last_execution_session))
        if self.exited_session is not None:
            object.__setattr__(self, "exited_session", _stamp(self.exited_session))
        symbols = tuple(sorted({str(item).upper() for item in self.exited_symbols}))
        object.__setattr__(self, "exited_symbols", symbols)
        object.__setattr__(self, "allocation_target_weight", _decimal(self.allocation_target_weight))

    @property
    def initial_activation_review_consumed(self) -> bool:
        """Compatibility name for state stores that call this an initial review."""

        return self.activation_review_consumed

    def as_dict(self) -> dict[str, Any]:
        return {
            "activation_review_consumed": self.activation_review_consumed,
            "last_execution_session": None if self.last_execution_session is None else _iso(self.last_execution_session),
            "last_review_week": self.last_review_week,
            "exited_session": None if self.exited_session is None else _iso(self.exited_session),
            "exited_symbols": list(self.exited_symbols),
            "last_decision_id": self.last_decision_id,
            "allocation_target_weight": None if self.allocation_target_weight is None else format(self.allocation_target_weight, "f"),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PolicyState":
        return cls(
            activation_review_consumed=bool(payload.get("activation_review_consumed", payload.get("initial_activation_review_consumed", False))),
            last_execution_session=payload.get("last_execution_session"),
            last_review_week=payload.get("last_review_week"),
            exited_session=payload.get("exited_session"),
            exited_symbols=tuple(payload.get("exited_symbols", ())),
            last_decision_id=payload.get("last_decision_id"),
            allocation_target_weight=payload.get("allocation_target_weight"),
        )


@dataclass(frozen=True)
class PolicyContext:
    """Point-in-time inputs for one ETF policy decision.

    ``features`` is keyed by the configured tradable and signal symbols. Each
    value may be a mapping, a row history, or a :class:`FeatureSnapshot`.
    ``pair_features`` remains available for the archived multi-ETF policy.
    """

    execution_session: date | datetime
    information_cutoff: date | datetime | None
    features: Mapping[str, Any] = field(default_factory=dict)
    positions: Mapping[str, Any] | Sequence[Any] = field(default_factory=dict)
    pending_orders: Sequence[Any] = ()
    prior_close_equity: Decimal = Decimal("0")
    prior_close: Mapping[str, Any] = field(default_factory=dict)
    state: PolicyState = field(default_factory=PolicyState)
    activation_review: bool = False
    activation_session: bool = False
    initial_activation_review: bool = False
    first_session_of_week: bool | None = None
    is_first_session_of_week: bool | None = None
    review_weekly: bool | None = None
    pair_features: Mapping[str, Any] = field(default_factory=dict)
    settled_cash: Decimal | None = None
    reserved_cash: Decimal | None = None
    purchase_allowed: bool = True

    @property
    def activation_review_requested(self) -> bool:
        return bool(self.activation_review or self.activation_session or self.initial_activation_review)


@dataclass(frozen=True)
class FeatureSnapshot:
    """Convenience feature record for fixture and runtime callers."""

    close: Decimal | float
    sma200: Decimal | float
    r126: Decimal | float
    r63: Decimal | float | None = None
    ratio: Decimal | float | None = None
    ratio_sma20: Decimal | float | None = None
    asof: date | datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "close": self.close,
            "sma200": self.sma200,
            "r126": self.r126,
            "r63": self.r63,
            "ratio": self.ratio,
            "ratio_sma20": self.ratio_sma20,
            "asof": self.asof,
        }


@dataclass(frozen=True)
class OrderIntent:
    """Deterministic, broker-neutral request produced by :class:`L11Policy`."""

    decision_id: str
    intent_id: str
    symbol: str
    side: Literal["buy", "sell"]
    quantity: Decimal
    reason: str
    signal_cutoff: datetime
    decision_session: datetime
    target_weight: Decimal | None = None
    target_quantity: Decimal | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", str(self.symbol).upper())
        side = str(self.side).lower()
        if side not in {_BUY, _SELL}:
            raise ValueError("ETF_LIVE_INTENT_SIDE_INVALID")
        object.__setattr__(self, "side", side)
        quantity = _decimal(self.quantity)
        if quantity is None or quantity <= _ZERO:
            raise ValueError("ETF_LIVE_INTENT_QUANTITY_INVALID")
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "signal_cutoff", _stamp(self.signal_cutoff))
        object.__setattr__(self, "decision_session", _stamp(self.decision_session))
        weight = _decimal(self.target_weight)
        target_quantity = _decimal(self.target_quantity)
        object.__setattr__(self, "target_weight", weight)
        object.__setattr__(self, "target_quantity", target_quantity)

    @property
    def requested_quantity(self) -> Decimal:
        return self.quantity

    @property
    def action(self) -> str:
        return self.side

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "intent_id": self.intent_id,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": format(self.quantity, "f"),
            "requested_quantity": format(self.quantity, "f"),
            "reason": self.reason,
            "signal_cutoff": _iso(self.signal_cutoff),
            "decision_session": _iso(self.decision_session),
            "target_weight": None if self.target_weight is None else format(self.target_weight, "f"),
            "target_quantity": None if self.target_quantity is None else format(self.target_quantity, "f"),
            "metadata": _canonical(self.metadata),
        }

    to_dict = as_dict

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]


@dataclass(frozen=True)
class PolicyDecision:
    """Result of a pure policy evaluation."""

    decision_id: str
    intents: tuple[OrderIntent, ...]
    next_state: PolicyState
    review: bool
    activation_review: bool
    signal_cutoff: datetime | None
    status: str = "READY"
    blocked_reason: str | None = None

    @property
    def orders(self) -> tuple[OrderIntent, ...]:
        return self.intents

    def __iter__(self):
        return iter(self.intents)

    def __len__(self) -> int:
        return len(self.intents)

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "intents": [item.as_dict() for item in self.intents],
            "review": self.review,
            "activation_review": self.activation_review,
            "signal_cutoff": None if self.signal_cutoff is None else _iso(self.signal_cutoff),
            "status": self.status,
            "blocked_reason": self.blocked_reason,
            "next_state": self.next_state.as_dict(),
        }


class L11Policy:
    """Pure broad-first TQQQ/SOXL policy.

    The constructor accepts the three values used by the live runtime.  The
    symbol sets are validated here so an accidental policy/config mismatch
    fails closed before any intent is produced.
    """

    strategy_id = "L11"
    quantity_decimals = 6

    def __init__(
        self,
        *,
        target_investment: Decimal | float = Decimal("0.99"),
        symbols: Sequence[str] = TRADE_SYMBOLS,
        signal_symbols: Sequence[str] = SIGNAL_SYMBOLS,
    ) -> None:
        target = _decimal(target_investment)
        if target is None or target <= _ZERO or target > Decimal("0.99"):
            raise ValueError("ETF_LIVE_TARGET_INVESTMENT_INVALID")
        normalized_symbols = tuple(str(item).upper() for item in symbols)
        normalized_signals = tuple(str(item).upper() for item in signal_symbols)
        if set(normalized_symbols) != set(TRADE_SYMBOLS) or len(normalized_symbols) != 2:
            raise ValueError("ETF_LIVE_L11_SYMBOLS_INVALID")
        if set(normalized_signals) != set(SIGNAL_SYMBOLS) or len(normalized_signals) != 2:
            raise ValueError("ETF_LIVE_L11_SIGNAL_SYMBOLS_INVALID")
        self.target_investment = target
        self.symbols = normalized_symbols
        self.signal_symbols = normalized_signals
        self.broad_symbol = "TQQQ"
        self.sector_symbol = "SOXL"
        self.broad_proxy = "QQQ"
        self.sector_proxy = "SOXX"

    def decide(self, context: PolicyContext) -> PolicyDecision:
        execution = _stamp(context.execution_session)
        cutoff = None if context.information_cutoff is None else _stamp(context.information_cutoff)
        if cutoff is None:
            return self._blocked(context, execution, "ETF_LIVE_NO_INFORMATION_CUTOFF")
        if cutoff >= execution:
            return self._blocked(context, execution, "ETF_LIVE_INFORMATION_CUTOFF_NOT_PRIOR")

        positions = self._positions(context.positions)
        pending = self._pending(context.pending_orders)
        features = context.features
        pair = context.pair_features
        broad_snapshot = self._snapshot(features, self.broad_proxy, cutoff)
        sector_snapshot = self._snapshot(features, self.sector_proxy, cutoff)
        broad_ok = self._eligible(broad_snapshot)
        sector_ok = self._eligible(sector_snapshot)
        if sector_ok:
            vr = self._value(sector_snapshot, "r63", cutoff)
            ur = self._value(broad_snapshot, "r63", cutoff)
            ratio = self._pair_value(pair, features, "ratio", cutoff)
            ratio_sma = self._pair_value(pair, features, "ratio_sma20", cutoff)
            sector_ok = all(item is not None for item in (vr, ur, ratio, ratio_sma)) and vr > ur and ratio > ratio_sma  # type: ignore[operator]

        targets: dict[str, Decimal] = {}
        if broad_ok and sector_ok:
            half = self.target_investment / Decimal("2")
            targets = {self.broad_symbol: half, self.sector_symbol: half}
        elif broad_ok:
            targets = {self.broad_symbol: self.target_investment}
        elif sector_ok:
            targets = {self.sector_symbol: self.target_investment / Decimal("2")}

        state = context.state if isinstance(context.state, PolicyState) else PolicyState.from_dict(context.state)
        review = self._is_review(context, state, execution)
        activation_review = bool(context.activation_review_requested and not state.activation_review_consumed)
        review = bool(review or activation_review)

        exits: list[tuple[str, str]] = []
        for symbol, snapshot in (
            (self.broad_symbol, broad_snapshot),
            (self.sector_symbol, sector_snapshot),
        ):
            if positions.get(symbol, _ZERO) <= _ZERO:
                continue
            close = self._value(snapshot, "close", cutoff)
            trend = self._value(snapshot, "sma200", cutoff)
            # Strict inequality is part of the frozen L11 rule.  Equality
            # retains the position and eligibility is likewise strict.
            if close is not None and trend is not None and close < trend:
                if not self._has_active(pending, symbol, _SELL):
                    # Keep the frozen research reason stable across the
                    # production adapter and research decision tape.
                    exits.append((symbol, "L11_DAILY_PROXY_SMA200_EXIT"))

        exited = {symbol for symbol, _ in exits}
        if state.exited_session is not None and state.exited_session == execution:
            exited.update(state.exited_symbols)

        intents_payload: list[dict[str, Any]] = []
        # Daily reductions always precede weekly target changes.  This order
        # is also the order used to derive deterministic intent IDs.
        for symbol, reason in exits:
            intents_payload.append(
                {
                    "symbol": symbol,
                    "side": _SELL,
                    "quantity": positions[symbol],
                    "reason": reason,
                    "target_weight": _ZERO,
                    "target_quantity": _ZERO,
                }
            )

        if review:
            for symbol in self.symbols:
                if symbol in exited:
                    continue
                current = positions.get(symbol, _ZERO)
                target_weight = targets.get(symbol, _ZERO)
                if target_weight <= _ZERO:
                    if current > _ZERO and not self._has_active(pending, symbol, _SELL):
                        intents_payload.append(
                            {
                                "symbol": symbol,
                                "side": _SELL,
                                "quantity": current,
                                "reason": "L11_BROAD_FIRST_LEADERSHIP",
                                "target_weight": _ZERO,
                                "target_quantity": _ZERO,
                            }
                        )
                    continue
                target_quantity = self._target_quantity(symbol, target_weight, context)
                # Missing prior-close price/equity blocks an increase.  It
                # never authorizes a current-session substitute.
                if target_quantity is None:
                    continue
                delta = target_quantity - current
                if delta > _ZERO:
                    if self._has_active(pending, symbol, _BUY):
                        continue
                    intents_payload.append(
                        {
                            "symbol": symbol,
                            "side": _BUY,
                            "quantity": delta,
                            "reason": "L11_BROAD_FIRST_LEADERSHIP",
                            "target_weight": target_weight,
                            "target_quantity": target_quantity,
                        }
                    )
                elif delta < _ZERO:
                    if self._has_active(pending, symbol, _SELL):
                        continue
                    intents_payload.append(
                        {
                            "symbol": symbol,
                            "side": _SELL,
                            "quantity": -delta,
                            "reason": "L11_BROAD_FIRST_LEADERSHIP",
                            "target_weight": target_weight,
                            "target_quantity": target_quantity,
                        }
                    )

        decision_payload = {
            "strategy_id": self.strategy_id,
            "execution_session": execution,
            "information_cutoff": cutoff,
            "review": review,
            "activation_review": activation_review,
            "targets": targets,
            "positions": positions,
            "pending": pending,
            "intents": intents_payload,
        }
        decision_id = f"l11d-{_digest(decision_payload)[:24]}"
        intents = tuple(
            OrderIntent(
                decision_id=decision_id,
                intent_id=f"l11i-{_digest({'decision_id': decision_id, 'index': index, 'intent': payload})[:20]}",
                symbol=payload["symbol"],
                side=payload["side"],
                quantity=self._floor(payload["quantity"]),
                reason=payload["reason"],
                signal_cutoff=cutoff,
                decision_session=execution,
                target_weight=payload.get("target_weight"),
                target_quantity=payload.get("target_quantity"),
                metadata={"strategy_id": self.strategy_id, "review": review},
            )
            for index, payload in enumerate(intents_payload)
            if self._floor(payload["quantity"]) > _ZERO
        )

        next_state = replace(
            state,
            activation_review_consumed=state.activation_review_consumed or activation_review,
            last_execution_session=execution,
            last_review_week=_week_key(execution) if review else state.last_review_week,
            exited_session=execution if exited else (None if state.exited_session != execution else state.exited_session),
            exited_symbols=tuple(sorted(exited)) if exited else (() if state.exited_session != execution else state.exited_symbols),
            last_decision_id=decision_id,
        )
        return PolicyDecision(
            decision_id=decision_id,
            intents=intents,
            next_state=next_state,
            review=review,
            activation_review=activation_review,
            signal_cutoff=cutoff,
        )

    evaluate = decide

    def __call__(self, context: PolicyContext) -> PolicyDecision:
        return self.decide(context)

    def _blocked(self, context: PolicyContext, execution: datetime, reason: str) -> PolicyDecision:
        # Blocked evaluations do not consume an activation review or advance
        # weekly state.  The caller can retry after obtaining a valid cutoff.
        payload = {"strategy_id": self.strategy_id, "execution_session": execution, "reason": reason}
        decision_id = f"l11d-{_digest(payload)[:24]}"
        state = context.state if isinstance(context.state, PolicyState) else PolicyState.from_dict(context.state)
        return PolicyDecision(decision_id, (), state, False, False, None, status="BLOCKED", blocked_reason=reason)

    def _is_review(self, context: PolicyContext, state: PolicyState, execution: datetime) -> bool:
        week = _week_key(execution)
        if state.last_review_week == week:
            return False
        explicit = context.review_weekly
        if explicit is None:
            explicit = context.first_session_of_week
        if explicit is None:
            explicit = context.is_first_session_of_week
        if explicit is not None:
            return bool(explicit)
        if state.last_execution_session is None:
            return True
        return _week_key(state.last_execution_session) != week

    def _snapshot(self, features: Mapping[str, Any], symbol: str, cutoff: datetime) -> Any:
        value = features.get(symbol)
        if value is None:
            value = features.get(symbol.upper())
        if value is None:
            return None
        # A feature provider may expose rows under ``rows``/``history``.  Use
        # the newest row at or before the cutoff and ignore future rows.
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, Mapping)):
            rows = value
        elif isinstance(value, Mapping):
            rows = value.get("rows", value.get("history"))
        else:
            rows = None
        if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes)):
            eligible = []
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                stamp = row.get("asof", row.get("date", row.get("timestamp")))
                if stamp is None:
                    continue
                try:
                    row_stamp = _stamp(stamp)
                except (TypeError, ValueError):
                    continue
                if row_stamp <= cutoff:
                    eligible.append((row_stamp, row))
            if eligible:
                return max(eligible, key=lambda item: item[0])[1]
            return None
        if isinstance(value, Mapping):
            asof = value.get("asof", value.get("information_cutoff"))
            if asof is not None:
                try:
                    if _stamp(asof) > cutoff:
                        return None
                except (TypeError, ValueError):
                    return None
        else:
            asof = getattr(value, "asof", None)
            if asof is not None:
                try:
                    if _stamp(asof) > cutoff:
                        return None
                except (TypeError, ValueError):
                    return None
        return value

    def _value(self, snapshot: Any, name: str, cutoff: datetime) -> Decimal | None:
        if snapshot is None:
            return None
        aliases = {
            "r126": ("r126", "return126", "return_126", "momentum126"),
            "r63": ("r63", "return63", "return_63", "momentum63"),
            "sma200": ("sma200", "sma_200", "trend200"),
            "close": ("close", "previous_close", "prior_close"),
            "asymmetry": ("asymmetry", "downside_asymmetry", "downside_ratio", "A", "a"),
            "vol_ratio": ("vol_ratio", "volatility_ratio", "sigma20_over_sigma60", "V", "v"),
        }
        names = aliases.get(name, (name,))
        raw = None
        if isinstance(snapshot, Mapping):
            for candidate in names:
                if candidate in snapshot:
                    raw = snapshot[candidate]
                    break
        else:
            for candidate in names:
                if hasattr(snapshot, candidate):
                    raw = getattr(snapshot, candidate)
                    break
        return _decimal(raw)

    def _pair_value(self, pair: Mapping[str, Any], features: Mapping[str, Any], name: str, cutoff: datetime) -> Decimal | None:
        candidates: list[Any] = [pair.get(name)] if isinstance(pair, Mapping) else []
        for key in ("PAIR", "pair", "SOXX/QQQ", "ratio"):
            item = features.get(key)
            if isinstance(item, Mapping):
                candidates.append(item.get(name))
        sector = features.get(self.sector_proxy)
        if isinstance(sector, Mapping):
            candidates.append(sector.get(name))
        elif sector is not None and hasattr(sector, name):
            candidates.append(getattr(sector, name))
        for value in candidates:
            if value is not None:
                result = _decimal(value)
                if result is not None:
                    return result
        return None

    def _eligible(self, snapshot: Any) -> bool:
        close = self._value(snapshot, "close", datetime.max.replace(tzinfo=UTC))
        trend = self._value(snapshot, "sma200", datetime.max.replace(tzinfo=UTC))
        momentum = self._value(snapshot, "r126", datetime.max.replace(tzinfo=UTC))
        return all(value is not None for value in (close, trend, momentum)) and close > trend and momentum > _ZERO  # type: ignore[operator]

    def _positions(self, value: Mapping[str, Any] | Sequence[Any]) -> dict[str, Decimal]:
        result: dict[str, Decimal] = {}
        if isinstance(value, Mapping):
            items = value.items()
        else:
            def _sequence_items() -> Iterable[tuple[Any, Any]]:
                for item in value:
                    if isinstance(item, Mapping):
                        yield item.get("symbol"), item
                    elif hasattr(item, "symbol"):
                        yield item.symbol, item
                    else:
                        yield None, item

            items = _sequence_items()
        for key, item in items:
            symbol = str(key or getattr(item, "symbol", "")).upper()
            if isinstance(item, Position):
                quantity = item.quantity
            elif isinstance(item, Mapping):
                quantity = item.get("quantity", item.get("qty", item.get("shares", _ZERO)))
            else:
                quantity = getattr(item, "quantity", item)
            parsed = _decimal(quantity)
            if symbol and parsed is not None and parsed > _ZERO:
                result[symbol] = parsed
        return result

    def _pending(self, value: Sequence[Any]) -> tuple[PendingOrder, ...]:
        output: list[PendingOrder] = []
        for item in value:
            if isinstance(item, PendingOrder):
                output.append(item)
                continue
            if isinstance(item, Mapping):
                try:
                    output.append(PendingOrder(symbol=item.get("symbol", ""), side=item.get("side", ""), quantity=item.get("quantity", item.get("qty", _ZERO)), status=item.get("status", "accepted"), decision_id=item.get("decision_id"), session=item.get("session")))
                except (TypeError, ValueError):
                    continue
        return tuple(output)

    @staticmethod
    def _has_active(pending: Iterable[PendingOrder], symbol: str, side: str | None = None) -> bool:
        return any(item.active and item.symbol == symbol and (side is None or item.side == side) for item in pending)

    def _target_quantity(self, symbol: str, weight: Decimal, context: PolicyContext) -> Decimal | None:
        equity = _positive(context.prior_close_equity)
        price = _positive(context.prior_close.get(symbol))
        if equity is None or price is None:
            return None
        return self._floor(equity * weight / price)

    @classmethod
    def _floor(cls, value: Any) -> Decimal:
        parsed = _decimal(value, default=_ZERO) or _ZERO
        quantum = Decimal(1).scaleb(-cls.quantity_decimals)
        return parsed.quantize(quantum, rounding=ROUND_DOWN)


class T08Policy(L11Policy):
    """Pure daily TECL/cash implementation of research strategy T08.

    A completed allocation transition is held in fixed shares until the
    strategy allocation state changes. Execution feedback, rather than a
    proposed order, records a completed transition. This prevents daily
    rebalance churn from market-driven weight drift.
    """

    strategy_id = "T08"
    minimum_order_notional = Decimal("5")

    def __init__(
        self,
        *,
        target_investment: Decimal | float = Decimal("0.99"),
        symbols: Sequence[str] = ("TECL",),
        signal_symbols: Sequence[str] = ("XLK",),
    ) -> None:
        target = _decimal(target_investment)
        if target is None or target <= _ZERO or target > Decimal("0.99"):
            raise ValueError("ETF_LIVE_TARGET_INVESTMENT_INVALID")
        normalized_symbols = tuple(str(item).upper() for item in symbols)
        normalized_signals = tuple(str(item).upper() for item in signal_symbols)
        if normalized_symbols != ("TECL",):
            raise ValueError("ETF_LIVE_T08_SYMBOLS_INVALID")
        if normalized_signals != ("XLK",):
            raise ValueError("ETF_LIVE_T08_SIGNAL_SYMBOLS_INVALID")
        self.target_investment = target
        self.symbols = normalized_symbols
        self.signal_symbols = normalized_signals
        self.trade_symbol = "TECL"
        self.signal_symbol = "XLK"

    def decide(self, context: PolicyContext) -> PolicyDecision:
        execution = _stamp(context.execution_session)
        cutoff = None if context.information_cutoff is None else _stamp(context.information_cutoff)
        if cutoff is None:
            return self._blocked_t08(context, execution, "ETF_LIVE_NO_INFORMATION_CUTOFF")
        if cutoff >= execution:
            return self._blocked_t08(context, execution, "ETF_LIVE_INFORMATION_CUTOFF_NOT_PRIOR")

        state = context.state if isinstance(context.state, PolicyState) else PolicyState.from_dict(context.state)
        positions = self._positions(context.positions)
        pending = self._pending(context.pending_orders)
        snapshot = self._snapshot(context.features, self.signal_symbol, cutoff)
        close = self._value(snapshot, "close", cutoff)
        trend = self._value(snapshot, "sma200", cutoff)
        asymmetry = self._value(snapshot, "asymmetry", cutoff)
        vol_ratio = self._value(snapshot, "vol_ratio", cutoff)
        if asymmetry is None:
            downside = self._value(snapshot, "downside_rms20", cutoff)
            upside = self._value(snapshot, "upside_rms20", cutoff)
            if downside is not None and upside is not None:
                if downside == _ZERO and upside == _ZERO:
                    asymmetry = Decimal("1")
                elif upside == _ZERO:
                    asymmetry = Decimal("999999") if downside > _ZERO else Decimal("1")
                else:
                    asymmetry = downside / upside
        if vol_ratio is None:
            sigma20 = self._value(snapshot, "sigma20", cutoff)
            sigma60 = self._value(snapshot, "sigma60", cutoff)
            if sigma20 is not None and sigma60 is not None:
                vol_ratio = _ZERO if sigma60 == _ZERO and sigma20 == _ZERO else (None if sigma60 == _ZERO else sigma20 / sigma60)

        held = positions.get(self.trade_symbol, _ZERO)
        below_trend = close is not None and trend is not None and close < trend
        equal_trend = close is not None and trend is not None and close == trend
        target: Decimal | None
        if below_trend:
            target = _ZERO
        elif equal_trend:
            # Equality retains the actual state.  No target is inferred from
            # a potentially stale risk snapshot at the boundary.
            target = None
        elif close is not None and trend is not None and close > trend and asymmetry is not None and vol_ratio is not None:
            target = Decimal("0.495") if asymmetry > Decimal("1.5") and vol_ratio > Decimal("1.25") else self.target_investment
        else:
            # Missing risk features block increases but preserve a valid
            # existing position.  This is the fail-closed live behavior.
            target = None

        exited = bool(below_trend and held > _ZERO)
        if state.exited_session is not None and state.exited_session == execution and self.trade_symbol in state.exited_symbols:
            exited = True

        intents_payload: list[dict[str, Any]] = []
        accepted_target = state.allocation_target_weight
        transition_complete_without_order = False
        if target is not None and not (exited and target > _ZERO):
            target_quantity = self._target_quantity(self.trade_symbol, target, context) if target > _ZERO else _ZERO
            if target > _ZERO and target_quantity is None:
                target_quantity = None
            if target_quantity is not None:
                if target == accepted_target and target > _ZERO:
                    # Hold acquired shares. Market movement does not create
                    # an allocation transition or trigger daily rebalancing.
                    transition_complete_without_order = True
                else:
                    delta = target_quantity - held
                    if delta > _ZERO:
                        if context.purchase_allowed and not self._has_active(pending, self.trade_symbol, _BUY) and not exited:
                            market_value = delta * (_positive(context.prior_close.get(self.trade_symbol)) or _ZERO)
                            if market_value < self.minimum_order_notional:
                                transition_complete_without_order = True
                            else:
                                intents_payload.append({
                                    "symbol": self.trade_symbol,
                                    "side": _BUY,
                                    "quantity": delta,
                                    "reason": "T08_ALLOCATION_INCREASE",
                                    "target_weight": target,
                                    "target_quantity": target_quantity,
                                })
                    elif delta < _ZERO:
                        if not self._has_active(pending, self.trade_symbol, _SELL):
                            market_value = -delta * (_positive(context.prior_close.get(self.trade_symbol)) or _ZERO)
                            if target > _ZERO and market_value < self.minimum_order_notional:
                                transition_complete_without_order = True
                            else:
                                intents_payload.append({
                                    "symbol": self.trade_symbol,
                                    "side": _SELL,
                                    "quantity": -delta,
                                    "reason": "T08_ALLOCATION_REDUCTION" if target > _ZERO else "T08_XLK_SMA200_EXIT",
                                    "target_weight": target,
                                    "target_quantity": target_quantity,
                                })
                    else:
                        transition_complete_without_order = True
                if transition_complete_without_order:
                    accepted_target = target

        decision_payload = {
            "strategy_id": self.strategy_id,
            "execution_session": execution,
            "information_cutoff": cutoff,
            "target": target,
            "features": {"close": close, "sma200": trend, "asymmetry": asymmetry, "vol_ratio": vol_ratio},
            "positions": positions,
            "pending": pending,
            "purchase_allowed": context.purchase_allowed,
            "intents": intents_payload,
        }
        decision_id = f"t08d-{_digest(decision_payload)[:24]}"
        intents = tuple(
            OrderIntent(
                decision_id=decision_id,
                intent_id=f"t08i-{_digest({'decision_id': decision_id, 'index': index, 'intent': payload})[:20]}",
                symbol=payload["symbol"],
                side=payload["side"],
                quantity=self._floor(payload["quantity"]),
                reason=payload["reason"],
                signal_cutoff=cutoff,
                decision_session=execution,
                target_weight=payload.get("target_weight"),
                target_quantity=payload.get("target_quantity"),
                metadata={"strategy_id": self.strategy_id, "daily": True},
            )
            for index, payload in enumerate(intents_payload)
            if self._floor(payload["quantity"]) > _ZERO
        )
        next_state = replace(
            state,
            activation_review_consumed=state.activation_review_consumed or context.activation_review_requested,
            last_execution_session=execution,
            exited_session=execution if exited else (None if state.exited_session == execution else state.exited_session),
            exited_symbols=(self.trade_symbol,) if exited else (() if state.exited_session == execution else state.exited_symbols),
            last_decision_id=decision_id,
            allocation_target_weight=accepted_target,
        )
        return PolicyDecision(
            decision_id=decision_id,
            intents=intents,
            next_state=next_state,
            review=True,
            activation_review=bool(context.activation_review_requested and not state.activation_review_consumed),
            signal_cutoff=cutoff,
        )

    evaluate = decide

    def _blocked_t08(self, context: PolicyContext, execution: datetime, reason: str) -> PolicyDecision:
        payload = {"strategy_id": self.strategy_id, "execution_session": execution, "reason": reason}
        decision_id = f"t08d-{_digest(payload)[:24]}"
        state = context.state if isinstance(context.state, PolicyState) else PolicyState.from_dict(context.state)
        return PolicyDecision(decision_id, (), state, False, False, None, status="BLOCKED", blocked_reason=reason)


__all__ = [
    "FeatureSnapshot",
    "L11Policy",
    "T08Policy",
    "OrderIntent",
    "PendingOrder",
    "PolicyContext",
    "PolicyDecision",
    "PolicyState",
    "Position",
]
