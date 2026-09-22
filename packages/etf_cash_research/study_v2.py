"""Study orchestration and immutable artifacts for the v2 ETF research tracks."""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path
from typing import Any

import pandas as pd

from packages.contracts.canonical import canonical_hash
from packages.research_data.artifacts import atomic_json, file_hash, write_parquet

from .collector import load_manifest
from .evaluation import chain_evaluation_windows
from .metrics import add_benchmark_comparison, compute_metrics, moving_block_bootstrap
from .protocol_v2 import (
    DEFAULT_STUDY_PROTOCOL,
    CandidateSpec,
    StudyProtocolV2,
    UniverseSpec,
    candidate_specs,
    protocol_envelope,
)
from .simulator_v2 import (
    GenericBacktestResult,
    _align_signal_frames,
    _intersection_sessions,
    _normalise_bars,
    run_generic_backtest,
)


def load_v2_bars_from_manifest(manifest_path: Path) -> pd.DataFrame:
    """Load raw bars and attach point-in-time actions from a hash-checked manifest."""
    manifest = load_manifest(manifest_path)
    datasets = {str(item["dataset_id"]): item for item in manifest.get("datasets", [])}
    raw_item = datasets.get("stock_bars_raw")
    if not raw_item:
        raise ValueError("ETF_V2_RAW_BARS_MISSING")
    raw_feeds = set(raw_item.get("feed", [])) if isinstance(raw_item.get("feed"), list) else {str(raw_item.get("feed"))}
    if len(raw_feeds) > 1:
        raise ValueError("ETF_V2_MIXED_FEEDS")
    raw_path = manifest_path.parent / str(raw_item["artifact"]["path"])
    if not raw_path.is_file() or file_hash(raw_path) != raw_item["artifact"].get("sha256"):
        raise ValueError("ETF_V2_RAW_BARS_HASH_MISMATCH")
    bars = pd.read_parquet(raw_path).rename(columns={"event_time": "date"})
    bars["date"] = pd.to_datetime(bars["date"], utc=True).dt.normalize()
    bars["symbol"] = bars["symbol"].astype(str).str.upper()
    bars["dividend"] = 0.0
    bars["dividend_payable_date"] = pd.Series(pd.NaT, index=bars.index, dtype="datetime64[ns, UTC]")
    bars["split_factor"] = 1.0

    split_item = datasets.get("stock_bars_split")
    if not split_item:
        raise ValueError("ETF_V2_SPLIT_BARS_MISSING")
    split_path = manifest_path.parent / str(split_item["artifact"]["path"])
    if not split_path.is_file() or file_hash(split_path) != split_item["artifact"].get("sha256"):
        raise ValueError("ETF_V2_SPLIT_BARS_HASH_MISMATCH")
    split_bars = pd.read_parquet(split_path).rename(columns={"event_time": "date"})
    split_bars["date"] = pd.to_datetime(split_bars["date"], utc=True).dt.normalize()
    split_bars["symbol"] = split_bars["symbol"].astype(str).str.upper()
    split_columns = split_bars[["date", "symbol", "open", "high", "low", "close"]].rename(
        columns={
            "open": "signal_open",
            "high": "signal_high",
            "low": "signal_low",
            "close": "signal_close",
        }
    )
    bars = bars.merge(split_columns, on=["date", "symbol"], how="left", validate="one_to_one")
    if bars[["signal_open", "signal_high", "signal_low", "signal_close"]].isna().any().any():
        raise ValueError("ETF_V2_SPLIT_BARS_COVERAGE_MISSING")

    actions_item = datasets.get("corporate_actions")
    if actions_item:
        action_path = manifest_path.parent / str(actions_item["artifact"]["path"])
        if not action_path.is_file() or file_hash(action_path) != actions_item["artifact"].get("sha256"):
            raise ValueError("ETF_V2_ACTIONS_HASH_MISMATCH")
        actions = pd.read_parquet(action_path)
        if not actions.empty:
            actions["symbol"] = actions["symbol"].astype(str).str.upper()
            actions["ex_date"] = pd.to_datetime(actions["ex_date"], utc=True, errors="coerce").dt.normalize()
            for action in actions.to_dict("records"):
                symbol = str(action.get("symbol", "")).upper()
                ex_date = action.get("ex_date")
                if not symbol or pd.isna(ex_date):
                    continue
                mask = (bars["symbol"] == symbol) & (bars["date"] == ex_date)
                action_type = str(action.get("action_type", action.get("type", ""))).lower()
                if "dividend" in action_type:
                    amount = _first_number(action, ("rate", "value", "amount"))
                    if amount is not None and amount >= 0:
                        bars.loc[mask, "dividend"] = float(amount)
                        payable = action.get("payable_date")
                        if payable is not None and not pd.isna(payable):
                            bars.loc[mask, "dividend_payable_date"] = pd.Timestamp(payable).tz_localize("UTC") if pd.Timestamp(payable).tzinfo is None else pd.Timestamp(payable).tz_convert("UTC")
                if "split" in action_type:
                    factor = _first_number(action, ("split_factor", "factor", "ratio"))
                    if factor is None:
                        factor = _split_factor_from_raw(bars, symbol, ex_date)
                    if factor is not None and factor > 0:
                        bars.loc[mask, "split_factor"] = float(factor)
                    else:
                        # Keep a sentinel that the simulator rejects.  A split
                        # without a defensible ratio cannot qualify.
                        bars.loc[mask, "split_factor"] = float("nan")
    return bars


def _first_number(record: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = record.get(key)
        if value is None or pd.isna(value):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    if keys == ("split_factor", "factor", "ratio"):
        old = _direct_number(record, ("old_rate", "old_shares", "numerator"))
        new = _direct_number(record, ("new_rate", "new_shares", "denominator"))
        if old and new:
            return new / old
    return None


def _direct_number(record: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = record.get(key)
        if value is None or pd.isna(value):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    return None


def _split_factor_from_raw(bars: pd.DataFrame, symbol: str, ex_date: pd.Timestamp) -> float | None:
    rows = bars[bars["symbol"] == symbol].sort_values("date")
    previous = rows[rows["date"] < ex_date].tail(1)
    current = rows[rows["date"] >= ex_date].head(1)
    if previous.empty or current.empty:
        return None
    before = float(previous.iloc[0]["close"])
    after = float(current.iloc[0]["close"])
    if before <= 0 or after <= 0:
        return None
    ratio = before / after
    common = (0.1, 0.2, 0.25, 0.3333333333, 0.5, 2.0, 3.0, 4.0, 5.0, 10.0)
    candidate = min(common, key=lambda value: abs(value - ratio))
    return candidate if abs(candidate - ratio) / candidate <= 0.12 else None


def build_strategy(candidate: CandidateSpec) -> Any:
    if candidate.universe.track_id == "a":
        from .strategies_track_a import build_track_a

        return build_track_a(candidate.strategy_id, variant=candidate.variant)
    from .strategies_track_b import build_track_b

    return build_track_b(
        candidate.strategy_id,
        broad_symbol=candidate.universe.tradable_symbols[0],
        semiconductor=candidate.universe.tradable_symbols[1],
        proxies=(candidate.universe.broad_proxy or candidate.universe.tradable_symbols[0], candidate.universe.semiconductor_proxy or "SOXX"),
        variant=candidate.variant,
    )


def _write_result(
    result: GenericBacktestResult,
    path: Path,
    *,
    window_id: str | None = None,
    phase: str | None = None,
) -> None:
    path.mkdir(parents=True, exist_ok=True)
    metrics = dict(result.metrics)
    metrics["window_id"] = window_id
    atomic_json(path / "metrics.json", metrics)
    atomic_json(path / "run_metadata.json", {
        "schema_version": "etf-cash-v2-run/v1",
        "candidate": result.candidate.as_dict(),
        "cost_scenario": result.cost_scenario,
        "execution_delay_sessions": result.execution_delay_sessions,
        "window_id": window_id,
        "protocol_hash": result.metrics.get("protocol_hash"),
        "status": "DETERMINISTIC_RESEARCH_ONLY",
        "historical_periods_reused": True,
        "llm_overlay": "NOT_PERFORMANCE_TESTED",
        "live_authority": False,
    })
    artifact_frames = (
        ("equity_daily", result.equity),
        ("signals", result.signals),
        ("orders", result.orders),
        ("fills", result.fills),
        ("cash_ledger", result.cash_ledger),
        ("trades", result.trades),
        ("component_ledger", result.component_ledger),
    )
    for name, frame in artifact_frames:
        if not frame.empty:
            frame = frame.copy()
            frame["cost_scenario"] = result.cost_scenario
            frame["phase"] = phase
            frame["window_id"] = window_id
            frame["candidate_id"] = result.candidate.candidate_id
            frame["track_id"] = result.candidate.universe.track_id
            frame["pair_id"] = result.candidate.universe.pair_id
        write_parquet(path, name, frame if not frame.empty else pd.DataFrame({"status": ["EMPTY"]}), tuple(frame.columns) if not frame.empty else ("status",))


def _candidate_metrics_rows(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for metrics_path in sorted(root.glob("**/metrics.json")):
        if metrics_path.parent == root:
            continue
        try:
            value = json.loads(metrics_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        value["artifact_path"] = str(metrics_path.parent.relative_to(root))
        rows.append(value)
    return rows


def _strategy_for(candidate: CandidateSpec) -> Any:
    return build_strategy(candidate)


def _study_candidates(track: str, *, pair_id: str | None, include_sensitivities: bool) -> list[CandidateSpec]:
    candidates = candidate_specs(track, include_sensitivities=include_sensitivities)
    if pair_id is None:
        return candidates
    normalized = pair_id.upper()
    return [item for item in candidates if item.universe.pair_id.upper() == normalized]


def _write_migration_comparison(bars: pd.DataFrame, output: Path, protocol: StudyProtocolV2) -> None:
    """Write an ablation report for the frozen S01/S10 migration.

    The old migration report compared v1 with one v2 run, which made a large
    result gap difficult to attribute.  This diagnostic keeps three distinct
    price paths visible:

    * v1 on split-consistent feature bars (the legacy reference);
    * v2 with the same split-consistent bars for both signals and execution;
    * v2 with split-consistent signals and actual raw execution/mark prices.

    It also runs a deliberately misaligned positional v2 reference and saves a
    paired signal trace.  That reference is an ablation only: it recreates the
    pre-fix positional-index bug and is never used for study results.
    """
    from .protocol import DEFAULT_PROTOCOL as V1_PROTOCOL
    from .simulator import run_backtest as run_v1_backtest
    from .strategies import ETFStrategy

    feature_bars = bars.copy()
    if {"signal_open", "signal_high", "signal_low", "signal_close"}.issubset(feature_bars.columns):
        for signal_column, raw_column in {
            "signal_open": "open",
            "signal_high": "high",
            "signal_low": "low",
            "signal_close": "close",
        }.items():
            feature_bars[raw_column] = feature_bars[signal_column]
        feature_bars["split_factor"] = 1.0
    feature_bars = feature_bars[feature_bars["symbol"].astype(str).isin(["QQQM", "SMH"])].copy()
    # Preserve the independent histories for the causal positional-index
    # ablation.  The v1 reference itself still receives only the common
    # overlap below, as required by its rectangular-history validator.
    full_feature_bars = feature_bars.copy()
    # The legacy simulator validates a rectangular history before applying
    # its requested study dates.  The extended v2 manifest can legitimately
    # have different listing/coverage starts (QQQM starts later than SMH), so
    # give the diagnostic only the common overlap.  This does not affect the
    # audited v2 run; it keeps the migration comparison comparable and
    # avoids treating out-of-scope warm-up rows as a data failure.
    common_start = feature_bars.groupby("symbol")["date"].min().max()
    common_end = feature_bars.groupby("symbol")["date"].max().min()
    feature_bars = feature_bars[(feature_bars["date"] >= common_start) & (feature_bars["date"] <= common_end)].copy()

    pair_symbols = ("QQQM", "SMH")
    normalized_feature_frame = _normalise_bars(full_feature_bars)
    common_sessions = _intersection_sessions(normalized_feature_frame, pair_symbols)
    independent_signal_frames = {
        symbol: normalized_feature_frame[normalized_feature_frame["symbol"] == symbol]
        .sort_values("date", kind="stable")
        .reset_index(drop=True)
        for symbol in pair_symbols
    }
    independent_indices = {
        symbol: {pd.Timestamp(value): index for index, value in enumerate(item["date"])}
        for symbol, item in independent_signal_frames.items()
    }
    aligned_signal_frames, aligned_indices = _align_signal_frames(
        normalized_feature_frame,
        pair_symbols,
        common_sessions,
    )
    primary_sessions = [
        item
        for item in common_sessions
        if item >= pd.Timestamp(protocol.primary_start, tz="UTC")
        and item <= pd.Timestamp(protocol.primary_end, tz="UTC")
    ]

    def _signal_snapshot(signal: Any) -> dict[str, Any]:
        return {
            "asof": pd.Timestamp(signal.asof).isoformat(),
            "reason_code": str(signal.reason_code),
            "target_weights": {
                str(symbol): round(float(weight), 12)
                for symbol, weight in sorted(signal.target_weights.items())
            },
            "entries": list(signal.entries),
            "exits": list(signal.exits),
        }

    def _trace(strategy_id: str, *, aligned: bool) -> list[dict[str, Any]]:
        strategy = ETFStrategy(strategy_id, "SMH")
        frames = aligned_signal_frames if aligned else independent_signal_frames
        index_map = aligned_indices if aligned else independent_indices["QQQM"]
        rows: list[dict[str, Any]] = []
        for execution_date in primary_sessions:
            prior_dates = [item for item in common_sessions if item < execution_date]
            if not prior_dates:
                continue
            information_cutoff = prior_dates[-1]
            index = index_map[information_cutoff]
            signal = strategy.evaluate(frames, index)
            rows.append({
                "execution_date": execution_date.isoformat(),
                "information_cutoff": information_cutoff.isoformat(),
                "signal": _signal_snapshot(signal),
                "frame_dates": {
                    symbol: pd.Timestamp(frames[symbol].iloc[index]["date"]).isoformat()
                    for symbol in pair_symbols
                },
            })
        return rows

    def _trace_comparison(strategy_id: str) -> dict[str, Any]:
        old_trace = _trace(strategy_id, aligned=False)
        aligned_trace = _trace(strategy_id, aligned=True)
        differences: list[dict[str, Any]] = []
        for old, aligned in zip(old_trace, aligned_trace, strict=True):
            if old["signal"] != aligned["signal"]:
                differences.append({
                    "execution_date": old["execution_date"],
                    "information_cutoff": old["information_cutoff"],
                    "old_signal": old["signal"],
                    "aligned_signal": aligned["signal"],
                    "old_frame_dates": old["frame_dates"],
                    "aligned_frame_dates": aligned["frame_dates"],
                })
        return {
            "decision_count": len(aligned_trace),
            "matching_signal_count": len(aligned_trace) - len(differences),
            "divergence_count": len(differences),
            "first_divergence": differences[0] if differences else None,
            "old_trace_hash": canonical_hash(old_trace),
            "aligned_trace_hash": canonical_hash(aligned_trace),
        }

    alignment_mismatches: list[dict[str, Any]] = []
    for information_cutoff in common_sessions:
        index = independent_indices["QQQM"].get(information_cutoff)
        if index is None:
            continue
        old_dates = {
            symbol: pd.Timestamp(independent_signal_frames[symbol].iloc[index]["date"])
            for symbol in pair_symbols
        }
        if any(value != information_cutoff for value in old_dates.values()):
            alignment_mismatches.append({
                "information_cutoff": information_cutoff.isoformat(),
                "old_frame_dates": {symbol: value.isoformat() for symbol, value in old_dates.items()},
            })

    class _MisalignedPositionalStrategy:
        """Ablation wrapper reproducing the pre-common-calendar v2 call."""

        def __init__(self, strategy_id: str) -> None:
            self.inner = ETFStrategy(strategy_id, "SMH")

        def evaluate(self, frames: Any, index: int, **kwargs: Any) -> Any:
            # The corrected simulator passes an aligned index.  Translate it
            # back to the old QQQM independent-history index and deliberately
            # evaluate the unaligned symbol frames.
            asof = pd.Timestamp(frames["QQQM"].iloc[index]["date"])
            old_index = independent_indices["QQQM"][asof]
            return self.inner.evaluate(independent_signal_frames, old_index)

    def _metrics_summary(result: GenericBacktestResult) -> dict[str, Any]:
        metrics = result.metrics
        return {
            "net_return": metrics.get("net_return"),
            "net_pnl": metrics.get("net_pnl"),
            "ending_equity": metrics.get("ending_equity"),
            "max_drawdown": metrics.get("max_drawdown"),
            "buy_count": metrics.get("buy_count"),
            "sell_count": metrics.get("sell_count"),
            "turnover": metrics.get("turnover"),
            "trading_costs": metrics.get("trading_costs"),
        }

    def _equity_gap(left: Any, right: Any) -> dict[str, Any]:
        left_frame = left.equity[["date", "equity"]].copy()
        right_frame = right.equity[["date", "equity"]].copy()
        joined = left_frame.merge(right_frame, on="date", suffixes=("_left", "_right"))
        if joined.empty:
            return {"matched_sessions": 0, "max_abs_equity_gap": None, "first_gap": None}
        joined["abs_gap"] = (joined["equity_left"] - joined["equity_right"]).abs()
        first = joined[joined["abs_gap"] > 0.01].iloc[0] if (joined["abs_gap"] > 0.01).any() else None
        return {
            "matched_sessions": int(len(joined)),
            "max_abs_equity_gap": float(joined["abs_gap"].max()),
            "first_gap": {
                "date": str(first["date"]),
                "left": float(first["equity_left"]),
                "right": float(first["equity_right"]),
                "absolute": float(first["abs_gap"]),
            } if first is not None else None,
        }

    def _first_fill_difference(left: Any, right: Any) -> dict[str, Any] | None:
        columns = ("date", "symbol", "side", "quantity", "price", "notional", "fee")
        left_frame = left.fills.reindex(columns=columns).fillna("").reset_index(drop=True)
        right_frame = right.fills.reindex(columns=columns).fillna("").reset_index(drop=True)
        limit = min(len(left_frame), len(right_frame))
        for index in range(limit):
            if any(str(left_frame.iloc[index][column]) != str(right_frame.iloc[index][column]) for column in columns):
                return {"index": index, "left": left_frame.iloc[index].to_dict(), "right": right_frame.iloc[index].to_dict()}
        if len(left_frame) != len(right_frame):
            return {"index": limit, "left": "<end-of-fills>" if limit >= len(left_frame) else left_frame.iloc[limit].to_dict(), "right": "<end-of-fills>" if limit >= len(right_frame) else right_frame.iloc[limit].to_dict()}
        return None

    traces = {strategy_id: _trace_comparison(strategy_id) for strategy_id in ("S01", "S10")}
    rows: list[dict[str, Any]] = []
    for strategy_id in ("S01", "S10"):
        for cost_v2 in protocol.costs_track_a:
            v1_cost = next(item for item in V1_PROTOCOL.costs if item.name == cost_v2.name)
            legacy = run_v1_backtest(
                feature_bars,
                strategy_id=strategy_id,
                semiconductor="SMH",
                cost=v1_cost,
                protocol=V1_PROTOCOL,
                start=protocol.primary_start,
                end=protocol.primary_end,
            )
            normalized_candidate = CandidateSpec(f"{strategy_id}__QQQM_SMH__migration_normalized", strategy_id, UniverseSpec("QQQM_SMH", pair_symbols, track_id="a"))
            actual_candidate = CandidateSpec(f"{strategy_id}__QQQM_SMH__migration_actual_raw", strategy_id, UniverseSpec("QQQM_SMH", pair_symbols, track_id="a"))
            normalized = run_generic_backtest(
                feature_bars,
                candidate=normalized_candidate,
                strategy=ETFStrategy(strategy_id, "SMH"),
                cost=cost_v2,
                protocol=protocol,
                start=protocol.primary_start,
                end=protocol.primary_end,
            )
            actual = run_generic_backtest(
                bars,
                candidate=actual_candidate,
                strategy=ETFStrategy(strategy_id, "SMH"),
                cost=cost_v2,
                protocol=protocol,
                start=protocol.primary_start,
                end=protocol.primary_end,
            )
            misaligned = run_generic_backtest(
                bars,
                candidate=actual_candidate,
                strategy=_MisalignedPositionalStrategy(strategy_id),
                cost=cost_v2,
                protocol=protocol,
                start=protocol.primary_start,
                end=protocol.primary_end,
            )
            legacy_summary = _metrics_summary(legacy)
            normalized_summary = _metrics_summary(normalized)
            actual_summary = _metrics_summary(actual)
            misaligned_summary = _metrics_summary(misaligned)
            normalized_matches_legacy = all(
                (
                    abs(float(legacy_summary[key]) - float(normalized_summary[key])) <= 0.01
                    if key in {"net_return", "max_drawdown"} and legacy_summary.get(key) is not None and normalized_summary.get(key) is not None
                    else legacy_summary.get(key) == normalized_summary.get(key)
                )
                for key in ("net_return", "max_drawdown", "buy_count", "sell_count")
            )
            raw_vs_normalized_equity = _equity_gap(actual, normalized)
            legacy_vs_normalized_equity = _equity_gap(legacy, normalized)
            raw_delta = (actual_summary["net_return"] - normalized_summary["net_return"]) if actual_summary["net_return"] is not None and normalized_summary["net_return"] is not None else None
            rows.append({
                "strategy_id": strategy_id,
                "cost_scenario": cost_v2.name,
                "legacy_v1_feature_execution": legacy_summary,
                "aligned_v2_normalized_execution": normalized_summary,
                "aligned_v2_actual_raw_execution": actual_summary,
                "misaligned_v2_actual_raw_ablation": misaligned_summary,
                "normalized_matches_legacy": normalized_matches_legacy,
                "evidence": {
                    "legacy_vs_normalized_equity": legacy_vs_normalized_equity,
                    "actual_raw_vs_normalized_equity": raw_vs_normalized_equity,
                    "first_legacy_vs_normalized_fill_difference": _first_fill_difference(legacy, normalized),
                    "first_actual_raw_vs_normalized_fill_difference": _first_fill_difference(actual, normalized),
                },
                "deltas": {
                    "actual_raw_minus_normalized_net_return": raw_delta,
                    "misaligned_raw_minus_aligned_raw_net_return": (misaligned_summary["net_return"] - actual_summary["net_return"]) if misaligned_summary["net_return"] is not None and actual_summary["net_return"] is not None else None,
                },
                "residual_reasons": [
                    "POSITIONAL_DATE_MISMATCH_RESOLVED: v2 now restricts every strategy frame to the universe common-session calendar before passing a positional index.",
                    f"RAW_VS_SPLIT_FEATURE_PATH: actual-raw v2 executes and marks on raw prices while calculating signals from split-consistent feature prices; measured primary net-return delta={raw_delta!r}.",
                    "NO_MEASURED_PRICE_PATH_RESIDUAL: raw and normalized paths are equal within the recorded equity/fill evidence when their delta is zero; inspect the evidence object rather than inferring a residual from the path definitions.",
                    "LEGACY_REFERENCE_SCOPE: v1 and normalized v2 use common-overlap split-consistent bars; this diagnostic does not establish live execution equivalence or validate unrelated simulator changes.",
                ],
            })
    atomic_json(output / "migration_comparison.json", {
        "schema_version": "etf-cash-v2-migration-comparison/v2",
        "protocol_hash": protocol.protocol_hash,
        "status": "RESEARCH_DIAGNOSTIC",
        "scope": "S01/S10 positional-date alignment ablation; no full-study rerun",
        "common_calendar": {
            "symbols": list(pair_symbols),
            "independent_coverage": {
                symbol: {
                    "start": independent_signal_frames[symbol]["date"].min().isoformat(),
                    "end": independent_signal_frames[symbol]["date"].max().isoformat(),
                    "sessions": len(independent_signal_frames[symbol]),
                }
                for symbol in pair_symbols
            },
            "common_start": common_sessions[0].isoformat(),
            "common_end": common_sessions[-1].isoformat(),
            "common_sessions": len(common_sessions),
            "old_positional_mismatch_count": len(alignment_mismatches),
            "old_positional_first_mismatch": alignment_mismatches[0] if alignment_mismatches else None,
            "aligned_positional_mismatch_count": 0,
            "primary_decision_sessions": len(primary_sessions),
        },
        "signal_alignment": traces,
        "rows": rows,
    })


class _StaticTarget:
    def __init__(self, weights: dict[str, float]) -> None:
        self.weights = dict(weights)

    def evaluate(self, frames, index, **kwargs):
        symbol = next(iter(frames))
        date_value = frames[symbol].iloc[index]["date"]
        return type("Signal", (), {"asof": date_value, "target_weights": self.weights, "reason_code": "STATIC_BENCHMARK", "entries": tuple(self.weights), "exits": ()})()


def _benchmark_specs(track: str) -> list[tuple[str, CandidateSpec, dict[str, float]]]:
    output: list[tuple[str, CandidateSpec, dict[str, float]]] = []
    def one(name: str, symbols: tuple[str, ...], weights: dict[str, float], track_id: str = "a") -> None:
        universe = UniverseSpec(name, symbols, track_id=track_id)
        output.append((name, CandidateSpec(f"BENCH__{name}", "BENCHMARK", universe), weights))
    for symbol in ("QQQ", "SPY", "QQQM", "SMH", "SOXX"):
        one(symbol, (symbol,), {symbol: 0.99}, "a")
    one("CASH", ("QQQM",), {}, "a")
    if track in {"a", "all"}:
        one("QQQM_SMH_STATIC50", ("QQQM", "SMH"), {"QQQM": 0.495, "SMH": 0.495}, "a")
    if track in {"b", "all"}:
        one("TQQQ_SOXL_STATIC50", ("TQQQ", "SOXL"), {"TQQQ": 0.495, "SOXL": 0.495}, "b")
        one("SPXL_SOXL_STATIC50", ("SPXL", "SOXL"), {"SPXL": 0.495, "SOXL": 0.495}, "b")
        one("TQQQ_SOXL_STATIC33CASH", ("TQQQ", "SOXL"), {"TQQQ": 0.33, "SOXL": 0.33}, "b")
        one("SPXL_SOXL_STATIC33CASH", ("SPXL", "SOXL"), {"SPXL": 0.33, "SOXL": 0.33}, "b")
    return output


def run_study(
    *,
    bars: pd.DataFrame,
    output: Path,
    track: str = "all",
    protocol: StudyProtocolV2 = DEFAULT_STUDY_PROTOCOL,
    include_sensitivities: bool = False,
    run_windows: bool = True,
    run_continuous: bool = True,
    run_delay: bool = True,
    run_delay_windows: bool = False,
    run_stress: bool = True,
    pair_id: str | None = None,
) -> Path:
    if output.exists() and any(output.iterdir()):
        raise ValueError("ETF_V2_OUTPUT_NOT_EMPTY")
    output.mkdir(parents=True, exist_ok=True)
    atomic_json(output / "protocol.json", protocol_envelope(protocol))
    study_candidates = _study_candidates(track, pair_id=pair_id, include_sensitivities=include_sensitivities)
    if not study_candidates:
        raise ValueError("ETF_V2_PAIR_OR_TRACK_EMPTY")
    bars = bars.copy()
    bars["date"] = pd.to_datetime(bars["date"], utc=True).dt.normalize()
    bars["symbol"] = bars["symbol"].astype(str).str.upper()
    registry = [item.as_dict() for item in study_candidates]
    atomic_json(output / "candidate_registry.json", {"schema_version": "etf-cash-v2-candidate-registry/v1", "candidates": registry, "registry_hash": canonical_hash(registry)})
    bars_snapshot = bars.copy()
    bars_snapshot["date"] = pd.to_datetime(bars_snapshot["date"], utc=True).astype(str)
    write_parquet(output, "input_bars_snapshot", bars_snapshot, tuple(bars_snapshot.columns))
    atomic_json(output / "input_manifest.json", {"schema_version": "etf-cash-v2-input-manifest/v1", "rows": len(bars_snapshot), "symbols": sorted(bars_snapshot["symbol"].astype(str).unique().tolist()), "input_hash": canonical_hash(json.loads(bars_snapshot.to_json(date_format="iso", orient="records")))})
    coverage = bars.groupby("symbol")["date"].agg(["min", "max", "count"]).reset_index()
    coverage["min"] = coverage["min"].astype(str)
    coverage["max"] = coverage["max"].astype(str)
    missing_payable = int((bars.get("dividend", pd.Series(dtype=float)).fillna(0.0).astype(float).gt(0) & bars.get("dividend_payable_date", pd.Series(index=bars.index, dtype="datetime64[ns, UTC]")).isna()).sum())
    study_floor = min([protocol.primary_start, *[item[1] for item in protocol.stress_periods]])
    relevant_missing_mask = (
        (bars["date"].dt.date >= study_floor)
        & bars.get("dividend", pd.Series(dtype=float)).fillna(0.0).astype(float).gt(0)
        & bars.get("dividend_payable_date", pd.Series(index=bars.index, dtype="datetime64[ns, UTC]")).isna()
    )
    relevant_missing_payable = int(relevant_missing_mask.sum())
    atomic_json(output / "data_quality.json", {
        "schema_version": "etf-cash-v2-data-quality/v1",
        "status": "REVIEW_REQUIRED" if missing_payable else "OK",
        "symbols": sorted(bars["symbol"].astype(str).unique().tolist()),
        "coverage": coverage.to_dict(orient="records"),
        "signal_features": [column for column in ("signal_open", "signal_high", "signal_low", "signal_close") if column in bars.columns],
        "missing_dividend_payable_dates": missing_payable,
        "missing_dividend_payable_dates_in_study": relevant_missing_payable,
        "split_events": int((pd.to_numeric(bars.get("split_factor", 1.0), errors="coerce").fillna(1.0) != 1.0).sum()),
    })

    rows: list[dict[str, Any]] = []
    for candidate in study_candidates:
        costs = protocol.costs_track_a if candidate.universe.track_id == "a" else protocol.costs_track_b
        for cost in costs:
            if run_continuous:
                result = run_generic_backtest(bars, candidate=candidate, strategy=_strategy_for(candidate), cost=cost, protocol=protocol, start=protocol.primary_start, end=protocol.primary_end)
                result_path = output / candidate.candidate_id / cost.name / "continuous"
                _write_result(result, result_path, phase="continuous")
                rows.append({**result.metrics, "phase": "continuous", "window_id": None, "benchmark": False, "artifact_path": str(result_path.relative_to(output))})
            if run_windows:
                for window_id, start, end in protocol.evaluation_windows:
                    result = run_generic_backtest(bars, candidate=candidate, strategy=_strategy_for(candidate), cost=cost, protocol=protocol, start=start, end=end)
                    result_path = output / candidate.candidate_id / cost.name / window_id
                    _write_result(result, result_path, window_id=window_id, phase="evaluation")
                    rows.append({**result.metrics, "phase": "evaluation", "window_id": window_id, "benchmark": False, "artifact_path": str(result_path.relative_to(output))})
            if run_delay:
                result = run_generic_backtest(bars, candidate=candidate, strategy=_strategy_for(candidate), cost=cost, protocol=protocol, start=protocol.primary_start, end=protocol.primary_end, execution_delay_sessions=protocol.delay_stress_sessions)
                result_path = output / candidate.candidate_id / cost.name / "delay_1_session"
                _write_result(result, result_path, phase="delay")
                rows.append({**result.metrics, "phase": "delay", "window_id": None, "benchmark": False, "artifact_path": str(result_path.relative_to(output))})
                if run_delay_windows:
                    for window_id, window_start, window_end in protocol.evaluation_windows:
                        delayed_window = run_generic_backtest(
                            bars,
                            candidate=candidate,
                            strategy=_strategy_for(candidate),
                            cost=cost,
                            protocol=protocol,
                            start=window_start,
                            end=window_end,
                            execution_delay_sessions=protocol.delay_stress_sessions,
                        )
                        delayed_path = output / candidate.candidate_id / cost.name / f"delay_{window_id}"
                        _write_result(delayed_window, delayed_path, window_id=window_id, phase="delay")
                        rows.append({**delayed_window.metrics, "phase": "delay", "window_id": window_id, "benchmark": False, "artifact_path": str(delayed_path.relative_to(output))})
            if run_stress:
                for stress_id, stress_start, stress_end in protocol.stress_periods:
                    result = run_generic_backtest(bars, candidate=candidate, strategy=_strategy_for(candidate), cost=cost, protocol=protocol, start=stress_start, end=stress_end)
                    result_path = output / candidate.candidate_id / cost.name / stress_id
                    _write_result(result, result_path, window_id=stress_id, phase="stress")
                    rows.append({**result.metrics, "phase": "stress", "window_id": stress_id, "benchmark": False, "artifact_path": str(result_path.relative_to(output))})
    benchmarks = _benchmark_specs(track)
    if pair_id is not None:
        normalized_pair = pair_id.upper()
        benchmarks = [
            item
            for item in benchmarks
            if item[1].universe.pair_id.upper() == normalized_pair
            or item[1].universe.pair_id.upper().startswith(normalized_pair + "_")
            or item[1].universe.pair_id in {"QQQ", "SPY", "QQQM", "SMH", "SOXX", "CASH"}
        ]
    for benchmark_name, benchmark, weights in benchmarks:
        costs = protocol.costs_track_a if benchmark.universe.track_id == "a" else protocol.costs_track_b
        for cost in costs:
            if run_continuous:
                result = run_generic_backtest(bars, candidate=benchmark, strategy=_StaticTarget(weights), cost=cost, protocol=protocol, start=protocol.primary_start, end=protocol.primary_end)
                result_path = output / "benchmarks" / benchmark_name / cost.name / "continuous"
                _write_result(result, result_path, phase="continuous")
                rows.append({**result.metrics, "phase": "continuous", "window_id": None, "benchmark": True, "artifact_path": str(result_path.relative_to(output))})
            if run_windows:
                for window_id, start, end in protocol.evaluation_windows:
                    result = run_generic_backtest(bars, candidate=benchmark, strategy=_StaticTarget(weights), cost=cost, protocol=protocol, start=start, end=end)
                    result_path = output / "benchmarks" / benchmark_name / cost.name / window_id
                    _write_result(result, result_path, window_id=window_id, phase="evaluation")
                    rows.append({**result.metrics, "phase": "evaluation", "window_id": window_id, "benchmark": True, "artifact_path": str(result_path.relative_to(output))})
    leaderboard = pd.DataFrame(rows)
    if not leaderboard.empty:
        write_parquet(output, "equity_daily", _concat_artifacts(output, "equity_daily"), tuple(_concat_artifacts(output, "equity_daily").columns))
        write_parquet(output, "signals", _concat_artifacts(output, "signals"), tuple(_concat_artifacts(output, "signals").columns))
        write_parquet(output, "orders", _concat_artifacts(output, "orders"), tuple(_concat_artifacts(output, "orders").columns))
        write_parquet(output, "fills", _concat_artifacts(output, "fills"), tuple(_concat_artifacts(output, "fills").columns))
        write_parquet(output, "cash_ledger", _concat_artifacts(output, "cash_ledger"), tuple(_concat_artifacts(output, "cash_ledger").columns))
        write_parquet(output, "trades", _concat_artifacts(output, "trades"), tuple(_concat_artifacts(output, "trades").columns))
        write_parquet(output, "component_ledger", _concat_artifacts(output, "component_ledger"), tuple(_concat_artifacts(output, "component_ledger").columns))
        leaderboard = _enrich_benchmark_metrics(output, leaderboard, protocol)
        leaderboard.to_csv(output / "leaderboard.csv", index=False)
    atomic_json(output / "study_metadata.json", {
        "schema_version": "etf-cash-v2-study/v2",
        "track": track,
        "protocol_hash": protocol.protocol_hash,
        "candidate_count": len(study_candidates),
        "status": "DETERMINISTIC_RESEARCH_ONLY",
        "historical_periods_reused": True,
        "stress_periods_requested": [item[0] for item in protocol.stress_periods],
        "stress_periods_executed": bool(run_stress),
        "benchmarks_included": True,
        "pair_id": pair_id,
        "bootstrap": {"samples": protocol.bootstrap_samples, "block_length": protocol.bootstrap_block_length, "seed": protocol.bootstrap_seed},
    })
    if track in {"a", "all"}:
        _write_migration_comparison(bars, output, protocol)
    return output


def _concat_artifacts(root: Path, name: str) -> pd.DataFrame:
    frames = []
    for path in root.glob(f"**/{name}.parquet"):
        # The normalized root is an aggregate produced after the individual
        # runs.  Exclude it when rebuilding aggregates, otherwise repeated
        # post-processing silently duplicates every row.
        if path.parent in {root, root / "normalized"}:
            continue
        try:
            frames.append(pd.read_parquet(path))
        except Exception:
            continue
    if not frames:
        return pd.DataFrame({"status": ["EMPTY"]})
    return pd.concat(frames, ignore_index=True, sort=False)


def _enrich_benchmark_metrics(
    output: Path,
    leaderboard: pd.DataFrame,
    protocol: StudyProtocolV2,
) -> pd.DataFrame:
    """Attach benchmark gaps, beta/correlation and paired bootstrap intervals.

    The calculation is deliberately a post-processing step over immutable
    daily equity ledgers.  It does not feed signals or selection decisions.
    Benchmark columns are scalar in the CSV; nested bootstrap metadata is
    expanded into fixed quantile columns so the report remains machine-readable.
    """
    equity_path = output / "equity_daily.parquet"
    if not equity_path.is_file():
        equity_path = output / "normalized" / "equity_daily.parquet"
    if not equity_path.is_file():
        return leaderboard
    try:
        equity = pd.read_parquet(equity_path)
    except (OSError, ValueError):
        return leaderboard
    required = {"candidate_id", "phase", "cost_scenario", "date", "equity"}
    if not required.issubset(equity.columns):
        return leaderboard
    equity = equity.copy()
    equity["date"] = pd.to_datetime(equity["date"], utc=True)
    benchmark_ids = {
        "qqq": "BENCH__QQQ",
        "spy": "BENCH__SPY",
        "qqqm": "BENCH__QQQM",
        "smh": "BENCH__SMH",
        "soxx": "BENCH__SOXX",
    }
    for row_index, row in leaderboard.iterrows():
        candidate_id = str(row.get("candidate_id", ""))
        if candidate_id.startswith("BENCH__"):
            continue
        key = (candidate_id, str(row.get("phase", "")), str(row.get("cost_scenario", "")), row.get("window_id"))
        candidate_equity = equity[equity["candidate_id"].astype(str) == candidate_id]
        candidate_equity = candidate_equity[candidate_equity["phase"].astype(str) == str(key[1])]
        candidate_equity = candidate_equity[candidate_equity["cost_scenario"].astype(str) == str(key[2])]
        if key[3] is not None and not pd.isna(key[3]):
            candidate_equity = candidate_equity[candidate_equity["window_id"].astype(str) == str(key[3])]
        if candidate_equity.empty:
            continue
        for prefix, benchmark_id in benchmark_ids.items():
            benchmark_equity = equity[equity["candidate_id"].astype(str) == benchmark_id]
            benchmark_equity = benchmark_equity[benchmark_equity["phase"].astype(str) == str(key[1])]
            benchmark_equity = benchmark_equity[benchmark_equity["cost_scenario"].astype(str) == str(key[2])]
            if key[3] is not None and not pd.isna(key[3]):
                benchmark_equity = benchmark_equity[benchmark_equity["window_id"].astype(str) == str(key[3])]
            if benchmark_equity.empty:
                continue
            comparison = add_benchmark_comparison(candidate_equity, benchmark_equity, prefix=prefix)
            for name, value in comparison.items():
                leaderboard.loc[row_index, name] = value
            joined = candidate_equity[["date", "equity"]].merge(
                benchmark_equity[["date", "equity"]], on="date", suffixes=("_strategy", "_benchmark")
            ).sort_values("date")
            if len(joined) >= 2:
                bootstrap = moving_block_bootstrap(
                    joined["equity_strategy"].pct_change().fillna(joined["equity_strategy"].iloc[0] / protocol.initial_cash - 1),
                    joined["equity_benchmark"].pct_change().fillna(joined["equity_benchmark"].iloc[0] / protocol.initial_cash - 1),
                    samples=protocol.bootstrap_samples,
                    block_length=protocol.bootstrap_block_length,
                    seed=protocol.bootstrap_seed,
                )
                quantiles = bootstrap.get("gap_quantiles") or {}
                for quantile in ("p025", "p05", "p50", "p95", "p975"):
                    leaderboard.loc[row_index, f"{prefix}_bootstrap_{quantile}"] = quantiles.get(quantile)
    return leaderboard


def _ensure_cash_benchmark(output: Path, leaderboard: pd.DataFrame, protocol: StudyProtocolV2) -> pd.DataFrame:
    """Materialize the zero-interest cash reference when merging older runs."""
    if leaderboard.empty or leaderboard["candidate_id"].astype(str).eq("BENCH__CASH").any():
        return leaderboard
    equity_path = output / "normalized" / "equity_daily.parquet"
    if not equity_path.is_file():
        return leaderboard
    equity = pd.read_parquet(equity_path)
    if equity.empty:
        return leaderboard
    new_rows: list[dict[str, Any]] = []
    combinations = leaderboard[(leaderboard["phase"].isin(["continuous", "evaluation"]))][["phase", "cost_scenario", "window_id"]].drop_duplicates().to_dict("records")
    for combination in combinations:
        phase = str(combination["phase"])
        cost_name = str(combination["cost_scenario"])
        window_id = combination.get("window_id")
        mask = equity["phase"].astype(str).eq(phase) & equity["cost_scenario"].astype(str).eq(cost_name)
        if window_id is not None and not pd.isna(window_id):
            mask &= equity["window_id"].astype(str).eq(str(window_id))
        dates = pd.to_datetime(equity.loc[mask, "date"], utc=True).drop_duplicates().sort_values()
        if dates.empty:
            continue
        cash_equity = pd.DataFrame({
            "date": dates.astype(str),
            "candidate_id": "BENCH__CASH",
            "track_id": "a",
            "pair_id": "CASH",
            "cash_settled": protocol.initial_cash,
            "cash_pending": 0.0,
            "holdings_value": 0.0,
            "equity": protocol.initial_cash,
            "invested_exposure": 0.0,
            "approx_3x_invested_exposure": None,
            "released_cash": 0.0,
            "daily_profit": 0.0,
            "daily_return": 0.0,
            "cumulative_profit": 0.0,
            "drawdown": 0.0,
            "cost_scenario": cost_name,
            "phase": phase,
            "window_id": window_id,
        })
        artifact_dir = output / "benchmarks" / "CASH" / cost_name / ("continuous" if phase == "continuous" else str(window_id))
        artifact_dir.mkdir(parents=True, exist_ok=True)
        metrics = compute_metrics(cash_equity, pd.DataFrame(), initial_cash=protocol.initial_cash, start=dates.iloc[0], end=dates.iloc[-1], trades=pd.DataFrame(), cash_ledger=pd.DataFrame())
        metrics.update({"candidate_id": "BENCH__CASH", "track_id": "a", "pair_id": "CASH", "strategy_id": "BENCHMARK", "variant": "primary", "cost_scenario": cost_name, "phase": phase, "window_id": window_id, "benchmark": True, "artifact_path": str(artifact_dir.relative_to(output))})
        atomic_json(artifact_dir / "metrics.json", metrics)
        write_parquet(artifact_dir, "equity_daily", cash_equity, tuple(cash_equity.columns))
        for name, columns in (("signals", ("status",)), ("orders", ("status",)), ("fills", ("status",)), ("cash_ledger", ("status",)), ("trades", ("status",)), ("component_ledger", ("status",))):
            write_parquet(artifact_dir, name, pd.DataFrame({"status": ["EMPTY"]}), columns)
        new_rows.append(metrics)
        equity = pd.concat([equity, cash_equity], ignore_index=True, sort=False)
    if new_rows:
        write_parquet(output, "equity_daily", equity, tuple(equity.columns))
        leaderboard = pd.concat([leaderboard, pd.DataFrame(new_rows)], ignore_index=True, sort=False)
    return leaderboard


def _json_safe(value: Any) -> Any:
    """Convert pandas/numpy missing scalars to JSON ``null`` recursively."""
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    # pandas/numpy scalar values expose ``item``; converting finite numeric
    # scalars keeps the selection artifact portable across Python versions.
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            return _json_safe(value.item())
        except (ValueError, TypeError):
            pass
    try:
        missing = pd.isna(value)
        if isinstance(missing, bool) and missing:
            return None
    except (TypeError, ValueError):
        pass
    return value


def rank_study(study_dir: Path, *, protocol: StudyProtocolV2 = DEFAULT_STUDY_PROTOCOL) -> Path:
    leaderboard_path = study_dir / "leaderboard.csv"
    if not leaderboard_path.is_file():
        raise ValueError("ETF_V2_LEADERBOARD_MISSING")
    frame = pd.read_csv(leaderboard_path)
    audit_path = study_dir / "correction_audit.json"
    audit = json.loads(audit_path.read_text()) if audit_path.is_file() else {}
    integrity_blockers = list(audit.get("blockers", []))
    if not audit_path.is_file():
        integrity_blockers.append("missing_execution_conformance_audit")
    quality_ok = False
    quality_path = study_dir / "data_quality.json"
    if quality_path.is_file():
        try:
            quality = json.loads(quality_path.read_text(encoding="utf-8"))
            quality_ok = int(quality.get("missing_dividend_payable_dates_in_study", 0)) == 0
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            quality_ok = False
    primary_mask = frame["candidate_id"].astype(str).str.endswith("__primary")
    primary = frame[(frame["phase"] == "continuous") & (frame["cost_scenario"] == "base") & primary_mask].copy()
    eval_frame = frame[(frame["phase"] == "evaluation") & (frame["cost_scenario"].isin(["base", "stress"])) & primary_mask]
    grouped = eval_frame.groupby(["candidate_id", "cost_scenario"], as_index=False).agg(
        evaluation_return=("net_return", lambda values: float((1.0 + pd.to_numeric(values, errors="coerce")).prod() - 1.0)),
        individual_window_drawdown=("max_drawdown", "max"),
        positive_windows=("net_return", lambda values: int((values > 0).sum())),
        window_count=("net_return", "count"),
    )
    equity_path = study_dir / "normalized" / "equity_daily.parquet"
    daily_equity = pd.read_parquet(equity_path) if equity_path.is_file() else pd.DataFrame()
    window_ids = [item[0] for item in protocol.evaluation_windows]
    index_metrics: dict[tuple[str, str, str], dict[str, float]] = {}
    index_frames = []
    if not daily_equity.empty:
        for key, daily_group in daily_equity[daily_equity["phase"].isin(["evaluation", "delay"])].groupby(["candidate_id", "phase", "cost_scenario"]):
            try:
                chained = chain_evaluation_windows(daily_group, window_ids, initial_cash=protocol.initial_cash)
            except ValueError:
                continue  # Missing/invalid daily evidence fails qualification below.
            index_frames.append(chained)
            index_metrics[key] = {
                "return": float(chained["evaluation_index"].iloc[-1] / protocol.initial_cash - 1.0),
                "drawdown": float(chained["drawdown"].max()),
            }
    if index_frames:
        pd.concat(index_frames, ignore_index=True).to_parquet(study_dir / "evaluation_index.parquet", index=False)
    grouped["evaluation_drawdown"] = [index_metrics.get((row.candidate_id, "evaluation", row.cost_scenario), {}).get("drawdown", float("nan")) for row in grouped.itertuples()]
    grouped["daily_evidence_valid"] = [(row.candidate_id, "evaluation", row.cost_scenario) in index_metrics and abs(index_metrics[(row.candidate_id, "evaluation", row.cost_scenario)]["return"] - row.evaluation_return) < 1e-9 for row in grouped.itertuples()]
    qualifying: list[dict[str, Any]] = []
    required_stress_global = {"stress_2022"}
    for row in primary.to_dict("records"):
        cid = row["candidate_id"]
        track_id = str(row.get("track_id", "a"))
        pair_id = str(row.get("pair_id", ""))
        required_stress = set(required_stress_global)
        if track_id == "b":
            required_stress.add("stress_2020")
        eval_rows = grouped[grouped["candidate_id"] == cid]
        base = eval_rows[eval_rows["cost_scenario"] == "base"]
        stress = eval_rows[eval_rows["cost_scenario"] == "stress"]
        continuous_stress = frame[(frame["candidate_id"] == cid) & (frame["phase"] == "continuous") & (frame["cost_scenario"] == "stress")]
        delay = frame[(frame["candidate_id"] == cid) & (frame["phase"] == "delay") & (frame["cost_scenario"] == "stress")]
        stress_periods = frame[(frame["candidate_id"] == cid) & (frame["phase"] == "stress") & (frame["cost_scenario"].isin(["base", "stress"]))]
        limit = protocol.max_drawdown_track_a if track_id == "a" else protocol.max_drawdown_track_b
        seen_stress = set(stress_periods.get("window_id", pd.Series(dtype=str)).dropna().astype(str))
        missing_stress = sorted(required_stress - seen_stress)
        missing_stress_costs = [f"{window}:{cost_name}" for window in required_stress for cost_name in ("base", "stress") if len(stress_periods[(stress_periods["window_id"] == window) & (stress_periods["cost_scenario"] == cost_name)]) != 1]
        stress_ok = not missing_stress_costs and (pd.to_numeric(stress_periods["max_drawdown"], errors="coerce").max() <= limit)
        delay_windows = delay[delay["window_id"].astype(str).isin([item[0] for item in protocol.evaluation_windows])]
        delay_index = index_metrics.get((cid, "delay", "stress"))
        if len(delay_windows) == len(protocol.evaluation_windows) and delay_index:
            delay_return = delay_index["return"]
            delay_drawdown = delay_index["drawdown"]
            delay_basis = "evaluation_index"
        else:
            delay_return = 0.0
            delay_drawdown = 1.0
            delay_basis = "missing"
        delay_ok = delay_return > 0.0 and delay_drawdown <= limit
        qualifies = bool(
            not base.empty
            and not stress.empty
            and float(base.iloc[0]["evaluation_return"]) > 0.0
            and float(stress.iloc[0]["evaluation_return"]) > 0.0
            and int(base.iloc[0]["positive_windows"]) >= 4
            and int(base.iloc[0]["window_count"]) == len(protocol.evaluation_windows)
            and int(stress.iloc[0]["window_count"]) == len(protocol.evaluation_windows)
            and float(base.iloc[0]["evaluation_drawdown"]) <= limit
            and float(stress.iloc[0]["evaluation_drawdown"]) <= limit
            and bool(base.iloc[0]["daily_evidence_valid"])
            and bool(stress.iloc[0]["daily_evidence_valid"])
            and float(base.iloc[0]["individual_window_drawdown"]) <= limit
            and float(stress.iloc[0]["individual_window_drawdown"]) <= limit
            and stress_ok
            and float(row.get("net_return", 0.0)) > 0.0
            and float(row.get("max_drawdown", 1.0)) <= limit
            and not continuous_stress.empty
            and float(continuous_stress.iloc[0].get("net_return", 0.0)) > 0.0
            and float(continuous_stress.iloc[0].get("max_drawdown", 1.0)) <= limit
            and delay_ok
            and quality_ok
        )
        reasons: list[str] = []
        if base.empty or stress.empty or not bool(base.iloc[0]["daily_evidence_valid"]) or not bool(stress.iloc[0]["daily_evidence_valid"]):
            reasons.append("evaluation_daily_evidence_missing_or_inconsistent")
        if base.empty or float(base.iloc[0]["evaluation_return"]) <= 0:
            reasons.append("evaluation_base_return_nonpositive_or_missing")
        if stress.empty or float(stress.iloc[0]["evaluation_return"]) <= 0:
            reasons.append("evaluation_stress_return_nonpositive_or_missing")
        if not base.empty and int(base.iloc[0]["positive_windows"]) < 4:
            reasons.append("fewer_than_four_positive_windows")
        if base.empty or int(base.iloc[0]["window_count"]) != len(protocol.evaluation_windows):
            reasons.append("evaluation_base_window_coverage_incomplete")
        if stress.empty or int(stress.iloc[0]["window_count"]) != len(protocol.evaluation_windows):
            reasons.append("evaluation_stress_window_coverage_incomplete")
        if not base.empty and float(base.iloc[0]["evaluation_drawdown"]) > limit:
            reasons.append("evaluation_base_drawdown_exceeds_ceiling")
        if not stress.empty and float(stress.iloc[0]["evaluation_drawdown"]) > limit:
            reasons.append("evaluation_stress_drawdown_exceeds_ceiling")
        if not missing_stress and not stress_ok:
            reasons.append("historical_stress_drawdown_exceeds_ceiling")
        if missing_stress:
            reasons.append("missing_required_stress:" + ",".join(missing_stress))
        if missing_stress_costs:
            reasons.append("historical_stress_cost_coverage_incomplete:" + ",".join(sorted(missing_stress_costs)))
        if float(row.get("net_return", 0.0)) <= 0:
            reasons.append("continuous_base_return_nonpositive")
        if float(row.get("max_drawdown", 1.0)) > limit:
            reasons.append("continuous_base_drawdown_exceeds_ceiling")
        if continuous_stress.empty or float(continuous_stress.iloc[0].get("net_return", 0.0)) <= 0:
            reasons.append("continuous_stress_return_nonpositive_or_missing")
        elif float(continuous_stress.iloc[0].get("max_drawdown", 1.0)) > limit:
            reasons.append("continuous_stress_drawdown_exceeds_ceiling")
        if not delay_ok:
            reasons.append("delayed_execution_gate_failed")
        if not quality_ok:
            reasons.append("relevant_corporate_action_data_incomplete")
        performance_gates_pass = qualifies
        if integrity_blockers:
            qualifies = False
            reasons.append("execution_protocol_audit_incomplete")
        if not reasons:
            reasons.append("passed_all_qualification_gates")
        status = "BLOCKED_EXECUTION_AUDIT" if integrity_blockers else "QUALIFIED" if qualifies else (
            "PROVISIONAL—2020 STRESS INCOMPLETE" if track_id == "b" and "stress_2020" in missing_stress else "FAILED"
        )
        qualifying.append({
            **row,
            "pair_id": pair_id,
            "evaluation_return": float(base.iloc[0]["evaluation_return"]) if not base.empty else None,
            "evaluation_drawdown": float(base.iloc[0]["evaluation_drawdown"]) if not base.empty else None,
            "stress_coverage": "COMPLETE" if not missing_stress else "INCOMPLETE",
            "qualification_status": status,
            "qualification_reasons": ";".join(reasons),
            "delayed_evaluation_return": delay_return,
            "delayed_evaluation_drawdown": delay_drawdown,
            "delayed_gate_basis": delay_basis,
            "qualifies": qualifies,
            "performance_gates_pass": performance_gates_pass,
        })
    ranked = pd.DataFrame(qualifying)
    if not ranked.empty:
        average_equity: dict[str, float] = {}
        equity_path = study_dir / "normalized" / "equity_daily.parquet"
        if equity_path.is_file():
            try:
                equity_frame = pd.read_parquet(equity_path)
                continuous_base = equity_frame[
                    (equity_frame["phase"].astype(str) == "continuous")
                    & (equity_frame["cost_scenario"].astype(str) == "base")
                ]
                average_equity = continuous_base.groupby("candidate_id")["equity"].mean().astype(float).to_dict()
            except (OSError, ValueError, KeyError):
                average_equity = {}
        ranked["average_equity"] = ranked["candidate_id"].map(average_equity).fillna(
            pd.to_numeric(ranked.get("starting_equity", 1000.0), errors="coerce")
        )
        ranked["turnover_to_average_equity"] = pd.to_numeric(ranked.get("turnover", 0.0), errors="coerce") / pd.to_numeric(ranked["average_equity"], errors="coerce")
        ranked = ranked.sort_values(["qualifies", "evaluation_return", "evaluation_drawdown", "turnover_to_average_equity", "candidate_id"], ascending=[False, False, True, True, True], kind="stable")
    ranked.to_csv(study_dir / "selection_leaderboard.csv", index=False)
    if not ranked.empty:
        qualifying_rows = ranked[ranked["qualifies"]].copy()
        shortlist = qualifying_rows.groupby("pair_id", sort=False).head(3)
    else:
        shortlist = ranked
    selection = {
        "schema_version": "etf-cash-v2-selection/v2",
        "protocol_hash": protocol.protocol_hash,
        "shortlist": _json_safe(shortlist.to_dict("records")),
        "status": "BLOCKED_EXECUTION_AUDIT" if integrity_blockers else "QUALIFIED" if not shortlist.empty else "NO_QUALIFYING_STRATEGY",
        "qualification_ceiling": {"track_a": protocol.max_drawdown_track_a, "track_b": protocol.max_drawdown_track_b},
        "historical_stress_required": sorted(required_stress_global | {"stress_2020"}),
        "all_primary_dispositions": _json_safe(ranked.to_dict("records")) if not ranked.empty else [],
        "integrity_blockers": integrity_blockers,
    }
    selection["selection_hash"] = canonical_hash(selection)
    atomic_json(study_dir / "selection.json", selection)
    return study_dir / "selection.json"


def reproduce_study(
    study_dir: Path,
    *,
    output: Path,
    protocol: StudyProtocolV2 = DEFAULT_STUDY_PROTOCOL,
    track: str | None = None,
) -> Path:
    manifest = json.loads((study_dir / "input_manifest.json").read_text(encoding="utf-8"))
    bars = pd.read_parquet(study_dir / "normalized" / "input_bars_snapshot.parquet") if (study_dir / "normalized" / "input_bars_snapshot.parquet").exists() else pd.read_parquet(study_dir / "input_bars_snapshot.parquet")
    actual = canonical_hash(json.loads(bars.assign(date=pd.to_datetime(bars["date"], utc=True).astype(str)).to_json(date_format="iso", orient="records")))
    if actual != manifest.get("input_hash"):
        raise ValueError("ETF_V2_INPUT_HASH_MISMATCH")
    metadata_path = study_dir / "study_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.is_file() else {}
    if metadata.get("protocol_hash") and metadata["protocol_hash"] != protocol.protocol_hash:
        raise ValueError("ETF_V2_PROTOCOL_HASH_MISMATCH")
    selected_track = track or str(metadata.get("track", "all"))
    return run_study(bars=bars, output=output, track=selected_track, protocol=protocol)


def merge_studies(study_dirs: list[Path], *, output: Path, protocol: StudyProtocolV2 = DEFAULT_STUDY_PROTOCOL) -> Path:
    """Merge pair-parallel v2 outputs after checking their immutable inputs."""
    if len(study_dirs) < 2:
        raise ValueError("ETF_V2_MERGE_REQUIRES_TWO_STUDIES")
    if output.exists() and any(output.iterdir()):
        raise ValueError("ETF_V2_OUTPUT_NOT_EMPTY")
    output.mkdir(parents=True, exist_ok=True)
    manifests = []
    registries: list[dict[str, Any]] = []
    leaderboards: list[pd.DataFrame] = []
    for study_dir in study_dirs:
        metadata = json.loads((study_dir / "study_metadata.json").read_text(encoding="utf-8"))
        if metadata.get("protocol_hash") != protocol.protocol_hash:
            raise ValueError("ETF_V2_PROTOCOL_HASH_MISMATCH")
        manifest_path = study_dir / "data_manifest.json"
        if manifest_path.is_file():
            manifests.append(json.loads(manifest_path.read_text(encoding="utf-8")))
        registry = json.loads((study_dir / "candidate_registry.json").read_text(encoding="utf-8"))
        registries.extend(registry.get("candidates", []))
        leaderboards.append(pd.read_csv(study_dir / "leaderboard.csv"))
        for item in study_dir.iterdir():
            if item.name in {"leaderboard.csv", "selection.json", "selection_leaderboard.csv", "study_metadata.json", "protocol.json", "candidate_registry.json", "data_manifest.json", "input_manifest.json", "normalized"}:
                continue
            destination = output / item.name
            if item.is_dir():
                shutil.copytree(item, destination, dirs_exist_ok=True)
            elif not destination.exists():
                shutil.copy2(item, destination)
    if manifests:
        first_manifest_hash = manifests[0].get("manifest_hash")
        if any(item.get("manifest_hash") != first_manifest_hash for item in manifests[1:]):
            raise ValueError("ETF_V2_INPUT_MANIFEST_MISMATCH")
        shutil.copy2(study_dirs[0] / "data_manifest.json", output / "data_manifest.json")
    source_snapshot = study_dirs[0] / "normalized" / "input_bars_snapshot.parquet"
    if source_snapshot.is_file():
        (output / "normalized").mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_snapshot, output / "normalized" / "input_bars_snapshot.parquet")
    atomic_json(output / "protocol.json", protocol_envelope(protocol))
    merged_registry = {"schema_version": "etf-cash-v2-candidate-registry/v1", "candidates": registries, "registry_hash": canonical_hash(registries)}
    atomic_json(output / "candidate_registry.json", merged_registry)
    for name in ("equity_daily", "signals", "orders", "fills", "cash_ledger", "trades", "component_ledger"):
        frames = []
        for study_dir in study_dirs:
            path = study_dir / "normalized" / f"{name}.parquet"
            if path.is_file():
                frame = pd.read_parquet(path)
                is_empty_sentinel = (
                    not frame.empty
                    and list(frame.columns) == ["status"]
                    and frame["status"].astype(str).eq("EMPTY").all()
                )
                if not frame.empty and not is_empty_sentinel:
                    frames.append(frame)
        merged = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame({"status": ["EMPTY"]})
        # Track-parallel runs each carry the same QQQ/SPY/QQQM/etc. reference
        # benchmarks.  Keep one immutable copy of identical ledger rows in
        # the merged output; candidate-specific rows remain untouched.
        if not merged.empty:
            merged = merged.drop_duplicates(ignore_index=True)
        write_parquet(output, name, merged, tuple(merged.columns))
    leaderboard = pd.concat(leaderboards, ignore_index=True, sort=False)
    leaderboard = leaderboard.drop_duplicates(subset=["candidate_id", "phase", "cost_scenario", "window_id"], keep="first")
    leaderboard = _ensure_cash_benchmark(output, leaderboard, protocol)
    leaderboard = _enrich_benchmark_metrics(output, leaderboard, protocol)
    leaderboard.to_csv(output / "leaderboard.csv", index=False)
    source_input_manifest = study_dirs[0] / "input_manifest.json"
    if source_input_manifest.is_file():
        shutil.copy2(source_input_manifest, output / "input_manifest.json")
    else:
        atomic_json(output / "input_manifest.json", {"schema_version": "etf-cash-v2-merged-input-manifest/v1", "source_manifest_hash": manifests[0].get("manifest_hash") if manifests else None})
    atomic_json(output / "study_metadata.json", {"schema_version": "etf-cash-v2-study/v2", "track": "all", "protocol_hash": protocol.protocol_hash, "candidate_count": len(registries), "status": "DETERMINISTIC_RESEARCH_ONLY", "merged_from": [str(item) for item in study_dirs], "historical_periods_reused": True})
    return output
