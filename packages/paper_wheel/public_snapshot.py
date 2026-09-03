"""Publish a sanitized, hash-bound Alpaca paper-account evidence snapshot.

The command is intentionally read-only. Credentials and the broker origin are
accepted only through environment variables and are never written to output.
Only filled orders carrying the configured system-owned client-order prefix are
eligible for publication.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

_DEFAULT_BASELINE = Decimal("100000")
_DEFAULT_PREFIX = "rs-v135"
_DEFAULT_STRATEGY = "v13.5"
_DEFAULT_UNDERLYING = "QQQ"
_OPTION_SYMBOL = re.compile(
    r"^(?P<underlying>[A-Z]{1,6})(?P<expiry>\d{6})(?P<right>[CP])(?P<strike>\d{8})$"
)


class PublicSnapshotError(RuntimeError):
    """Raised when broker evidence cannot be safely published."""


def _decimal(value: Any, *, field: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PublicSnapshotError(f"PUBLIC_SNAPSHOT_INVALID_{field.upper()}") from exc


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01")))


def _canonical_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _option_contract(symbol: str) -> dict[str, str | float | None]:
    match = _OPTION_SYMBOL.fullmatch(symbol)
    if match is None:
        return {
            "symbol": symbol,
            "underlying": None,
            "expiry": None,
            "option_type": None,
            "strike": None,
        }
    expiry = datetime.strptime(match.group("expiry"), "%y%m%d").date().isoformat()
    strike = Decimal(match.group("strike")) / Decimal("1000")
    return {
        "symbol": symbol,
        "underlying": match.group("underlying"),
        "expiry": expiry,
        "option_type": "CALL" if match.group("right") == "C" else "PUT",
        "strike": float(strike),
    }


def _public_order(order: dict[str, Any], *, prefix: str) -> dict[str, Any] | None:
    client_order_id = str(order.get("client_order_id") or "")
    if not client_order_id.startswith(f"{prefix}-"):
        return None
    filled_quantity = _decimal(order.get("filled_qty", "0"), field="filled_quantity")
    average = order.get("filled_avg_price")
    filled_at = _timestamp(order.get("filled_at") or order.get("updated_at"))
    if filled_quantity <= 0 or average is None or filled_at is None:
        return None
    side = str(order.get("side") or "").upper()
    position_intent = str(order.get("position_intent") or "").upper()
    action = position_intent or side or "FILLED"
    return {
        "filled_at": filled_at,
        "action": action,
        "side": side,
        "quantity": float(filled_quantity),
        "average_fill_price": _money(_decimal(average, field="average_fill_price")),
        "contract": _option_contract(str(order.get("symbol") or "").upper()),
        "system_ref": client_order_id[-8:],
    }


def _portfolio_points(
    history: dict[str, Any] | None,
    *,
    baseline: Decimal,
) -> list[dict[str, Any]]:
    """Return bounded, sanitized daily equity history for public charting."""
    if history is None:
        return []
    timestamps = history.get("timestamp")
    equities = history.get("equity")
    if not isinstance(timestamps, list) or not isinstance(equities, list):
        return []

    points_by_timestamp: dict[str, dict[str, Any]] = {}
    for timestamp, raw_equity in zip(timestamps, equities, strict=False):
        try:
            parsed_timestamp = datetime.fromtimestamp(float(timestamp), tz=UTC)
            equity = _decimal(raw_equity, field="portfolio_equity")
        except (OSError, OverflowError, TypeError, ValueError, PublicSnapshotError):
            continue
        if equity <= 0:
            continue
        captured_at = parsed_timestamp.isoformat().replace("+00:00", "Z")
        points_by_timestamp[captured_at] = {
            "timestamp": captured_at,
            "equity": _money(equity),
            "total_pnl": _money(equity - baseline),
            "total_return": float((equity / baseline) - Decimal("1")),
        }
    return [points_by_timestamp[key] for key in sorted(points_by_timestamp)][-366:]


def build_snapshot(
    *,
    account: dict[str, Any],
    orders: list[dict[str, Any]],
    expected_account_id: str,
    generated_at: datetime,
    portfolio_history: dict[str, Any] | None = None,
    baseline: Decimal = _DEFAULT_BASELINE,
    client_order_prefix: str = _DEFAULT_PREFIX,
) -> dict[str, Any]:
    """Build public evidence from already-fetched broker payloads."""
    account_id = str(account.get("id") or "")
    if not account_id or account_id != expected_account_id:
        raise PublicSnapshotError("PUBLIC_SNAPSHOT_ACCOUNT_MISMATCH")
    equity = _decimal(account.get("equity"), field="equity")
    previous_equity = _decimal(account.get("last_equity"), field="last_equity")
    cash = _decimal(account.get("cash"), field="cash")
    buying_power = _decimal(account.get("buying_power"), field="buying_power")
    if baseline <= 0 or previous_equity <= 0:
        raise PublicSnapshotError("PUBLIC_SNAPSHOT_INVALID_BASELINE")

    public_orders = [
        item
        for order in orders
        if (item := _public_order(order, prefix=client_order_prefix)) is not None
    ]
    public_orders.sort(key=lambda item: item["filled_at"], reverse=True)
    public_orders = public_orders[:10]
    captured_at = generated_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    history_points = _portfolio_points(portfolio_history, baseline=baseline)
    payload: dict[str, Any] = {
        "schema_version": "stable-income-generator-live-paper/v3",
        "source": "broker_reported_paper",
        "generated_at": captured_at,
        "refresh_contract": {
            "scheduled_interval_seconds": 1800,
            "browser_poll_seconds": 60,
            "stale_after_seconds": 5400,
            "publishing_window": {
                "timezone": "America/New_York",
                "weekdays": ["MON", "TUE", "WED", "THU", "FRI"],
                "start": "09:00",
                "final_run": "17:00",
            },
            "delivery": "Sanitized JSON is regenerated by GitHub Actions and deployed with GitHub Pages.",
        },
        "account": {
            "account_id": account_id,
            "status": str(account.get("status") or "UNKNOWN"),
            "equity": _money(equity),
            "cash": _money(cash),
            "buying_power": _money(buying_power),
            "starting_baseline": _money(baseline),
            "total_pnl": _money(equity - baseline),
            "total_return": float((equity / baseline) - Decimal("1")),
            "day_start_equity": _money(previous_equity),
            "day_pnl": _money(equity - previous_equity),
            "day_return": float((equity / previous_equity) - Decimal("1")),
        },
        "strategy": {
            "strategy_id": _DEFAULT_STRATEGY,
            "underlying": _DEFAULT_UNDERLYING,
            "client_order_prefix": client_order_prefix,
        },
        "recent_filled_system_orders": public_orders,
        "portfolio_history": {
            "status": "available" if history_points else "unavailable",
            "period": "1A",
            "timeframe": "1D",
            "points": history_points,
        },
        "publication_scope": {
            "paper_only": True,
            "account_id_publication_approved": True,
            "order_filter": "Filled broker orders with the V13.5 system-owned client-order prefix only.",
            "excluded": [
                "API credentials",
                "broker origin",
                "broker order IDs",
                "order submission and cancellation controls",
            ],
        },
    }
    payload["artifact_hash"] = _canonical_hash(payload)
    return payload


def _request_json(*, base_url: str, path: str, key: str, secret: str) -> Any:
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        headers={
            "Accept": "application/json",
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret,
            "User-Agent": "stable-income-generator-public-evidence/1",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed trusted secret origin
            return json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise PublicSnapshotError("PUBLIC_SNAPSHOT_BROKER_READ_FAILED") from exc


def publish(*, output: Path) -> dict[str, Any]:
    """Fetch read-only account evidence and atomically write a public snapshot."""
    names = (
        "ALPACA_PAPER_API_KEY",
        "ALPACA_PAPER_API_SECRET",
        "ALPACA_PAPER_ACCOUNT_ID",
        "ALPACA_PAPER_BASE_URL",
    )
    values = {name: os.environ.get(name, "").strip() for name in names}
    if any(not values[name] for name in names):
        raise PublicSnapshotError("PUBLIC_SNAPSHOT_ENVIRONMENT_INCOMPLETE")
    account = _request_json(
        base_url=values["ALPACA_PAPER_BASE_URL"],
        path="/v2/account",
        key=values["ALPACA_PAPER_API_KEY"],
        secret=values["ALPACA_PAPER_API_SECRET"],
    )
    query = urlencode(
        {"status": "closed", "limit": 500, "direction": "desc", "nested": "true"}
    )
    orders = _request_json(
        base_url=values["ALPACA_PAPER_BASE_URL"],
        path=f"/v2/orders?{query}",
        key=values["ALPACA_PAPER_API_KEY"],
        secret=values["ALPACA_PAPER_API_SECRET"],
    )
    history_query = urlencode(
        {
            "period": "1A",
            "timeframe": "1D",
            "intraday_reporting": "market_hours",
        }
    )
    try:
        portfolio_history = _request_json(
            base_url=values["ALPACA_PAPER_BASE_URL"],
            path=f"/v2/account/portfolio/history?{history_query}",
            key=values["ALPACA_PAPER_API_KEY"],
            secret=values["ALPACA_PAPER_API_SECRET"],
        )
    except PublicSnapshotError:
        portfolio_history = None
    if not isinstance(account, dict) or not isinstance(orders, list):
        raise PublicSnapshotError("PUBLIC_SNAPSHOT_BROKER_PAYLOAD_INVALID")
    snapshot = build_snapshot(
        account=account,
        orders=orders,
        expected_account_id=values["ALPACA_PAPER_ACCOUNT_ID"],
        generated_at=datetime.now(tz=UTC),
        portfolio_history=portfolio_history if isinstance(portfolio_history, dict) else None,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    snapshot = publish(output=args.output)
    print(
        "PUBLIC_PAPER_SNAPSHOT_READY "
        f"generated_at={snapshot['generated_at']} "
        f"orders={len(snapshot['recent_filled_system_orders'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
