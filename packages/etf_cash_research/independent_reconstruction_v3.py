"""Independent v3 account reconstruction.

The v3 simulator emits several outcome artifacts, but none of those artifacts
is authoritative by itself.  This module rebuilds an account from the raw
closing prices, resolved corporate actions, actual fills, and cash/settlement
events.  It deliberately does not accept a saved equity curve as an input to
the reconstruction.  ``compare_saved_equity`` is a separate, final audit
step.

The implementation uses integer microshares for physical and component
ownership.  It accepts the v2 column names while also accepting the explicit
v3 names (``quantity_microshares``, ``cash_settled_delta`` and so on) so the
auditor can be used while the repaired engine is being migrated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

MICROSHARES = 1_000_000
DEFAULT_INITIAL_CASH = 1_000.0
DEFAULT_TOLERANCE = 0.01
_DECIMAL_MICROSHARES = Decimal(MICROSHARES)


@dataclass(frozen=True)
class ReconstructionResult:
    """Independent account reconstruction and ledger consistency checks."""

    daily: pd.DataFrame
    dividend_checks: pd.DataFrame
    settlement_checks: pd.DataFrame
    fee_checks: pd.DataFrame
    component_checks: pd.DataFrame
    summary: dict[str, Any]


def _normalise_date(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        raise ValueError(f"ETF_V3_RECON_DATE_COLUMN_MISSING:{column}")
    values = pd.to_datetime(frame[column], utc=True, errors="coerce").dt.normalize()
    if values.isna().any():
        raise ValueError(f"ETF_V3_RECON_INVALID_DATE:{column}")
    return values


def _normalise_optional_date(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns, UTC]")
    return pd.to_datetime(frame[column], utc=True, errors="coerce").dt.normalize()


def _timestamp(value: Any) -> pd.Timestamp:
    result = pd.Timestamp(value)
    if result.tzinfo is None:
        result = result.tz_localize("UTC")
    else:
        result = result.tz_convert("UTC")
    return result.normalize()


def _iso(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    return _timestamp(value).isoformat()


def _filter_run(frame: pd.DataFrame, *, candidate_id: str, phase: str, cost_scenario: str, name: str) -> pd.DataFrame:
    if frame is None:
        return pd.DataFrame()
    result = frame.copy()
    filters = {
        "candidate_id": candidate_id,
        "phase": phase,
        "cost_scenario": cost_scenario,
    }
    for column, expected in filters.items():
        if column in result.columns:
            result = result[result[column].astype(str).eq(str(expected))]
        elif column == "candidate_id":
            # ``EngineResult`` ledgers are already scoped to one independent
            # account and intentionally omit repeated run labels.  Aggregate
            # study artifacts should carry the labels and are filtered above.
            # In either case, never mix multiple accounts in one call.
            continue
        elif column in {"phase", "cost_scenario"}:
            # Small hand-built fixtures often omit the optional run labels.
            # Once present, however, labels are always filtered strictly.
            continue
    return result.reset_index(drop=True)


def _normalise_raw_bars(raw_bars: pd.DataFrame) -> pd.DataFrame:
    frame = raw_bars.copy()
    if "date" not in frame.columns and "event_time" in frame.columns:
        frame = frame.rename(columns={"event_time": "date"})
    required = {"date", "symbol", "close"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"ETF_V3_RECON_RAW_COLUMNS_MISSING:{','.join(sorted(missing))}")
    frame["date"] = _normalise_date(frame, "date")
    frame["symbol"] = frame["symbol"].astype(str).str.upper()
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    if frame["close"].isna().any() or (frame["close"] <= 0).any():
        raise ValueError("ETF_V3_RECON_RAW_CLOSE_INVALID")
    if frame.duplicated(["date", "symbol"]).any():
        raise ValueError("ETF_V3_RECON_RAW_DUPLICATE_BAR")
    return frame.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)


def _microshares(value: Any, *, field: str) -> int:
    if value is None or pd.isna(value):
        raise ValueError(f"ETF_V3_RECON_{field.upper()}_MISSING")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"ETF_V3_RECON_{field.upper()}_INVALID") from exc
    scaled = decimal * _DECIMAL_MICROSHARES
    if scaled != scaled.to_integral_value():
        raise ValueError(f"ETF_V3_RECON_{field.upper()}_NOT_MICROSHARE_ALIGNED")
    result = int(scaled)
    if result < 0:
        raise ValueError(f"ETF_V3_RECON_{field.upper()}_NEGATIVE")
    return result


def _quantity_microshares(row: Mapping[str, Any], *, required: bool = True) -> int:
    for key in ("quantity_microshares", "filled_quantity_microshares", "microshares"):
        if key in row and row[key] is not None and not pd.isna(row[key]):
            value = row[key]
            try:
                result = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"ETF_V3_RECON_{key.upper()}_INVALID") from exc
            if result < 0:
                raise ValueError(f"ETF_V3_RECON_{key.upper()}_NEGATIVE")
            return result
    for key in ("quantity", "filled_quantity", "shares"):
        if key in row and row[key] is not None and not pd.isna(row[key]):
            return _microshares(row[key], field=key)
    if required:
        raise ValueError("ETF_V3_RECON_QUANTITY_MISSING")
    return 0


def _action_type(row: Mapping[str, Any]) -> str:
    return str(row.get("action_type", row.get("type", ""))).lower()


def _action_factor(row: Mapping[str, Any]) -> float:
    for key in ("split_factor", "factor", "ratio"):
        value = row.get(key)
        if value is not None and not pd.isna(value):
            factor = float(value)
            if factor > 0:
                return factor
    old = row.get("old_rate", row.get("old_shares"))
    new = row.get("new_rate", row.get("new_shares"))
    try:
        factor = float(new) / float(old)
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise ValueError("ETF_V3_RECON_SPLIT_RATIO_INVALID") from exc
    if factor <= 0:
        raise ValueError("ETF_V3_RECON_SPLIT_RATIO_INVALID")
    return factor


def _action_rate(row: Mapping[str, Any]) -> float:
    for key in ("rate", "value", "amount", "distribution"):
        value = row.get(key)
        if value is not None and not pd.isna(value):
            rate = float(value)
            if rate >= 0:
                return rate
    raise ValueError("ETF_V3_RECON_DIVIDEND_RATE_INVALID")


def _load_resolutions(value: Mapping[str, Any] | Path | str | None) -> dict[str, Any]:
    if value is None:
        return {"resolutions": []}
    if isinstance(value, (str, Path)):
        return json.loads(Path(value).read_text(encoding="utf-8"))
    return dict(value)


def _normalise_actions(
    corporate_actions: pd.DataFrame,
    *,
    resolutions: Mapping[str, Any] | Path | str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, Any]]]:
    """Return resolved split/dividend actions and resolution diagnostics.

    Multiple dividend records on one symbol/ex-date are rejected unless an
    explicit, manifest-bound resolution removes the provider duplicate.  The
    checker never silently chooses the first row.
    """
    if corporate_actions is None or corporate_actions.empty:
        empty_split = pd.DataFrame(columns=["symbol", "date", "factor"])
        empty_dividend = pd.DataFrame(columns=["symbol", "date", "rate", "payable_date"])
        return empty_split, empty_dividend, []
    actions = corporate_actions.copy()
    if "symbol" not in actions.columns:
        raise ValueError("ETF_V3_RECON_ACTION_SYMBOL_MISSING")
    date_column = "ex_date" if "ex_date" in actions.columns else "date"
    actions["date"] = _normalise_date(actions, date_column)
    actions["symbol"] = actions["symbol"].astype(str).str.upper()
    resolutions_obj = _load_resolutions(resolutions)
    resolution_diagnostics: list[dict[str, Any]] = []
    for fix in resolutions_obj.get("resolutions", []):
        if not isinstance(fix, Mapping):
            raise ValueError("ETF_V3_RECON_ACTION_RESOLUTION_INVALID")
        if "exclude_id" not in fix or "retain_id" not in fix:
            raise ValueError("ETF_V3_RECON_ACTION_RESOLUTION_IDS_MISSING")
        if "id" not in actions.columns:
            raise ValueError("ETF_V3_RECON_ACTION_RESOLUTION_ID_COLUMN_MISSING")
        keep = actions[actions["id"].astype(str).eq(str(fix["retain_id"]))]
        reject = actions[actions["id"].astype(str).eq(str(fix["exclude_id"]))]
        if len(keep) != 1 or len(reject) != 1:
            raise ValueError("ETF_V3_RECON_ACTION_RESOLUTION_ROW_MISMATCH")
        if "rate" in fix and abs(_action_rate(keep.iloc[0].to_dict()) - float(fix["rate"])) > 1e-12:
            raise ValueError("ETF_V3_RECON_ACTION_RESOLUTION_RATE_MISMATCH")
        if "verified_payable_date" in fix:
            payable = _normalise_optional_date(keep, "payable_date").iloc[0]
            if _iso(payable) != _iso(fix["verified_payable_date"]):
                raise ValueError("ETF_V3_RECON_ACTION_RESOLUTION_PAYABLE_MISMATCH")
        actions = actions[~actions["id"].astype(str).eq(str(fix["exclude_id"]))].copy()
        resolution_diagnostics.append({"exclude_id": str(fix["exclude_id"]), "retain_id": str(fix["retain_id"])})

    split_rows: list[dict[str, Any]] = []
    dividend_rows: list[dict[str, Any]] = []
    for row in actions.to_dict("records"):
        kind = _action_type(row)
        if "split" in kind:
            split_rows.append({"symbol": row["symbol"], "date": row["date"], "factor": _action_factor(row)})
        elif "dividend" in kind:
            payable = _normalise_optional_date(pd.DataFrame([row]), "payable_date").iloc[0]
            if pd.isna(payable):
                raise ValueError("ETF_V3_RECON_DIVIDEND_PAYABLE_DATE_MISSING")
            dividend_rows.append({"symbol": row["symbol"], "date": row["date"], "rate": _action_rate(row), "payable_date": payable})
    splits = pd.DataFrame(split_rows, columns=["symbol", "date", "factor"])
    if not splits.empty:
        splits = splits.groupby(["symbol", "date"], as_index=False, sort=True)["factor"].prod()
    dividends = pd.DataFrame(dividend_rows, columns=["symbol", "date", "rate", "payable_date"])
    if not dividends.empty:
        duplicate_groups = dividends.groupby(["symbol", "date"], sort=True)
        for (symbol, date_value), group in duplicate_groups:
            if len(group) > 1:
                raise ValueError(f"ETF_V3_RECON_MULTIPLE_DISTRIBUTIONS:{symbol}:{_iso(date_value)}")
    return splits, dividends, resolution_diagnostics


def _normalise_events(frame: pd.DataFrame | None, *, name: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        result = pd.DataFrame(columns=['date','kind','symbol','amount','settlement_date','payable_date'])
        result['date'] = pd.Series(dtype='datetime64[ns, UTC]')
        return result
    result = frame.copy()
    result["date"] = _normalise_date(result, "date")
    if "symbol" in result.columns:
        result["symbol"] = result["symbol"].astype(str).str.upper()
    if "kind" in result.columns:
        result["kind"] = result["kind"].astype(str).str.lower()
    if "event_type" in result.columns and "kind" not in result.columns:
        result["kind"] = result["event_type"].astype(str).str.lower()
    if "amount" in result.columns:
        result["amount"] = pd.to_numeric(result["amount"], errors="coerce")
        if result["amount"].isna().any():
            raise ValueError(f"ETF_V3_RECON_{name.upper()}_AMOUNT_INVALID")
    for column in ("settlement_date", "payable_date"):
        if column in result.columns:
            result[column] = _normalise_optional_date(result, column)
    return result.sort_values(["date"], kind="stable").reset_index(drop=True)


def _normalise_fills(frame: pd.DataFrame, *, candidate_id: str, phase: str, cost_scenario: str) -> pd.DataFrame:
    result = _filter_run(frame, candidate_id=candidate_id, phase=phase, cost_scenario=cost_scenario, name="fills")
    if result.empty:
        return result
    result["date"] = _normalise_date(result, "date")
    for column in ("symbol", "side"):
        if column not in result.columns:
            raise ValueError(f"ETF_V3_RECON_FILLS_{column.upper()}_MISSING")
    result["symbol"] = result["symbol"].astype(str).str.upper()
    result["side"] = result["side"].astype(str).str.lower()
    if (~result["side"].isin(["buy", "sell"])).any():
        raise ValueError("ETF_V3_RECON_FILL_SIDE_INVALID")
    if "price" not in result.columns:
        raise ValueError("ETF_V3_RECON_FILLS_PRICE_MISSING")
    result["quantity_microshares"] = [
        _quantity_microshares(row, required=True) for row in result.to_dict("records")
    ]
    if (result["quantity_microshares"] <= 0).any():
        raise ValueError("ETF_V3_RECON_FILL_QUANTITY_ZERO")
    result["price"] = pd.to_numeric(result["price"], errors="coerce")
    if result["price"].isna().any() or (result["price"] <= 0).any():
        raise ValueError("ETF_V3_RECON_FILL_PRICE_INVALID")
    if "fee" in result.columns:
        result["fee"] = pd.to_numeric(result["fee"], errors="coerce").fillna(0.0)
    else:
        result["fee"] = 0.0
    if (result["fee"] < 0).any():
        raise ValueError("ETF_V3_RECON_FILL_FEE_NEGATIVE")
    result["settlement_date"] = _normalise_optional_date(result, "settlement_date")
    return result.sort_values(["date"], kind="stable").reset_index(drop=True)


def _numeric_amount(row: Mapping[str, Any]) -> float:
    if "amount" in row and row["amount"] is not None and not pd.isna(row["amount"]):
        return float(row["amount"])
    for key in ("cash_delta", "cash_settled_delta", "cash_pending_delta"):
        if key in row and row[key] is not None and not pd.isna(row[key]):
            return float(row[key])
    return 0.0


def _cash_delta(kind: str, amount: float, row: Mapping[str, Any]) -> tuple[float, float]:
    """Return (settled delta, pending delta) for one explicit cash event."""
    if "cash_settled_delta" in row and not pd.isna(row["cash_settled_delta"]):
        settled = float(row["cash_settled_delta"])
    else:
        settled = 0.0
    if "cash_pending_delta" in row and not pd.isna(row["cash_pending_delta"]):
        pending = float(row["cash_pending_delta"])
    else:
        pending = 0.0
    if "cash_settled_delta" in row or "cash_pending_delta" in row:
        return settled, pending
    if kind in {"buy", "purchase", "cash_settled"}:
        return amount, 0.0
    if kind in {"sale_pending", "sell_pending", "dividend_receivable", "cash_pending"}:
        return 0.0, amount
    if kind in {"sale_settled", "sell_settled", "settlement_release", "dividend_paid", "dividend_settled", "dividend_release", "cash_pending_release"}:
        return amount, -amount
    if kind in {"fee", "commission"}:
        return amount, 0.0
    if kind in {"split", "internal_transfer", "transfer", "rebalance"}:
        return 0.0, 0.0
    raise ValueError(f"ETF_V3_RECON_CASH_KIND_UNKNOWN:{kind}")


def _empty_dividend_checks() -> pd.DataFrame:
    return pd.DataFrame(columns=["symbol", "ex_date", "expected_quantity_microshares", "rate", "expected_amount", "ledger_amount", "payable_date", "status"])


def _empty_settlement_checks() -> pd.DataFrame:
    return pd.DataFrame(columns=["symbol", "trade_date", "settlement_date", "expected_amount", "ledger_amount", "status"])


def _empty_fee_checks() -> pd.DataFrame:
    return pd.DataFrame(columns=["order_id", "date", "symbol", "side", "expected_cash_amount", "ledger_cash_amount", "fee", "status"])


def _empty_component_checks() -> pd.DataFrame:
    return pd.DataFrame(columns=["date", "symbol", "physical_quantity_microshares", "component_quantity_microshares", "difference_microshares", "status"])


def reconstruct_daily_equity_v3(
    fills: pd.DataFrame,
    cash_ledger: pd.DataFrame,
    raw_bars: pd.DataFrame,
    corporate_actions: pd.DataFrame,
    *,
    candidate_id: str,
    valuation_dates: Iterable[Any],
    phase: str = "continuous",
    cost_scenario: str = "base",
    component_ledger: pd.DataFrame | None = None,
    settlement_events: pd.DataFrame | None = None,
    action_resolutions: Mapping[str, Any] | Path | str | None = None,
    initial_cash: float = DEFAULT_INITIAL_CASH,
    amount_tolerance: float = 1e-7,
) -> ReconstructionResult:
    """Rebuild daily equity from immutable event inputs.

    No saved equity, daily profit, or engine metric is read here.  The
    ``valuation_dates`` argument only controls which rebuilt marks are
    returned; fills and cash events on intervening sessions are still applied.
    """
    if initial_cash <= 0:
        raise ValueError("ETF_V3_RECON_INITIAL_CASH_INVALID")
    dates = pd.DatetimeIndex(pd.to_datetime(list(valuation_dates), utc=True, errors="coerce")).normalize()
    if len(dates) == 0 or dates.isna().any():
        raise ValueError("ETF_V3_RECON_VALUATION_DATES_INVALID")
    dates = dates.drop_duplicates().sort_values()
    fills_run = _normalise_fills(fills, candidate_id=candidate_id, phase=phase, cost_scenario=cost_scenario)
    ledger_run = _filter_run(cash_ledger, candidate_id=candidate_id, phase=phase, cost_scenario=cost_scenario, name="cash_ledger")
    ledger_run = _normalise_events(ledger_run, name="cash_ledger")
    settlement_run = _filter_run(settlement_events, candidate_id=candidate_id, phase=phase, cost_scenario=cost_scenario, name="settlement_events") if settlement_events is not None else pd.DataFrame()
    settlement_run = _normalise_events(settlement_run, name="settlement_events")
    if not settlement_run.empty:
        ledger_run = pd.concat([ledger_run, settlement_run], ignore_index=True, sort=False)
        ledger_run = ledger_run.sort_values("date", kind="stable").reset_index(drop=True)
    components_run = _filter_run(component_ledger, candidate_id=candidate_id, phase=phase, cost_scenario=cost_scenario, name="component_ledger") if component_ledger is not None else pd.DataFrame()
    components_run = _normalise_events(components_run, name="component_ledger")
    raw = _normalise_raw_bars(raw_bars)
    raw_lookup = {(row.date, row.symbol): float(row.close) for row in raw.itertuples(index=False)}
    splits, dividends, resolution_diagnostics = _normalise_actions(corporate_actions, resolutions=action_resolutions)
    split_lookup = {(row.symbol, row.date): float(row.factor) for row in splits.itertuples(index=False)}
    dividend_lookup: dict[tuple[str, pd.Timestamp], dict[str, Any]] = {
        (row.symbol, row.date): row._asdict() for row in dividends.itertuples(index=False)
    }

    # Process all event dates through the final valuation date.  This keeps
    # settlement and payable-date events correct even when a caller requests a
    # sparse set of output marks.
    max_date = max(dates)
    event_dates = set(dates)
    event_dates.update(pd.Timestamp(value) for value in fills_run.loc[fills_run["date"] <= max_date, "date"].tolist())
    event_dates.update(pd.Timestamp(value) for value in ledger_run.loc[ledger_run["date"] <= max_date, "date"].tolist())
    event_dates.update(pd.Timestamp(value) for value in dividends.loc[dividends["date"] <= max_date, "date"].tolist())
    event_dates.update(pd.Timestamp(value) for value in splits.loc[splits["date"] <= max_date, "date"].tolist())
    event_dates = sorted(value for value in event_dates if value <= max_date)

    symbols = sorted(set(fills_run["symbol"].tolist()) | set(raw["symbol"].tolist()))
    holdings: dict[str, int] = {symbol: 0 for symbol in symbols}
    cash_settled = float(initial_cash)
    cash_pending = 0.0
    daily_rows: list[dict[str, Any]] = []
    dividend_rows: list[dict[str, Any]] = []
    settlement_rows: list[dict[str, Any]] = []
    fee_rows: list[dict[str, Any]] = []
    component_rows: list[dict[str, Any]] = []
    snapshot_mismatches: list[dict[str, Any]] = []
    negative_events: list[dict[str, Any]] = []
    missing_marks: list[dict[str, Any]] = []
    split_event_count = 0

    component_holdings: dict[tuple[str, str], int] = {}
    component_snapshot_columns = {"quantity_microshares", "quantity", "shares", "delta_microshares", "delta_quantity", "quantity_delta"}

    for date_value in event_dates:
        # Corporate actions are effective before the day's entitlement and
        # before same-session fills.  A rational split ratio must preserve the
        # microshare representation exactly.
        for symbol in list(holdings):
            factor = split_lookup.get((symbol, date_value), 1.0)
            if factor != 1.0:
                scaled = Decimal(holdings[symbol]) * Decimal(str(factor))
                if scaled != scaled.to_integral_value():
                    raise ValueError("ETF_V3_RECON_SPLIT_MICROSHARE_ROUNDING")
                holdings[symbol] = int(scaled)
                split_event_count += 1
                for (component, item), quantity in list(component_holdings.items()):
                    if item != symbol:
                        continue
                    component_scaled = Decimal(quantity) * Decimal(str(factor))
                    if component_scaled != component_scaled.to_integral_value():
                        raise ValueError("ETF_V3_RECON_COMPONENT_SPLIT_MICROSHARE_ROUNDING")
                    component_holdings[(component, item)] = int(component_scaled)

        ledger_day = ledger_run[ledger_run["date"].eq(date_value)]
        fills_day = fills_run[fills_run["date"].eq(date_value)]
        component_day = components_run[components_run["date"].eq(date_value)] if not components_run.empty else pd.DataFrame()

        # Internal component transfers are ownership bookkeeping.  They do
        # not change physical quantity, cash, fees, or external turnover.
        for row in component_day.to_dict("records"):
            if str(row.get("kind", "")).lower() != "internal_transfer":
                continue
            symbol = str(row.get("symbol", "")).upper()
            quantity = _quantity_microshares(row, required=True)
            source = str(row.get("from_component", ""))
            target = str(row.get("to_component", ""))
            if not source or not target or not symbol:
                raise ValueError("ETF_V3_RECON_INTERNAL_TRANSFER_FIELDS_MISSING")
            source_key = (source, symbol)
            target_key = (target, symbol)
            component_holdings[source_key] = component_holdings.get(source_key, 0) - quantity
            component_holdings[target_key] = component_holdings.get(target_key, 0) + quantity
            if component_holdings[source_key] < 0:
                negative_events.append({"date": _iso(date_value), "kind": "component", "component_id": source, "symbol": symbol, "microshares": component_holdings[source_key]})

        # Dividend entitlement is checked before fills, so an ex-date opening
        # purchase receives no distribution.
        for (symbol, ex_date), action in dividend_lookup.items():
            if ex_date != date_value:
                continue
            expected_quantity = holdings.get(symbol, 0)
            expected_amount = expected_quantity / MICROSHARES * float(action["rate"])
            rows = ledger_day[(ledger_day.get("kind", pd.Series(index=ledger_day.index, dtype=str)).eq("dividend_receivable")) & ledger_day.get("symbol", pd.Series(index=ledger_day.index, dtype=str)).astype(str).str.upper().eq(symbol)]
            actual_amount = float(rows["amount"].sum()) if not rows.empty and "amount" in rows else 0.0
            expected_payable = action["payable_date"]
            actual_payables = set(rows["payable_date"].dropna().tolist()) if not rows.empty and "payable_date" in rows else set()
            if abs(actual_amount - expected_amount) > amount_tolerance:
                status = "AMOUNT_MISMATCH"
            elif expected_amount > amount_tolerance and rows.empty:
                status = "MISSING_LEDGER_ENTITLEMENT"
            elif actual_payables and expected_payable not in actual_payables:
                status = "PAYABLE_MISMATCH"
            else:
                status = "MATCHED"
            dividend_rows.append({
                "symbol": symbol,
                "ex_date": _iso(ex_date),
                "expected_quantity_microshares": expected_quantity,
                "rate": float(action["rate"]),
                "expected_amount": expected_amount,
                "ledger_amount": actual_amount,
                "payable_date": _iso(expected_payable),
                "status": status,
            })

        # Apply cash events independently of fills.  Ledger events are the
        # explicit source of settlement and payable-date cash availability.
        settled_delta = 0.0
        pending_delta = 0.0
        for row in ledger_day.to_dict("records"):
            kind = str(row.get("kind", "")).lower()
            amount = _numeric_amount(row)
            delta_settled, delta_pending = _cash_delta(kind, amount, row)
            settled_delta += delta_settled
            pending_delta += delta_pending
        cash_settled += settled_delta
        cash_pending += pending_delta
        if cash_settled < -amount_tolerance or cash_pending < -amount_tolerance:
            negative_events.append({"date": _iso(date_value), "kind": "cash", "settled": cash_settled, "pending": cash_pending})

        # Fills affect physical ownership after ex-date entitlement and after
        # effective splits, then are marked at that day's raw close.
        for row in fills_day.to_dict("records"):
            symbol = str(row["symbol"]).upper()
            holdings.setdefault(symbol, 0)
            quantity = int(row["quantity_microshares"])
            if row["side"] == "buy":
                holdings[symbol] += quantity
            else:
                holdings[symbol] -= quantity
                if holdings[symbol] < 0:
                    negative_events.append({"date": _iso(date_value), "kind": "holding", "symbol": symbol, "microshares": holdings[symbol]})
            if "component_id" in row and row.get("component_id") is not None and not pd.isna(row.get("component_id")):
                component_key = (str(row["component_id"]), symbol)
                component_holdings[component_key] = component_holdings.get(component_key, 0) + (quantity if row["side"] == "buy" else -quantity)
                if component_holdings[component_key] < 0:
                    negative_events.append({"date": _iso(date_value), "kind": "component", "component_id": component_key[0], "symbol": symbol, "microshares": component_holdings[component_key]})

        # Some engine versions emit explicit component position snapshots.
        # Apply those after same-session fills; event rows such as
        # ``realized_partial`` are attribution records and are intentionally
        # excluded from the snapshot path.
        for row in component_day.to_dict("records"):
            kind = str(row.get("kind", "")).lower()
            if kind in {"physical_position", "internal_transfer", "realized_partial", "realized", "attribution"}:
                continue
            if "component_id" not in row or pd.isna(row.get("component_id")) or "symbol" not in row or pd.isna(row.get("symbol")):
                continue
            if not (component_snapshot_columns & set(row)):
                continue
            component = str(row["component_id"])
            symbol = str(row["symbol"]).upper()
            if any(key in row and row[key] is not None and not pd.isna(row[key]) for key in ("quantity_microshares", "quantity", "shares")):
                observed_quantity = _quantity_microshares(row, required=True)
                if kind == "component_position":
                    expected_quantity = component_holdings.get((component,symbol),0)
                    if observed_quantity != expected_quantity:
                        snapshot_mismatches.append({"date":_iso(date_value),"symbol":symbol,"component_id":component,"expected":expected_quantity,"observed":observed_quantity})
                else:
                    component_holdings[(component, symbol)] = observed_quantity
            else:
                delta_key = next(key for key in ("delta_microshares", "delta_quantity", "quantity_delta") if key in row and row[key] is not None and not pd.isna(row[key]))
                delta = int(row[delta_key]) if delta_key == "delta_microshares" else _microshares(row[delta_key], field=delta_key)
                component_holdings[(component, symbol)] = component_holdings.get((component, symbol), 0) + delta

        # Match explicit settlement release amounts to sale fills, including
        # aggregated releases where several fills settle together.
        for settlement_date, group in fills_run[fills_run["side"].eq("sell")].groupby("settlement_date", dropna=True):
            if pd.isna(settlement_date) or _timestamp(settlement_date) != date_value:
                continue
            for symbol, symbol_group in group.groupby("symbol"):
                expected_amount = float(sum((int(row.quantity_microshares) / MICROSHARES) * float(row.price) - float(row.fee) for row in symbol_group.itertuples(index=False)))
                rows = ledger_day[(ledger_day.get("kind", pd.Series(index=ledger_day.index, dtype=str)).isin(["sale_settled", "sell_settled", "settlement_release"])) & ledger_day.get("symbol", pd.Series(index=ledger_day.index, dtype=str)).astype(str).str.upper().eq(symbol)]
                actual_amount = float(rows["amount"].sum()) if not rows.empty and "amount" in rows else 0.0
                settlement_rows.append({"symbol": symbol, "trade_date": _iso(symbol_group["date"].min()), "settlement_date": _iso(date_value), "expected_amount": expected_amount, "ledger_amount": actual_amount, "status": "MATCHED" if abs(expected_amount - actual_amount) <= amount_tolerance else "RELEASE_MISMATCH"})

        # Reconcile every fill's gross cash impact and fee.  Matching by
        # order_id is preferred; date/symbol/kind is the migration fallback.
        for row in fills_day.to_dict("records"):
            quantity = int(row["quantity_microshares"]) / MICROSHARES
            gross = quantity * float(row["price"])
            fee = float(row["fee"])
            expected = -(gross + fee) if row["side"] == "buy" else gross - fee
            if row["side"] == "buy":
                kinds = ["buy", "purchase"]
            else:
                kinds = ["sale_pending", "sell_pending"]
            candidate_rows = ledger_day[ledger_day.get("kind", pd.Series(index=ledger_day.index, dtype=str)).isin(kinds)]
            if "order_id" in row and "order_id" in ledger_day.columns and pd.notna(row["order_id"]):
                candidate_rows = candidate_rows[candidate_rows["order_id"].astype(str).eq(str(row["order_id"]))]
            elif "symbol" in ledger_day.columns:
                candidate_rows = candidate_rows[candidate_rows["symbol"].astype(str).str.upper().eq(str(row["symbol"]).upper())]
            observed = float(candidate_rows["amount"].sum()) if not candidate_rows.empty and "amount" in candidate_rows else 0.0
            status = "MATCHED" if abs(expected - observed) <= amount_tolerance else "CASH_OR_FEE_MISMATCH"
            fee_rows.append({"order_id": str(row.get("order_id", "")), "date": _iso(date_value), "symbol": row["symbol"], "side": row["side"], "expected_cash_amount": expected, "ledger_cash_amount": observed, "fee": fee, "status": status})

        if date_value not in set(dates):
            continue
        holdings_value = 0.0
        for symbol, quantity in sorted(holdings.items()):
            if quantity == 0:
                continue
            close = raw_lookup.get((date_value, symbol))
            if close is None:
                missing_marks.append({"date": _iso(date_value), "symbol": symbol, "microshares": quantity})
                continue
            holdings_value += quantity / MICROSHARES * close
        components = {
            symbol: {
                component: quantity
                for (component, item), quantity in sorted(component_holdings.items())
                if item == symbol and quantity != 0
            }
            for symbol in sorted(holdings)
        }
        daily_row: dict[str, Any] = {
            "date": _iso(date_value),
            "candidate_id": candidate_id,
            "phase": phase,
            "cost_scenario": cost_scenario,
            "cash_settled_reconstructed": cash_settled,
            "cash_pending_reconstructed": cash_pending,
            "holdings_value_reconstructed": holdings_value,
            "equity_reconstructed": cash_settled + cash_pending + holdings_value,
        }
        for symbol, quantity in sorted(holdings.items()):
            daily_row[f"holding_{symbol}_microshares"] = int(quantity)
            daily_row[f"holding_{symbol}"] = quantity / MICROSHARES
        daily_rows.append(daily_row)

        if components_run.empty:
            continue
        # Component sums must equal the physical quantity exactly, not merely
        # after rounding to displayed shares.
        for symbol, physical in sorted(holdings.items()):
            by_component = components.get(symbol, {})
            if not by_component:
                status = "MATCHED" if physical == 0 else "COMPONENT_STATE_MISSING"
                component_rows.append({"date": _iso(date_value), "symbol": symbol, "physical_quantity_microshares": physical, "component_quantity_microshares": 0, "difference_microshares": physical, "status": status})
                continue
            total = sum(by_component.values())
            component_rows.append({"date": _iso(date_value), "symbol": symbol, "physical_quantity_microshares": physical, "component_quantity_microshares": total, "difference_microshares": total - physical, "status": "MATCHED" if total == physical else "COMPONENT_QUANTITY_MISMATCH"})

    daily = pd.DataFrame(daily_rows)
    dividends_frame = pd.DataFrame(dividend_rows) if dividend_rows else _empty_dividend_checks()
    settlements_frame = pd.DataFrame(settlement_rows) if settlement_rows else _empty_settlement_checks()
    fees_frame = pd.DataFrame(fee_rows) if fee_rows else _empty_fee_checks()
    components_frame = pd.DataFrame(component_rows) if component_rows else _empty_component_checks()
    summary = {
        "schema_version": "etf-cash-v3-independent-reconstruction/v1",
        "candidate_id": candidate_id,
        "phase": phase,
        "cost_scenario": cost_scenario,
        "rows": int(len(daily)),
        "date_start": daily["date"].iloc[0] if not daily.empty else None,
        "date_end": daily["date"].iloc[-1] if not daily.empty else None,
        "symbols": sorted(holdings),
        "split_event_count": int(split_event_count),
        "action_resolution_count": int(len(resolution_diagnostics)),
        "missing_marks": int(len(missing_marks)),
        "negative_holding_or_cash_events": int(len(negative_events)),
        "dividend_check_count": int(len(dividends_frame)),
        "dividend_mismatch_count": int((~dividends_frame["status"].eq("MATCHED")).sum()) if not dividends_frame.empty else 0,
        "settlement_check_count": int(len(settlements_frame)),
        "settlement_mismatch_count": int((~settlements_frame["status"].eq("MATCHED")).sum()) if not settlements_frame.empty else 0,
        "fee_check_count": int(len(fees_frame)),
        "fee_mismatch_count": int((~fees_frame["status"].eq("MATCHED")).sum()) if not fees_frame.empty else 0,
        "component_check_count": int(len(components_frame)),
        "component_mismatch_count": (int((~components_frame["status"].eq("MATCHED")).sum()) if not components_frame.empty else 0) + len(snapshot_mismatches),
        "component_snapshot_mismatches": snapshot_mismatches[:10],
        "missing_mark_samples": missing_marks[:10],
        "negative_holding_or_cash_samples": negative_events[:10],
    }
    return ReconstructionResult(daily, dividends_frame, settlements_frame, fees_frame, components_frame, summary)


def reconstruct_v3(*args: Any, **kwargs: Any) -> ReconstructionResult:
    """Short alias used by orchestration code."""
    return reconstruct_daily_equity_v3(*args, **kwargs)


def compare_saved_equity(
    reconstructed: pd.DataFrame,
    saved_equity: pd.DataFrame,
    *,
    tolerance: float = DEFAULT_TOLERANCE,
) -> dict[str, Any]:
    """Compare a saved engine curve after independent reconstruction.

    The saved daily-profit field is intentionally ignored.  This function is
    never called by the reconstruction itself, so a corrupt saved equity file
    cannot influence the rebuilt values.
    """
    if "date" not in saved_equity.columns:
        raise ValueError("ETF_V3_RECON_SAVED_EQUITY_DATE_MISSING")
    equity_column = "equity" if "equity" in saved_equity.columns else "equity_reconstructed"
    if equity_column not in saved_equity.columns:
        raise ValueError("ETF_V3_RECON_SAVED_EQUITY_COLUMN_MISSING")
    rebuilt_column = "equity_reconstructed" if "equity_reconstructed" in reconstructed.columns else "equity"
    if rebuilt_column not in reconstructed.columns:
        raise ValueError("ETF_V3_RECON_REBUILT_EQUITY_COLUMN_MISSING")
    saved = saved_equity.copy()
    rebuilt = reconstructed.copy()
    saved["date"] = _normalise_date(saved, "date")
    rebuilt["date"] = _normalise_date(rebuilt, "date")
    if saved["date"].duplicated().any() or rebuilt["date"].duplicated().any():
        raise ValueError("ETF_V3_RECON_EQUITY_DUPLICATE_DATE")
    joined = saved[["date", equity_column]].merge(rebuilt[["date", rebuilt_column]], on="date", how="outer", indicator=True)
    difference = pd.to_numeric(joined[equity_column], errors="coerce") - pd.to_numeric(joined[rebuilt_column], errors="coerce")
    matched = joined["_merge"].eq("both")
    max_abs = float(difference[matched].abs().max()) if matched.any() else None
    return {
        "saved_rows": int(len(saved)),
        "reconstructed_rows": int(len(rebuilt)),
        "matched_rows": int(matched.sum()),
        "missing_saved_rows": int(joined["_merge"].eq("right_only").sum()),
        "missing_reconstructed_rows": int(joined["_merge"].eq("left_only").sum()),
        "max_abs_equity_difference": max_abs,
        "tolerance": float(tolerance),
        "equity_within_tolerance": bool(max_abs is not None and max_abs <= tolerance and matched.all()),
        "daily_profit_used": False,
    }


__all__ = [
    "DEFAULT_INITIAL_CASH",
    "DEFAULT_TOLERANCE",
    "MICROSHARES",
    "ReconstructionResult",
    "compare_saved_equity",
    "reconstruct_daily_equity_v3",
    "reconstruct_v3",
]
