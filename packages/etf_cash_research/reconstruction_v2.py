"""Independent reconstruction audit for saved v2 cash-account runs.

This module intentionally sits beside the simulator rather than calling it.
It rebuilds positions from fills, cash state from the cash ledger, and marks
from raw closes.  It is a diagnostic comparison against saved equity artifacts;
it does not certify the simulator or any strategy result.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from packages.research_data.artifacts import atomic_json, file_hash

DEFAULT_INITIAL_CASH = 1000.0
DEFAULT_TOLERANCE = 0.01
_SETTLED_KINDS = frozenset({"buy", "sale_settled", "dividend_paid"})
_PENDING_ADD_KINDS = frozenset({"sale_pending", "dividend_receivable"})
_PENDING_REMOVE_KINDS = frozenset({"sale_settled", "dividend_paid"})


@dataclass(frozen=True)
class ReconstructionResult:
    """Independent account reconstruction and action-entitlement checks."""

    daily: pd.DataFrame
    dividend_checks: pd.DataFrame
    summary: dict[str, Any]


def _normalise_date(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        raise ValueError(f"ETF_RECONSTRUCTION_DATE_COLUMN_MISSING:{column}")
    values = pd.to_datetime(frame[column], utc=True, errors="coerce").dt.normalize()
    if values.isna().any():
        raise ValueError(f"ETF_RECONSTRUCTION_INVALID_DATE:{column}")
    return values


def _safe_iso(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.normalize().isoformat()


def _filter_run(frame: pd.DataFrame, *, candidate_id: str, phase: str, cost_scenario: str) -> pd.DataFrame:
    required = {"candidate_id", "phase", "cost_scenario"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"ETF_RECONSTRUCTION_RUN_COLUMNS_MISSING:{','.join(sorted(missing))}")
    mask = (
        frame["candidate_id"].astype(str).eq(candidate_id)
        & frame["phase"].astype(str).eq(phase)
        & frame["cost_scenario"].astype(str).eq(cost_scenario)
    )
    return frame.loc[mask].copy()


def _normalise_raw_bars(raw_bars: pd.DataFrame) -> pd.DataFrame:
    frame = raw_bars.copy()
    if "date" not in frame and "event_time" in frame:
        frame = frame.rename(columns={"event_time": "date"})
    required = {"date", "symbol", "close"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"ETF_RECONSTRUCTION_RAW_COLUMNS_MISSING:{','.join(sorted(missing))}")
    frame["date"] = _normalise_date(frame, "date")
    frame["symbol"] = frame["symbol"].astype(str).str.upper()
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    if frame["close"].isna().any() or (frame["close"] <= 0).any():
        raise ValueError("ETF_RECONSTRUCTION_RAW_CLOSE_INVALID")
    if frame.duplicated(["date", "symbol"]).any():
        raise ValueError("ETF_RECONSTRUCTION_RAW_DUPLICATE_BAR")
    return frame.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)


def _action_date(frame: pd.DataFrame) -> pd.Series:
    column = "ex_date" if "ex_date" in frame else "date"
    return _normalise_date(frame, column)


def _normalise_actions(corporate_actions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, Any]]]:
    """Return split rows, dividend rows and invalid action diagnostics."""
    if corporate_actions is None or corporate_actions.empty:
        empty = pd.DataFrame(columns=["symbol", "date", "factor"])
        return empty, pd.DataFrame(columns=["symbol", "date", "rate", "payable_date"]), []
    actions = corporate_actions.copy()
    if "symbol" not in actions:
        raise ValueError("ETF_RECONSTRUCTION_ACTION_SYMBOL_MISSING")
    actions["symbol"] = actions["symbol"].astype(str).str.upper()
    actions["date"] = _action_date(actions)
    action_type = actions.get("action_type", pd.Series("", index=actions.index)).astype(str).str.lower()
    invalid: list[dict[str, Any]] = []

    split_mask = action_type.str.contains("split", na=False)
    splits = actions.loc[split_mask, ["symbol", "date"]].copy()
    if not splits.empty:
        if "split_factor" in actions:
            factors = pd.to_numeric(actions.loc[split_mask, "split_factor"], errors="coerce")
        else:
            new_rate = pd.to_numeric(actions.loc[split_mask].get("new_rate"), errors="coerce")
            old_rate = pd.to_numeric(actions.loc[split_mask].get("old_rate"), errors="coerce")
            factors = new_rate.div(old_rate.replace(0.0, pd.NA))
        splits["factor"] = factors.to_numpy()
        for row in splits[splits["factor"].isna() | (splits["factor"] <= 0)].to_dict("records"):
            invalid.append({"kind": "split", "symbol": row["symbol"], "date": _safe_iso(row["date"]), "reason": "invalid_factor"})
        splits = splits[splits["factor"].notna() & (splits["factor"] > 0)]
        splits = splits.groupby(["symbol", "date"], as_index=False, sort=True)["factor"].prod()

    dividend_mask = action_type.str.contains("dividend", na=False)
    dividends = actions.loc[dividend_mask, ["symbol", "date"]].copy()
    if not dividends.empty:
        rate_column = next((column for column in ("rate", "value", "amount") if column in actions), None)
        dividends["rate"] = pd.to_numeric(actions.loc[dividend_mask, rate_column], errors="coerce") if rate_column else float("nan")
        payable = actions.loc[dividend_mask, "payable_date"] if "payable_date" in actions else pd.Series(pd.NaT, index=dividends.index)
        dividends["payable_date"] = pd.to_datetime(payable, utc=True, errors="coerce").dt.normalize()
        for row in dividends[dividends["rate"].isna() | (dividends["rate"] < 0)].to_dict("records"):
            invalid.append({"kind": "dividend", "symbol": row["symbol"], "date": _safe_iso(row["date"]), "reason": "invalid_rate"})
        dividends = dividends[dividends["rate"].notna() & (dividends["rate"] >= 0)].copy()
        # Multiple dividend records on one ex-date are economically additive.
        # Payable dates must agree; a disagreement remains visible as invalid.
        payable_counts = dividends.groupby(["symbol", "date"])["payable_date"].nunique(dropna=False)
        for (symbol, date_value), count in payable_counts.items():
            if int(count) > 1:
                invalid.append({"kind": "dividend", "symbol": symbol, "date": _safe_iso(date_value), "reason": "multiple_payable_dates"})
        dividends = dividends.groupby(["symbol", "date"], as_index=False, sort=True).agg(
            rate=("rate", "sum"),
            payable_date=("payable_date", "first"),
        )
    return splits, dividends, invalid


def _empty_checks() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "candidate_id",
            "date",
            "symbol",
            "expected_quantity",
            "rate",
            "expected_amount",
            "ledger_amount",
            "expected_payable_date",
            "ledger_payable_date",
            "amount_difference",
            "status",
        ]
    )


def _ledger_cash_delta(ledger: pd.DataFrame, date_value: pd.Timestamp) -> tuple[float, float, float]:
    rows = ledger[ledger["date"].eq(date_value)]
    settled = 0.0
    pending_delta = 0.0
    released = 0.0
    for row in rows.itertuples(index=False):
        kind = str(getattr(row, "kind", ""))
        amount = float(getattr(row, "amount", 0.0) or 0.0)
        if kind in _SETTLED_KINDS:
            settled += amount
            if kind in _PENDING_REMOVE_KINDS:
                pending_delta -= amount
                released += amount
        elif kind in _PENDING_ADD_KINDS:
            pending_delta += amount
    return settled, pending_delta, released


def reconstruct_daily_equity(
    fills: pd.DataFrame,
    cash_ledger: pd.DataFrame,
    raw_bars: pd.DataFrame,
    corporate_actions: pd.DataFrame,
    *,
    candidate_id: str,
    valuation_dates: Iterable[Any],
    phase: str = "continuous",
    cost_scenario: str = "base",
    initial_cash: float = DEFAULT_INITIAL_CASH,
    amount_tolerance: float = 1e-7,
) -> ReconstructionResult:
    """Rebuild one account without reading saved equity or daily profit.

    ``valuation_dates`` controls which sessions are marked; the values of any
    saved equity artifact are never used.  A position is split-adjusted before
    that date's fills, matching the independent economic ordering of the
    action and fill ledgers.  Cash is derived only from ledger events.
    """
    if initial_cash <= 0:
        raise ValueError("ETF_RECONSTRUCTION_INITIAL_CASH_INVALID")
    dates = pd.DatetimeIndex(pd.to_datetime(list(valuation_dates), utc=True, errors="coerce")).normalize().drop_duplicates().sort_values()
    if len(dates) == 0 or dates.isna().any():
        raise ValueError("ETF_RECONSTRUCTION_VALUATION_DATES_INVALID")
    fills_run = _filter_run(fills, candidate_id=candidate_id, phase=phase, cost_scenario=cost_scenario)
    ledger_run = _filter_run(cash_ledger, candidate_id=candidate_id, phase=phase, cost_scenario=cost_scenario)
    for frame, name in ((fills_run, "fills"), (ledger_run, "cash_ledger")):
        if "date" not in frame:
            raise ValueError(f"ETF_RECONSTRUCTION_{name.upper()}_DATE_MISSING")
        frame["date"] = _normalise_date(frame, "date")
    fills_run["symbol"] = fills_run.get("symbol", pd.Series(dtype=str)).astype(str).str.upper()
    fills_run["side"] = fills_run.get("side", pd.Series(dtype=str)).astype(str).str.lower()
    fills_run["quantity"] = pd.to_numeric(fills_run.get("quantity", 0.0), errors="coerce")
    if fills_run["quantity"].isna().any() or (fills_run["quantity"] < 0).any():
        raise ValueError("ETF_RECONSTRUCTION_FILL_QUANTITY_INVALID")
    ledger_run["kind"] = ledger_run.get("kind", pd.Series(dtype=str)).astype(str).str.lower()
    ledger_run["amount"] = pd.to_numeric(ledger_run.get("amount", 0.0), errors="coerce")
    if ledger_run["amount"].isna().any():
        raise ValueError("ETF_RECONSTRUCTION_LEDGER_AMOUNT_INVALID")
    if "payable_date" in ledger_run:
        ledger_run["payable_date"] = pd.to_datetime(ledger_run["payable_date"], utc=True, errors="coerce").dt.normalize()

    raw = _normalise_raw_bars(raw_bars)
    raw_lookup = raw.set_index(["date", "symbol"])["close"].to_dict()
    splits, dividends, invalid_actions = _normalise_actions(corporate_actions)
    split_lookup = {(row.symbol, row.date): float(row.factor) for row in splits.itertuples(index=False)}
    dividends_by_date: dict[pd.Timestamp, list[dict[str, Any]]] = {}
    for row in dividends.to_dict("records"):
        dividends_by_date.setdefault(pd.Timestamp(row["date"]), []).append(row)

    symbols = sorted(set(fills_run.loc[fills_run["symbol"].ne("NAN"), "symbol"].tolist()))
    holdings = {symbol: 0.0 for symbol in symbols}
    cash_settled = float(initial_cash)
    cash_pending = 0.0
    daily_rows: list[dict[str, Any]] = []
    dividend_rows: list[dict[str, Any]] = []
    missing_marks: list[dict[str, Any]] = []
    negative_holding_events: list[dict[str, Any]] = []
    split_event_count = 0

    for date_value in dates:
        date_value = pd.Timestamp(date_value)
        # Splits apply to positions held before the session's fills.
        for symbol in symbols:
            factor = split_lookup.get((symbol, date_value), 1.0)
            if factor != 1.0:
                holdings[symbol] *= factor
                split_event_count += 1

        # Entitlement is based on post-split holdings before same-session
        # fills.  Compare it with the independent dividend-receivable ledger.
        ledger_day = ledger_run[ledger_run["date"].eq(date_value)]
        actual_dividends = ledger_day[ledger_day["kind"].eq("dividend_receivable")]
        actual_by_symbol = {
            str(symbol).upper(): group
            for symbol, group in actual_dividends.groupby(actual_dividends["symbol"].astype(str).str.upper(), dropna=False)
        }
        for action in dividends_by_date.get(date_value, []):
            symbol = str(action["symbol"]).upper()
            quantity = float(holdings.get(symbol, 0.0))
            expected_amount = quantity * float(action["rate"])
            actual = actual_by_symbol.get(symbol)
            actual_amount = float(actual["amount"].sum()) if actual is not None else 0.0
            ledger_payables = set(actual["payable_date"].dropna().tolist()) if actual is not None and "payable_date" in actual else set()
            expected_payable = action.get("payable_date")
            amount_difference = actual_amount - expected_amount
            if abs(amount_difference) > amount_tolerance:
                status = "AMOUNT_MISMATCH"
            elif expected_payable is None or pd.isna(expected_payable):
                status = "ACTION_PAYABLE_DATE_MISSING"
            elif ledger_payables and expected_payable not in ledger_payables:
                status = "PAYABLE_MISMATCH"
            elif expected_amount > amount_tolerance and actual is None:
                status = "MISSING_LEDGER_ENTITLEMENT"
            else:
                status = "MATCHED"
            dividend_rows.append({
                "candidate_id": candidate_id,
                "date": date_value.isoformat(),
                "symbol": symbol,
                "expected_quantity": quantity,
                "rate": float(action["rate"]),
                "expected_amount": expected_amount,
                "ledger_amount": actual_amount,
                "expected_payable_date": _safe_iso(expected_payable),
                "ledger_payable_date": ",".join(sorted(_safe_iso(item) or "" for item in ledger_payables)) or None,
                "amount_difference": amount_difference,
                "status": status,
            })

        # Apply fills after actions and before that session's close valuation.
        fills_day = fills_run[fills_run["date"].eq(date_value)]
        for row in fills_day.itertuples(index=False):
            symbol = str(getattr(row, "symbol", "")).upper()
            if symbol not in holdings:
                holdings[symbol] = 0.0
                symbols.append(symbol)
            quantity = float(getattr(row, "quantity", 0.0))
            side = str(getattr(row, "side", "")).lower()
            if side == "buy":
                holdings[symbol] += quantity
            elif side == "sell":
                holdings[symbol] -= quantity
            else:
                raise ValueError(f"ETF_RECONSTRUCTION_FILL_SIDE_INVALID:{side}")
            if holdings[symbol] < -amount_tolerance:
                negative_holding_events.append({"date": date_value.isoformat(), "symbol": symbol, "quantity": holdings[symbol]})

        settled_delta, pending_delta, released = _ledger_cash_delta(ledger_run, date_value)
        cash_settled += settled_delta
        cash_pending += pending_delta
        if cash_settled < -amount_tolerance or cash_pending < -amount_tolerance:
            negative_holding_events.append({"date": date_value.isoformat(), "symbol": "__cash__", "quantity": min(cash_settled, cash_pending)})

        holdings_value = 0.0
        for symbol, quantity in sorted(holdings.items()):
            if abs(quantity) <= amount_tolerance:
                continue
            close = raw_lookup.get((date_value, symbol))
            if close is None:
                missing_marks.append({"date": date_value.isoformat(), "symbol": symbol, "quantity": quantity})
                continue
            holdings_value += quantity * float(close)
        daily_rows.append({
            "date": date_value.isoformat(),
            "candidate_id": candidate_id,
            "phase": phase,
            "cost_scenario": cost_scenario,
            "cash_settled_reconstructed": cash_settled,
            "cash_pending_reconstructed": cash_pending,
            "holdings_value_reconstructed": holdings_value,
            "equity_reconstructed": cash_settled + cash_pending + holdings_value,
            "released_cash_reconstructed": released,
            **{f"holding_{symbol}": float(quantity) for symbol, quantity in sorted(holdings.items())},
        })

    dividend_checks = pd.DataFrame(dividend_rows) if dividend_rows else _empty_checks()
    daily = pd.DataFrame(daily_rows)
    summary = {
        "candidate_id": candidate_id,
        "phase": phase,
        "cost_scenario": cost_scenario,
        "rows": int(len(daily)),
        "date_start": daily["date"].iloc[0] if not daily.empty else None,
        "date_end": daily["date"].iloc[-1] if not daily.empty else None,
        "symbols": sorted(symbols),
        "split_event_count": int(split_event_count),
        "invalid_action_count": int(len(invalid_actions)),
        "invalid_actions": invalid_actions,
        "missing_marks": int(len(missing_marks)),
        "negative_holding_or_cash_events": int(len(negative_holding_events)),
        "dividend_check_count": int(len(dividend_checks)),
        "dividend_mismatch_count": int((~dividend_checks["status"].eq("MATCHED")).sum()) if not dividend_checks.empty else 0,
        "missing_mark_samples": missing_marks[:10],
        "negative_holding_or_cash_samples": negative_holding_events[:10],
    }
    return ReconstructionResult(daily, dividend_checks, summary)


def compare_saved_equity(
    reconstructed: pd.DataFrame,
    saved_equity: pd.DataFrame,
    *,
    tolerance: float = DEFAULT_TOLERANCE,
) -> dict[str, Any]:
    """Compare independently rebuilt balances to saved fields, excluding daily_profit."""
    if "date" not in saved_equity or "equity" not in saved_equity:
        raise ValueError("ETF_RECONSTRUCTION_SAVED_EQUITY_COLUMNS_MISSING")
    saved = saved_equity.copy()
    saved["date"] = _normalise_date(saved, "date")
    saved = saved.drop_duplicates("date", keep="last")
    rebuilt = reconstructed.copy()
    rebuilt["date"] = _normalise_date(rebuilt, "date")
    joined = saved.merge(rebuilt, on="date", how="outer", indicator=True, suffixes=("_saved", "_reconstructed"))
    comparison_fields = {
        "equity": "equity_reconstructed",
        "cash_settled": "cash_settled_reconstructed",
        "cash_pending": "cash_pending_reconstructed",
        "holdings_value": "holdings_value_reconstructed",
    }
    field_differences: dict[str, float | None] = {}
    for saved_field, rebuilt_field in comparison_fields.items():
        if saved_field not in joined or rebuilt_field not in joined:
            field_differences[saved_field] = None
            continue
        diff = pd.to_numeric(joined[saved_field], errors="coerce") - pd.to_numeric(joined[rebuilt_field], errors="coerce")
        field_differences[saved_field] = float(diff.abs().max()) if diff.notna().any() else None
    equity_diff = pd.to_numeric(joined.get("equity", pd.Series(dtype=float)), errors="coerce") - pd.to_numeric(joined.get("equity_reconstructed", pd.Series(dtype=float)), errors="coerce")
    equity_diff = equity_diff[joined["_merge"].eq("both")]
    max_abs = float(equity_diff.abs().max()) if equity_diff.notna().any() else None
    return {
        "saved_rows": int(len(saved)),
        "reconstructed_rows": int(len(rebuilt)),
        "matched_rows": int(joined["_merge"].eq("both").sum()),
        "missing_saved_rows": int(joined["_merge"].eq("right_only").sum()),
        "missing_reconstructed_rows": int(joined["_merge"].eq("left_only").sum()),
        "max_abs_equity_difference": max_abs,
        "field_max_abs_difference": field_differences,
        "tolerance": float(tolerance),
        "equity_within_tolerance": bool(max_abs is not None and max_abs <= tolerance and joined["_merge"].eq("both").all()),
        "comparison_fields": ["equity", "cash_settled", "cash_pending", "holdings_value"],
        "daily_profit_used": False,
    }


def _source_hashes(study_dir: Path) -> dict[str, str]:
    paths = (
        study_dir / "normalized" / "fills.parquet",
        study_dir / "normalized" / "cash_ledger.parquet",
        study_dir / "normalized" / "equity_daily.parquet",
        study_dir / "data" / "normalized" / "stock_bars_raw.parquet",
        study_dir / "data" / "normalized" / "corporate_actions.parquet",
    )
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise ValueError(f"ETF_RECONSTRUCTION_SOURCE_MISSING:{','.join(missing)}")
    return {str(path.relative_to(study_dir)): file_hash(path) for path in paths}


def audit_primary_continuous(
    study_dir: Path,
    output_dir: Path,
    *,
    tolerance: float = DEFAULT_TOLERANCE,
    initial_cash: float = DEFAULT_INITIAL_CASH,
) -> dict[str, Any]:
    """Audit every primary continuous base run in a saved study directory."""
    study_dir = Path(study_dir)
    output_dir = Path(output_dir)
    source_hashes = _source_hashes(study_dir)
    normalized = study_dir / "normalized"
    fills = pd.read_parquet(normalized / "fills.parquet")
    cash_ledger = pd.read_parquet(normalized / "cash_ledger.parquet")
    saved_equity = pd.read_parquet(normalized / "equity_daily.parquet")
    raw_bars = pd.read_parquet(study_dir / "data" / "normalized" / "stock_bars_raw.parquet")
    corporate_actions = pd.read_parquet(study_dir / "data" / "normalized" / "corporate_actions.parquet")
    required = {"candidate_id", "phase", "cost_scenario", "date", "equity"}
    if not required.issubset(saved_equity.columns):
        raise ValueError(f"ETF_RECONSTRUCTION_EQUITY_COLUMNS_MISSING:{','.join(sorted(required - set(saved_equity.columns)))}")
    primary_ids = sorted(
        saved_equity.loc[
            saved_equity["phase"].astype(str).eq("continuous")
            & saved_equity["cost_scenario"].astype(str).eq("base")
            & saved_equity["candidate_id"].astype(str).str.endswith("__primary")
            & ~saved_equity["candidate_id"].astype(str).str.startswith("BENCH__"),
            "candidate_id",
        ].astype(str).unique()
    )
    if not primary_ids:
        raise ValueError("ETF_RECONSTRUCTION_NO_PRIMARY_RUNS")

    daily_parts: list[pd.DataFrame] = []
    dividend_parts: list[pd.DataFrame] = []
    run_reports: list[dict[str, Any]] = []
    for candidate_id in primary_ids:
        saved_run = _filter_run(saved_equity, candidate_id=candidate_id, phase="continuous", cost_scenario="base")
        valuation_dates = _normalise_date(saved_run, "date")
        result = reconstruct_daily_equity(
            fills,
            cash_ledger,
            raw_bars,
            corporate_actions,
            candidate_id=candidate_id,
            valuation_dates=valuation_dates,
            phase="continuous",
            cost_scenario="base",
            initial_cash=initial_cash,
        )
        comparison = compare_saved_equity(result.daily, saved_run, tolerance=tolerance)
        run_report = {**result.summary, **comparison}
        run_report["equity_reconstruction_status"] = "MATCH" if comparison["equity_within_tolerance"] else "MISMATCH"
        run_reports.append(run_report)
        daily_parts.append(result.daily)
        if not result.dividend_checks.empty:
            dividend_parts.append(result.dividend_checks)

    reconstructed_daily = pd.concat(daily_parts, ignore_index=True) if daily_parts else pd.DataFrame()
    dividend_checks = pd.concat(dividend_parts, ignore_index=True) if dividend_parts else _empty_checks()
    output_dir.mkdir(parents=True, exist_ok=True)
    if not reconstructed_daily.empty:
        reconstructed_daily.to_parquet(output_dir / "reconstructed_equity.parquet", index=False)
    if not dividend_checks.empty:
        dividend_checks.to_parquet(output_dir / "dividend_checks.parquet", index=False)
    mismatch_count = sum(report["equity_reconstruction_status"] != "MATCH" for report in run_reports)
    dividend_mismatch_count = sum(int(report["dividend_mismatch_count"]) for report in run_reports)
    audit = {
        "schema_version": "etf-cash-v2-reconstruction-audit/v1",
        "status": "DIAGNOSTIC_ONLY",
        "overall_result": "ALL_RECONSTRUCTIONS_MATCH" if mismatch_count == 0 else "MISMATCHES_FOUND",
        "engine_compliance": False,
        "study_dir": str(study_dir.resolve()),
        "output_dir": str(output_dir.resolve()),
        "source_hashes": source_hashes,
        "run_scope": {
            "phase": "continuous",
            "cost_scenario": "base",
            "candidate_filter": "candidate_id ends with __primary and is not a benchmark",
            "candidate_count": len(primary_ids),
        },
        "initial_cash": float(initial_cash),
        "equity_tolerance": float(tolerance),
        "comparison_fields": ["equity", "cash_settled", "cash_pending", "holdings_value"],
        "daily_profit_used": False,
        "reconstruction_inputs": ["fills.parquet", "cash_ledger.parquet", "stock_bars_raw.parquet", "corporate_actions.parquet"],
        "reconstructed_artifacts": [
            str((output_dir / "reconstructed_equity.parquet").name),
            str((output_dir / "dividend_checks.parquet").name),
        ],
        "run_count": len(run_reports),
        "equity_mismatch_count": int(mismatch_count),
        "dividend_mismatch_count": int(dividend_mismatch_count),
        "all_runs_equity_within_tolerance": bool(mismatch_count == 0),
        "all_dividend_entitlements_matched": bool(dividend_mismatch_count == 0),
        "runs": run_reports,
        "limitations": [
            "This is an independent reconstruction diagnostic, not an engine-compliance certification.",
            "It does not recalculate signals, order intent, settlement eligibility, or strategy state.",
            "Saved daily_profit is excluded from all calculations and comparisons.",
        ],
    }
    atomic_json(output_dir / "reconstruction_audit.json", audit)
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    args = parser.parse_args()
    audit = audit_primary_continuous(args.study_dir, args.output_dir, tolerance=args.tolerance)
    print(json.dumps({
        "status": audit["status"],
        "overall_result": audit["overall_result"],
        "run_count": audit["run_count"],
        "equity_mismatch_count": audit["equity_mismatch_count"],
        "dividend_mismatch_count": audit["dividend_mismatch_count"],
        "output": str((args.output_dir / "reconstruction_audit.json").resolve()),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
