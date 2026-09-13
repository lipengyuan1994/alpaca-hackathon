"""Deterministic Group B SMH/SOXL pair-cell replay engine.

Research-only machinery for the frozen Group B candidates
``opening_range_breakout__all_feasible__o2_v1`` and
``gap_continuation__all_feasible__o2_v1`` over the SMH/SOXL pair cells.
It implements plan sections 5-13 of
``docs/research/GROUP_B_SEMICONDUCTOR_PLAN.md``: variant enumeration from the
frozen candidate specs, next-observation execution, ``TREND_VWAP_OR_60M_V1``
exit replay, complete-market-date daily returns, 2025 OOS folds, section 12
metric authority, and the synchronized centered five-session circular
moving-block bootstrap with family-wise max-statistic p-values.

The engine is fail-closed: a real outcome run requires the attested
data-steward manifests (plan section 14, gate B0).  Without them, ``main``
writes ``pair_cell_refusal.json`` and exits 2.  The library functions are
pure over their inputs so synthetic fixtures can validate determinism
without viewing any real outcome.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd
import yaml

from .artifacts import atomic_json, ensure_empty_output, write_parquet
from .group_b_features import (
    SessionBars,
    aggregate_intervals,
    breakout_features,
    feature_dictionary,
    gap_features,
    split_sessions,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ET = "America/New_York"
_PAIR_SYMBOLS = ("SMH", "SOXL")
_CANDIDATES = (
    "opening_range_breakout__all_feasible__o2_v1",
    "gap_continuation__all_feasible__o2_v1",
)
_CENTRAL_EXIT_CAP = "15:45"
_EQUITY_START = 100000.0
_ANNUALIZATION = 252
_BOOTSTRAP_SEED = 20260829
_BOOTSTRAP_REPS = 10000
_BOOTSTRAP_BLOCK = 5
_SIGNAL_COLUMNS = (
    "candidate_id",
    "symbol",
    "decision_time",
    "variant_id",
    "action",
    "score",
    "reason_code",
)
_TRADE_COLUMNS = (
    "candidate_id",
    "symbol",
    "variant_id",
    "session_date",
    "decision_time",
    "action",
    "entry_time",
    "entry_price",
    "exit_time",
    "exit_price",
    "exit_reason",
    "trade_return",
    "missing_exit",
)
_FOLD_COLUMNS = (
    "candidate_id",
    "variant_id",
    "fold_id",
    "sessions",
    "trades",
    "positive_trades",
    "hit_rate",
    "net_return",
)
_OOS_FOLDS = (
    ("2025Q1", "2025-01-01", "2025-03-31"),
    ("2025Q2", "2025-04-01", "2025-06-30"),
    ("2025Q3", "2025-07-01", "2025-09-30"),
    ("2025Q4", "2025-10-01", "2025-12-31"),
)


class GroupBPairCellError(Exception):
    """Declared, fail-closed refusal carrying an uppercase reason code."""


@dataclass(frozen=True)
class CandidateSpec:
    """One frozen candidate's loaded central config and diagnostics."""

    candidate_id: str
    plugin_id: str
    central: Mapping[str, Any]
    diagnostics: Mapping[str, Sequence[Any]]
    config_hash: str
    sensitivities_hash: str


@dataclass(frozen=True)
class Variant:
    """One enumerated run variant: central, diagnostic, or falsification."""

    variant_id: str
    kind: str  # central | diagnostic | falsification
    parameters: Mapping[str, str]
    removal: str | None = None


@dataclass
class SymbolSessions:
    """Precomputed per-symbol session state for the replay loop."""

    symbol: str
    sessions: dict[str, SessionBars] = field(default_factory=dict)
    ordered_dates: list[str] = field(default_factory=list)
    intervals: dict[str, pd.DataFrame] = field(default_factory=dict)


def _lf_hash(path: Path) -> str:
    normalized = path.read_bytes().replace(b"\r\n", b"\n")
    return f"sha256:{hashlib.sha256(normalized).hexdigest()}"


def load_candidate_spec(repo_root: Path, candidate_id: str) -> CandidateSpec:
    """Load and hash one candidate's frozen central config and sensitivities."""
    if candidate_id not in _CANDIDATES:
        raise GroupBPairCellError(f"CANDIDATE_NOT_RECOGNIZED_{candidate_id}")
    directory = repo_root / "research" / "candidates" / candidate_id
    config_path = directory / "central_config.json"
    sensitivities_path = directory / "sensitivities.yaml"
    if not config_path.is_file():
        raise GroupBPairCellError("CANDIDATE_CENTRAL_CONFIG_MISSING")
    if not sensitivities_path.is_file():
        raise GroupBPairCellError("CANDIDATE_SENSITIVITIES_MISSING")
    try:
        central = json.loads(config_path.read_text(encoding="utf-8"))
        sensitivities = yaml.safe_load(sensitivities_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, yaml.YAMLError, UnicodeDecodeError) as exc:
        raise GroupBPairCellError("CANDIDATE_SPEC_INVALID") from exc
    if not isinstance(central, dict) or not isinstance(sensitivities, dict):
        raise GroupBPairCellError("CANDIDATE_SPEC_INVALID")
    diagnostics = sensitivities.get("diagnostics")
    if not isinstance(diagnostics, dict):
        raise GroupBPairCellError("CANDIDATE_DIAGNOSTICS_MISSING")
    plugin_id = "gap_continuation" if candidate_id.startswith("gap_") else "opening_range_breakout"
    return CandidateSpec(
        candidate_id=candidate_id,
        plugin_id=plugin_id,
        central=central,
        diagnostics=diagnostics,
        config_hash=_lf_hash(config_path),
        sensitivities_hash=_lf_hash(sensitivities_path),
    )


def enumerate_variants(spec: CandidateSpec) -> list[Variant]:
    """Enumerate central, one-at-a-time diagnostics, and falsification variants."""
    variants = [Variant("central", "central", dict(spec.central))]
    for parameter, values in sorted(spec.diagnostics.items()):
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            continue
        for value in values:
            rendered = _render_parameter(value)
            parameters = dict(spec.central)
            parameters[parameter] = rendered
            if parameter.endswith("exit_minutes") or parameter == "time_exit_minutes":
                parameters["time_exit_minutes"] = int(rendered)
            variants.append(
                Variant(f"diag_{parameter}_{rendered}", "diagnostic", parameters)
            )
    if spec.candidate_id.startswith("gap_"):
        removals = ("continuation", "session_vwap")
    else:
        removals = ("volume_ratio", "session_vwap")
    for removal in removals:
        variants.append(
            Variant(f"falsify_remove_{removal}", "falsification", dict(spec.central), removal)
        )
    return variants


def _render_parameter(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def plugin_signal(repo_root: Path, plugin_id: str) -> tuple[Callable[..., Any], str]:
    """Import the frozen pure ``evaluate_signal`` and its content hash.

    The Group B plug-ins ship as src-layout research packages that the
    registry hash in ``packages.plugin_integrity`` does not cover, so the
    canonical content hash is computed here over the same material scheme:
    every ``*.py`` file under the package, path-then-sha256, hashed through
    ``canonical_hash`` with the registry-style entrypoint recorded.
    """
    from packages.contracts.canonical import canonical_hash

    package_root = repo_root / "strategy_plugins" / f"{plugin_id}_v1"
    module_path = package_root / "src" / f"{plugin_id}_v1" / "signal.py"
    if not package_root.is_dir() or not module_path.is_file():
        raise GroupBPairCellError("PLUGIN_SIGNAL_MODULE_MISSING")
    files = sorted(
        path for path in package_root.rglob("*.py") if "__pycache__" not in path.parts
    )
    if not files:
        raise GroupBPairCellError("PLUGIN_PACKAGE_EMPTY")
    material: list[dict[str, str]] = []
    for path in files:
        if path.is_symlink():
            raise GroupBPairCellError("PLUGIN_PACKAGE_SYMLINK_FORBIDDEN")
        material.append(
            {
                "path": path.relative_to(repo_root).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    content_hash = canonical_hash(
        {"entrypoint": f"{plugin_id}_v1.plugin:Plugin", "source_files": material}
    )
    module_name = f"group_b_engine_{plugin_id}_signal"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise GroupBPairCellError("PLUGIN_SIGNAL_MODULE_INVALID")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    evaluate = getattr(module, "evaluate_signal", None)
    if not callable(evaluate):
        raise GroupBPairCellError("PLUGIN_EVALUATE_SIGNAL_MISSING")
    return evaluate, content_hash


def _decision_times(spec: Mapping[str, Any]) -> tuple[str, ...]:
    if "decision_time_et" in spec:
        return (str(spec["decision_time_et"]),)
    start = str(spec.get("decision_start_et", "10:30:01"))
    end = str(spec.get("decision_end_et", "14:30:01"))
    step = int(spec.get("decision_step_minutes", 30))
    start_minute = _minutes_of(start)
    end_minute = _minutes_of(end)
    grid = []
    minute = start_minute
    while minute <= end_minute:
        grid.append(f"{minute // 60:02d}:{minute % 60:02d}:{start.split(':')[2]}")
        minute += step
    return tuple(grid)


def _minutes_of(clock: str) -> int:
    hours, minutes, _ = clock.split(":")
    return int(hours) * 60 + int(minutes)


def build_symbol_sessions(bars: pd.DataFrame, symbol: str) -> SymbolSessions:
    """Precompute sessions, ordered dates, and interval frames for one symbol."""
    sessions = split_sessions(bars, symbol=symbol)
    ordered = sorted(sessions)
    state = SymbolSessions(symbol=symbol, sessions=sessions, ordered_dates=ordered)
    for date in ordered:
        state.intervals[date] = aggregate_intervals(sessions[date])
    return state


def _session_timestamp(date: str, clock: str) -> pd.Timestamp:
    return pd.Timestamp(f"{date} {clock}", tz=_ET)


def _minute_open(session: SessionBars, when: pd.Timestamp) -> float | None:
    """Open of the first whole minute beginning at or after ``when``."""
    target = when.ceil("min")
    frame = session.frame
    matches = frame[frame["et"] == target]
    if matches.empty:
        return None
    return float(matches.iloc[0]["open"])


def _evaluate_removal(
    family: str,
    features: Mapping[str, Decimal],
    removal: str,
    parameters: Mapping[str, Any],
) -> tuple[str, Decimal | None, str]:
    """Mirror the frozen signal minus exactly one named confirmation gate."""
    close = features["close_completed_15m_v1"]
    vwap = features["session_iex_vwap_v1"]
    if family == "gap":
        gap_z = features["gap_z_60_v1"]
        continuation = features["continuation_ratio_v1"]
        threshold = Decimal(str(parameters.get("gap_z_threshold", "1.00")))
        continuation_threshold = Decimal(str(parameters.get("continuation_ratio_threshold", "0.25")))
        bullish = gap_z >= threshold
        bearish = gap_z <= -threshold
        if removal == "continuation":
            direction_ok = (bullish and close > vwap) or (bearish and close < vwap)
            score = abs(gap_z) / threshold
            if direction_ok:
                return ("BUY" if bullish else "SELL"), score, "FALSIFY_WITHOUT_CONTINUATION"
            return "NO_TRADE", None, "FALSIFY_WITHOUT_CONTINUATION"
        if removal == "session_vwap":
            confirmed = continuation >= continuation_threshold
            score = min(abs(gap_z) / threshold, continuation / continuation_threshold)
            if confirmed and (bullish or bearish):
                return ("BUY" if bullish else "SELL"), score, "FALSIFY_WITHOUT_VWAP"
            return "NO_TRADE", None, "FALSIFY_WITHOUT_VWAP"
    else:
        up_break = features["up_break_fraction_or30_v1"]
        down_break = features["down_break_fraction_or30_v1"]
        volume_ratio = features["volume_ratio_same_time_20_v1"]
        break_threshold = Decimal(str(parameters.get("break_fraction_threshold", "0.10")))
        volume_threshold = Decimal(str(parameters.get("volume_ratio_threshold", "1.25")))
        bullish = up_break >= break_threshold
        bearish = down_break >= break_threshold
        if removal == "volume_ratio":
            direction_ok = (bullish and close > vwap) or (bearish and close < vwap)
            score = (up_break if bullish else down_break) / break_threshold
            if direction_ok:
                return ("BUY" if bullish else "SELL"), score, "FALSIFY_WITHOUT_VOLUME"
            return "NO_TRADE", None, "FALSIFY_WITHOUT_VOLUME"
        if removal == "session_vwap":
            confirmed = volume_ratio >= volume_threshold
            score = min(
                (up_break if bullish else down_break) / break_threshold,
                volume_ratio / volume_threshold,
            )
            if confirmed and (bullish or bearish):
                return ("BUY" if bullish else "SELL"), score, "FALSIFY_WITHOUT_VWAP"
            return "NO_TRADE", None, "FALSIFY_WITHOUT_VWAP"
    raise GroupBPairCellError(f"FALSIFICATION_REMOVAL_NOT_DECLARED_{removal}")


def replay_variant(
    *,
    spec: CandidateSpec,
    variant: Variant,
    symbol_state: SymbolSessions,
    evaluate: Callable[..., Any],
    start_date: str,
    end_date: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Replay one candidate/variant/symbol cell over the frozen date range.

    Returns ``(signal_rows, trade_rows)``.  Signal rows record every decision
    with its outcome and reason; trade rows record filled underlying entries
    with ``TREND_VWAP_OR_60M_V1`` exits.  The signed underlying forward return
    is a research diagnostic, never an option-proxy P&L claim.
    """
    family = "gap" if spec.candidate_id.startswith("gap_") else "orb"
    grid = _decision_times(spec.central)
    exit_minutes = int(variant.parameters.get("time_exit_minutes", 60))
    signals: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    for date in symbol_state.ordered_dates:
        if not (start_date <= date <= end_date):
            continue
        session = symbol_state.sessions[date]
        intervals = symbol_state.intervals[date]
        if not session.valid:
            _record_signal(
                signals, spec, variant, symbol_state.symbol, date, "10:30:01",
                "NO_TRADE", None, "EARLY_CLOSE_SESSION",
            )
            continue
        entered = False
        for clock in grid:
            decision = _session_timestamp(date, clock)
            if entered:
                _record_signal(
                    signals, spec, variant, symbol_state.symbol, date, clock,
                    "NO_TRADE", None, "DAILY_ENTRY_ALREADY_USED",
                )
                continue
            decision_et = decision.tz_convert(_ET)
            eligible = intervals[
                (intervals["interval_end"] + pd.Timedelta(seconds=1) <= decision_et)
                & intervals["complete"]
            ]
            if eligible.empty:
                _record_signal(
                    signals, spec, variant, symbol_state.symbol, date, clock,
                    "NO_TRADE", None, "DATA_MISSING",
                )
                continue
            interval = eligible.iloc[-1]
            interval_index = int(interval["bucket"])
            if family == "gap":
                values = gap_features(
                    symbol_state.sessions, symbol_state.ordered_dates, date, interval
                )
            else:
                values = breakout_features(
                    session, interval, interval_index,
                    symbol_state.sessions, symbol_state.ordered_dates, date,
                    prior_intervals=symbol_state.intervals,
                )
            rendered = feature_dictionary(values or {})
            if rendered is None:
                _record_signal(
                    signals, spec, variant, symbol_state.symbol, date, clock,
                    "NO_TRADE", None, "DATA_MISSING",
                )
                continue
            if variant.removal is None:
                result = _invoke_frozen_signal(
                    evaluate, spec, variant, symbol_state.symbol, rendered
                )
                action, score, reason = result
            else:
                action, score, reason = _evaluate_removal(
                    family, rendered, variant.removal, variant.parameters
                )
            _record_signal(
                signals, spec, variant, symbol_state.symbol, date, clock,
                action, score, reason,
            )
            if action == "NO_TRADE":
                continue
            entry_price = _minute_open(session, decision_et)
            if entry_price is None:
                _record_signal(
                    signals, spec, variant, symbol_state.symbol, date, clock,
                    "NO_TRADE", None, "EXECUTION_PROXY_UNAVAILABLE",
                )
                continue
            entered = True
            entry_time = decision_et.ceil("min")
            trade = _replay_exit(
                session=session,
                intervals=intervals,
                candidate_id=spec.candidate_id,
                symbol=symbol_state.symbol,
                variant_id=variant.variant_id,
                session_date=date,
                decision_time=clock,
                action=action,
                entry_time=entry_time,
                entry_price=entry_price,
                exit_minutes=exit_minutes,
            )
            trades.append(trade)
    return signals, trades


def _invoke_frozen_signal(
    evaluate: Callable[..., Any],
    spec: CandidateSpec,
    variant: Variant,
    symbol: str,
    features: Mapping[str, Decimal],
) -> tuple[str, Decimal | None, str]:
    try:
        result = evaluate(
            underlying=symbol, features=features, **_signal_kwargs(spec, variant)
        )
    except ArithmeticError:
        # A declared diagnostic parameter can be degenerate (a zero
        # continuation-ratio threshold makes the frozen score division
        # undefined); the decision fails closed with a declared reason
        # instead of aborting the whole deterministic replay.
        return "NO_TRADE", None, "SIGNAL_EVALUATION_UNDEFINED"
    action = str(result.action)
    score = result.score
    reason = result.reason_codes[-1] if result.reason_codes else "NO_SIGNAL"
    return action, score, reason


def _signal_kwargs(spec: CandidateSpec, variant: Variant) -> dict[str, Decimal]:
    parameters = variant.parameters
    if spec.candidate_id.startswith("gap_"):
        return {
            "gap_z_threshold": Decimal(str(parameters.get("gap_z_threshold", "1.00"))),
            "continuation_ratio_threshold": Decimal(
                str(parameters.get("continuation_ratio_threshold", "0.25"))
            ),
            "gap_floor": Decimal(str(parameters.get("gap_floor", "0.000001"))),
        }
    return {
        "break_fraction_threshold": Decimal(str(parameters.get("break_fraction_threshold", "0.10"))),
        "volume_ratio_threshold": Decimal(str(parameters.get("volume_ratio_threshold", "1.25"))),
        "range_floor": Decimal(str(parameters.get("range_floor", "0.000001"))),
    }


def _record_signal(
    signals: list[dict[str, Any]],
    spec: CandidateSpec,
    variant: Variant,
    symbol: str,
    date: str,
    clock: str,
    action: str,
    score: Decimal | None,
    reason: str,
) -> None:
    signals.append(
        {
            "candidate_id": spec.candidate_id,
            "symbol": symbol,
            "decision_time": f"{date}T{clock}-04:00",
            "variant_id": variant.variant_id,
            "action": action,
            "score": "" if score is None else str(score),
            "reason_code": reason,
        }
    )


def _replay_exit(
    *,
    session: SessionBars,
    intervals: pd.DataFrame,
    candidate_id: str,
    symbol: str,
    variant_id: str,
    session_date: str,
    decision_time: str,
    action: str,
    entry_time: pd.Timestamp,
    entry_price: float,
    exit_minutes: int,
) -> dict[str, Any]:
    """Replay ``TREND_VWAP_OR_60M_V1`` for one filled underlying entry."""
    deadline = entry_time + pd.Timedelta(minutes=exit_minutes)
    cap = _session_timestamp(session_date, _CENTRAL_EXIT_CAP)
    if deadline > cap:
        deadline = cap
    evaluations = intervals[
        (intervals["interval_end"] + pd.Timedelta(seconds=1) > entry_time)
        & intervals["complete"]
    ]
    exit_time: pd.Timestamp | None = None
    exit_reason = "TIME_EXIT"
    for _, interval in evaluations.iterrows():
        evaluation_time = interval["interval_end"] + pd.Timedelta(seconds=1)
        if evaluation_time >= deadline:
            break
        close = float(interval["close"])
        session_vwap = float(interval["session_vwap"])
        adverse = close <= session_vwap if action == "BUY" else close >= session_vwap
        if adverse:
            exit_time = evaluation_time
            exit_reason = "TREND_VWAP_CROSS"
            break
    if exit_time is None:
        exit_time = deadline
    exit_price = _minute_open(session, exit_time)
    missing_exit = exit_price is None
    if missing_exit:
        final = session.frame.iloc[-1]
        exit_price = float(final["close"])
        exit_time = pd.Timestamp(final["et"])
    trade_return = (exit_price - entry_price) / entry_price if action == "BUY" else (
        entry_price - exit_price
    ) / entry_price
    return {
        "candidate_id": candidate_id,
        "symbol": symbol,
        "variant_id": variant_id,
        "session_date": session_date,
        "decision_time": decision_time,
        "action": action,
        "entry_time": str(entry_time),
        "entry_price": entry_price,
        "exit_time": str(exit_time),
        "exit_price": float(exit_price),
        "exit_reason": exit_reason,
        "trade_return": float(trade_return),
        "missing_exit": bool(missing_exit),
    }


def daily_return_frame(
    trades: Sequence[Mapping[str, Any]],
    calendar_dates: Sequence[str],
    *,
    candidate_id: str,
    variant_id: str,
) -> pd.DataFrame:
    """Complete-market-date daily returns with explicit zero dates.

    Each closed trade compounds its signed underlying forward return into the
    session equity: ``E_d = E_{d-1} * (1 + r_trade)`` for trades exiting on
    ``d``.  This is the underlying diagnostic aggregation labeled in
    ``limitations.md``; option-proxy P&L remains steward-gated.
    """
    by_date: dict[str, float] = {}
    for trade in trades:
        if bool(trade.get("missing_exit")):
            continue
        factor = 1.0 + float(trade["trade_return"])
        by_date[trade["session_date"]] = by_date.get(trade["session_date"], 1.0) * factor
    equity = _EQUITY_START
    rows = []
    for date in calendar_dates:
        factor = by_date.get(date, 1.0)
        equity *= factor
        rows.append(
            {
                "candidate_id": candidate_id,
                "variant_id": variant_id,
                "session_date": date,
                "equity": equity,
                "daily_return": factor - 1.0,
            }
        )
    return pd.DataFrame(rows)


def fold_metric_rows(
    trades: Sequence[Mapping[str, Any]],
    calendar_dates: Sequence[str],
    *,
    candidate_id: str,
    variant_id: str,
) -> list[dict[str, Any]]:
    """Per-fold OOS counts and net returns over the frozen 2025 quarters."""
    rows: list[dict[str, Any]] = []
    for fold_id, start, end in _OOS_FOLDS:
        dates = [date for date in calendar_dates if start <= date <= end]
        fold_trades = [
            trade for trade in trades if start <= str(trade["session_date"]) <= end
        ]
        positive = sum(1 for trade in fold_trades if float(trade["trade_return"]) > 0)
        net = 1.0
        for trade in fold_trades:
            net *= 1.0 + float(trade["trade_return"])
        rows.append(
            {
                "candidate_id": candidate_id,
                "variant_id": variant_id,
                "fold_id": fold_id,
                "sessions": len(dates),
                "trades": len(fold_trades),
                "positive_trades": positive,
                "hit_rate": (positive / len(fold_trades)) if fold_trades else None,
                "net_return": net - 1.0,
            }
        )
    return rows


def _decimal_string(value: float | None) -> str | None:
    if value is None or not math.isfinite(value):
        return None
    return f"{value:.10f}"


def compute_metrics(daily: pd.DataFrame) -> dict[str, Any]:
    """Section 12 metric authority over one complete-date daily frame."""
    returns = daily["daily_return"].to_numpy(dtype=float)
    equity = daily["equity"].to_numpy(dtype=float)
    if len(returns) < 2:
        return {"status": "INSUFFICIENT_DATES", "sessions": int(len(returns))}
    mean = float(np.mean(returns))
    std = float(np.std(returns, ddof=1))
    sharpe = math.sqrt(_ANNUALIZATION) * mean / std if std > 0 else None
    downside = math.sqrt(float(np.mean(np.minimum(returns, 0.0) ** 2)))
    sortino = math.sqrt(_ANNUALIZATION) * mean / downside if downside > 0 else None
    running_max = np.maximum.accumulate(equity)
    drawdowns = equity / running_max - 1.0
    max_drawdown = float(np.min(drawdowns))
    total_return = float(equity[-1] / _EQUITY_START - 1.0)
    sessions = len(returns)
    years = sessions / _ANNUALIZATION
    annualized = (equity[-1] / _EQUITY_START) ** (1.0 / years) - 1.0 if years > 0 else None
    calmar = (
        annualized / abs(max_drawdown)
        if annualized is not None and max_drawdown < 0
        else None
    )
    quantile = float(np.quantile(returns, 0.05))
    tail = returns[returns <= quantile]
    expected_shortfall = -float(np.mean(tail)) if len(tail) else None
    return {
        "status": "OK",
        "sessions": int(sessions),
        "total_return": _decimal_string(total_return),
        "annualized_return": _decimal_string(annualized),
        "sharpe": _decimal_string(sharpe),
        "sortino": _decimal_string(sortino),
        "calmar": _decimal_string(calmar),
        "max_drawdown": _decimal_string(max_drawdown),
        "expected_shortfall_95": _decimal_string(expected_shortfall),
        "worst_day": _decimal_string(float(np.min(returns))),
        "best_day": _decimal_string(float(np.max(returns))),
    }


def trade_diagnostics(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Hit rate and top-trade/day concentration over closed trades."""
    closed = [trade for trade in trades if not bool(trade.get("missing_exit"))]
    if not closed:
        return {"status": "NO_CLOSED_TRADES"}
    positives = [trade for trade in closed if float(trade["trade_return"]) > 0]
    positive_sum = sum(float(trade["trade_return"]) for trade in positives)
    by_date: dict[str, float] = {}
    for trade in positives:
        by_date[str(trade["session_date"])] = (
            by_date.get(str(trade["session_date"]), 0.0) + float(trade["trade_return"])
        )
    top_trade = max(float(trade["trade_return"]) for trade in positives) if positives else None
    top_day = max(by_date.values()) if by_date else None
    return {
        "status": "OK",
        "closed_trades": len(closed),
        "hit_rate": _decimal_string(len(positives) / len(closed)),
        "top_trade_concentration": (
            _decimal_string(top_trade / positive_sum) if positive_sum > 0 and top_trade else None
        ),
        "top_day_concentration": (
            _decimal_string(top_day / positive_sum) if positive_sum > 0 and top_day else None
        ),
        "missing_exit_count": sum(1 for trade in trades if bool(trade.get("missing_exit"))),
    }


def synchronized_bootstrap(
    daily_by_key: Mapping[str, pd.Series],
    *,
    reps: int = _BOOTSTRAP_REPS,
    block: int = _BOOTSTRAP_BLOCK,
    seed: int = _BOOTSTRAP_SEED,
) -> dict[str, dict[str, Any]]:
    """Centered circular moving-block bootstrap with family-wise max statistic.

    Block starts are drawn once per replication over the shared sorted date
    index so every candidate receives identical sampled date blocks (plan
    section 11).  The statistic is the mean daily return; the family-wise
    adjustment takes, per replication, the maximum statistic across all keys.
    """
    keys = sorted(daily_by_key)
    if not keys:
        return {}
    frames = {key: daily_by_key[key].sort_index() for key in keys}
    dates = sorted(
        set().union(*(set(frame.index) for frame in frames.values()))
    )
    matrix = np.zeros((len(keys), len(dates)), dtype=float)
    for row, key in enumerate(keys):
        frame = frames[key].reindex(dates).fillna(0.0)
        matrix[row] = frame.to_numpy(dtype=float)
    centered = matrix - matrix.mean(axis=1, keepdims=True)
    observed = matrix.mean(axis=1)
    generator = np.random.Generator(np.random.PCG64(seed))
    n_dates = len(dates)
    exceedances = np.zeros(len(keys), dtype=np.int64)
    draws = int(math.ceil(n_dates / block))
    offsets = np.arange(block)
    for _ in range(reps):
        starts = generator.integers(0, n_dates, size=draws)
        blocks = (starts[:, None] + offsets[None, :]) % n_dates
        positions = blocks.reshape(-1)[:n_dates]
        statistic = centered[:, positions].mean(axis=1)
        family_max = statistic.max()
        exceedances += statistic >= family_max - 1e-15
    report: dict[str, dict[str, Any]] = {}
    for row, key in enumerate(keys):
        report[key] = {
            "observed_mean": _decimal_string(float(observed[row])),
            "familywise_one_sided_p": _decimal_string(float(exceedances[row] / reps)),
            "reps": int(reps),
            "block_sessions": int(block),
            "seed": int(seed),
        }
    return report


def write_run_artifacts(
    *,
    output: Path,
    spec: CandidateSpec,
    signals: Sequence[Mapping[str, Any]],
    trades: Sequence[Mapping[str, Any]],
    daily: pd.DataFrame,
    fold_rows: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, Any],
    diagnostics: Mapping[str, Any],
    bootstrap: Mapping[str, Any],
    plugin_content_hash: str,
    run_parameters: Mapping[str, Any],
) -> Path:
    """Write the frozen per-run artifact tree for one candidate/cell replay."""
    output.mkdir(parents=True, exist_ok=True)
    signal_rows = [dict(row) for row in signals]
    trade_rows = [dict(row) for row in trades]
    signal_rows.sort(key=lambda row: (row["candidate_id"], row["symbol"], row["decision_time"], row["variant_id"]))
    trade_rows.sort(key=lambda row: (row["candidate_id"], row["symbol"], row["session_date"], row["variant_id"]))
    write_parquet(output, "signals", signal_rows, _SIGNAL_COLUMNS)
    write_parquet(output, "trades", trade_rows, _TRADE_COLUMNS)
    write_parquet(
        output,
        "daily_returns",
        daily.to_dict(orient="records"),
        ("candidate_id", "variant_id", "session_date", "equity", "daily_return"),
    )
    write_parquet(output, "fold_metrics", [dict(row) for row in fold_rows], _FOLD_COLUMNS)
    manifest = {
        "schema_version": "group-b-pair-cell-run/v1",
        "candidate_id": spec.candidate_id,
        "plugin_id": spec.plugin_id,
        "central_config_hash": spec.config_hash,
        "sensitivities_hash": spec.sensitivities_hash,
        "plugin_content_hash": plugin_content_hash,
        "engine_module_hash": _lf_hash(Path(__file__).with_name("group_b_pair_cell.py")),
        "features_module_hash": _lf_hash(Path(__file__).with_name("group_b_features.py")),
        "run_parameters": dict(run_parameters),
        "bootstrap": dict(bootstrap),
        "metrics": dict(metrics),
        "trade_diagnostics": dict(diagnostics),
        "cost_stress": {
            "applied": False,
            "reason": "OPTION_PROXY_NOT_RUN_WITHOUT_STEWARD_OBSERVATIONS",
        },
        "split_adjustment_audit": {
            "applied": False,
            "reason": "SYNTHETIC_OR_STEWARD_GATED_BASIS_ONLY",
        },
    }
    atomic_json(output / "run_manifest.json", manifest)
    limitations = (
        "# Limitations\n\n"
        "- Underlying diagnostic only: option-proxy P&L requires steward option "
        "observations and remains fail-closed.\n"
        "- Trade returns are signed underlying forward returns on next-minute-open "
        "execution proxies, not fills.\n"
        "- Pair evidence is diagnostic; no SMH/SOXL winner is selected and no "
        "central arbitration is applied.\n"
        "- Cost stresses and the O2 debit-vertical layer are recorded as not "
        "applied without steward artifacts.\n"
    )
    (output / "limitations.md").write_text(limitations, encoding="utf-8")
    return output


def run_pair_cell(
    *,
    repo_root: Path,
    bars: pd.DataFrame,
    candidate_id: str,
    symbol: str,
    start_date: str,
    end_date: str,
    output: Path,
    variant_filter: Sequence[str] | None = None,
    bootstrap_reps: int = _BOOTSTRAP_REPS,
) -> Path:
    """Run every enumerated variant for one candidate/symbol pair cell."""
    spec = load_candidate_spec(repo_root, candidate_id)
    evaluate, plugin_content_hash = plugin_signal(repo_root, spec.plugin_id)
    variants = enumerate_variants(spec)
    if variant_filter:
        wanted = set(variant_filter)
        variants = [variant for variant in variants if variant.variant_id in wanted]
        if not variants:
            raise GroupBPairCellError("VARIANT_FILTER_MATCHED_NOTHING")
    state = build_symbol_sessions(bars, symbol)
    calendar = [date for date in state.ordered_dates if start_date <= date <= end_date]
    if not calendar:
        raise GroupBPairCellError("SESSION_CALENDAR_EMPTY_IN_RANGE")
    all_signals: list[dict[str, Any]] = []
    all_trades: list[dict[str, Any]] = []
    daily_by_variant: dict[str, pd.Series] = {}
    fold_rows: list[dict[str, Any]] = []
    variant_metrics: dict[str, Any] = {}
    for variant in variants:
        signals, trades = replay_variant(
            spec=spec,
            variant=variant,
            symbol_state=state,
            evaluate=evaluate,
            start_date=start_date,
            end_date=end_date,
        )
        all_signals.extend(signals)
        all_trades.extend(trades)
        daily = daily_return_frame(
            trades, calendar, candidate_id=spec.candidate_id, variant_id=variant.variant_id
        )
        series = pd.Series(
            daily["daily_return"].to_numpy(dtype=float),
            index=pd.Index(daily["session_date"].tolist(), name="session_date"),
        )
        daily_by_variant[variant.variant_id] = series
        fold_rows.extend(
            fold_metric_rows(
                trades, calendar, candidate_id=spec.candidate_id, variant_id=variant.variant_id
            )
        )
        variant_metrics[variant.variant_id] = compute_metrics(daily)
    bootstrap = synchronized_bootstrap(daily_by_variant, reps=bootstrap_reps)
    central = daily_by_variant.get("central")
    central_daily = (
        daily_return_frame(
            [trade for trade in all_trades if trade["variant_id"] == "central"],
            calendar,
            candidate_id=spec.candidate_id,
            variant_id="central",
        )
        if central is not None
        else pd.DataFrame()
    )
    central_trades = [trade for trade in all_trades if trade["variant_id"] == "central"]
    return write_run_artifacts(
        output=output,
        spec=spec,
        signals=all_signals,
        trades=all_trades,
        daily=central_daily,
        fold_rows=fold_rows,
        metrics=variant_metrics.get("central", {"status": "NO_CENTRAL_VARIANT"}),
        diagnostics=trade_diagnostics(central_trades),
        bootstrap=bootstrap,
        plugin_content_hash=plugin_content_hash,
        run_parameters={
            "candidate_id": spec.candidate_id,
            "symbol": symbol,
            "start_date": start_date,
            "end_date": end_date,
            "variants": [variant.variant_id for variant in variants],
            "bootstrap_reps": int(bootstrap_reps),
            "engine": "group_b_pair_cell/v1",
        },
    )


def _validate_steward_gates(data_manifest_path: Path, feasibility_manifest_path: Path) -> dict[str, Any]:
    """Fail-closed B0 validation of the attested steward artifacts."""
    if not data_manifest_path.is_file():
        raise GroupBPairCellError(f"DATA_MANIFEST_MISSING_{data_manifest_path}")
    if not feasibility_manifest_path.is_file():
        raise GroupBPairCellError(f"FEASIBILITY_MANIFEST_MISSING_{feasibility_manifest_path}")
    try:
        data_doc = json.loads(data_manifest_path.read_text(encoding="utf-8"))
        feasibility_doc = json.loads(feasibility_manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GroupBPairCellError("STEWARD_MANIFEST_INVALID_JSON") from exc
    if data_doc.get("status") != "COLLECTED":
        raise GroupBPairCellError(f"DATA_MANIFEST_NOT_COLLECTED_{data_doc.get('status')}")
    if feasibility_doc.get("status") != "READY_FOR_REPLAY":
        raise GroupBPairCellError(
            f"FEASIBILITY_MANIFEST_NOT_READY_{feasibility_doc.get('status')}"
        )
    symbols = data_doc.get("symbols")
    if not isinstance(symbols, list) or any(item not in symbols for item in _PAIR_SYMBOLS):
        raise GroupBPairCellError("DATA_MANIFEST_PAIR_SYMBOLS_MISSING")
    return {"data": data_doc, "feasibility": feasibility_doc}


def main(argv: Sequence[str] | None = None) -> int:
    """Run one pair-cell replay behind the fail-closed steward gates."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-manifest", required=True, type=Path)
    parser.add_argument("--feasibility-manifest", required=True, type=Path)
    parser.add_argument("--candidate", required=True, choices=_CANDIDATES)
    parser.add_argument("--symbol", required=True, choices=_PAIR_SYMBOLS)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    ensure_empty_output(args.output)
    try:
        _validate_steward_gates(args.data_manifest, args.feasibility_manifest)
        raise GroupBPairCellError("OUTCOME_RUNS_BLOCKED_UNTIL_STEWARD_DATASETS_PUBLISHED")
    except GroupBPairCellError as exc:
        atomic_json(
            args.output / "pair_cell_refusal.json",
            {"status": "REFUSED", "reason": str(exc)},
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
