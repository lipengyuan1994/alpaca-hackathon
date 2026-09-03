"""Synthetic-fixture tests for the Group B pair-cell engine.

Every scenario here is hand-constructed minute-bar data with known arithmetic:
uniform volume and ``vwap == price`` so interval and session VWAPs are plain
means, per-date log gaps that make sigma and z deterministic, and drift paths
tuned so gate decisions carry wide margins.  The tests prove engine mechanics
(variant enumeration, next-observation execution, ``TREND_VWAP_OR_60M_V1``
exits, metric authority, synchronized bootstrap, artifact determinism, and
fail-closed refusals) without viewing any real outcome.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Callable, Mapping

import numpy as np
import pandas as pd
import pytest

from packages.research_data import group_b_features as features
from packages.research_data import group_b_pair_cell as engine

REPO_ROOT = Path(__file__).resolve().parents[2]
_ET = "America/New_York"
_GAP_CANDIDATE = "gap_continuation__all_feasible__o2_v1"
_ORB_CANDIDATE = "opening_range_breakout__all_feasible__o2_v1"


def _dates(count: int, start: str = "2025-03-10") -> list[str]:
    index = pd.bdate_range(start, periods=count)
    return [stamp.date().isoformat() for stamp in index]


GAP_DATES = _dates(72)
GAP_TARGET = GAP_DATES[-1]
GAP_RANGE_START = GAP_DATES[-6]
ORB_DATES = _dates(25)
ORB_TARGET = ORB_DATES[-1]
ORB_RANGE_START = ORB_DATES[-5]


def _drift_path(drift: float) -> Callable[[int], float]:
    def path(minute: int) -> float:
        return drift * minute / 389.0

    return path


def _peak_decline(minute: int) -> float:
    if minute <= 60:
        return 0.01 * minute / 60.0
    return 0.01 - 0.04 * (minute - 60) / 329.0


_gap_drift = _drift_path(0.03)
_weak_drift = _drift_path(0.001)
_orb_drift = _drift_path(0.02)


def _synthetic_bars(
    symbol: str,
    dates: list[str],
    *,
    gap_by_date: Mapping[str, float] | None = None,
    drift_by_date: Mapping[str, float] | None = None,
    volume_by_date: Mapping[str, float] | None = None,
) -> pd.DataFrame:
    """Build full 390-minute sessions anchored to 09:30 ET regardless of DST.

    Prices follow ``base * exp(path(minute))`` with ``path(0) == 0`` so the
    realized log gap equals ``gap_by_date[date]`` exactly; volume is uniform
    per session so interval/session VWAPs are plain price means.
    """
    gaps = gap_by_date or {}
    drifts = drift_by_date or {}
    volumes = volume_by_date or {}
    base = 100.0
    rows: list[dict[str, object]] = []
    for date in dates:
        base *= math.exp(gaps.get(date, 0.0))
        path = _drift_path(drifts.get(date, 0.0))
        volume = volumes.get(date, 1000.0)
        session_open = pd.Timestamp(f"{date} 09:30:00", tz=_ET).tz_convert("UTC")
        for minute in range(390):
            price = base * math.exp(path(minute))
            rows.append(
                {
                    "symbol": symbol,
                    "event_time": session_open + pd.Timedelta(minutes=minute),
                    "open": price,
                    "high": price * 1.0001,
                    "low": price * 0.9999,
                    "close": price,
                    "volume": volume,
                    "vwap": price,
                }
            )
    return pd.DataFrame(rows)


def _rewritten_session(
    state: engine.SymbolSessions,
    target: str,
    *,
    path: Callable[[int], float] | None = None,
    volume: float | None = None,
    minutes: int | None = None,
    zero_volume_through: int | None = None,
) -> engine.SymbolSessions:
    """Replace only the target day's session inside a shallow-copied state."""
    base = float(state.sessions[target].frame["open"].iloc[0])
    count = 390 if minutes is None else minutes
    rows: list[dict[str, object]] = []
    session_open = pd.Timestamp(f"{target} 09:30:00", tz=_ET)
    for minute in range(count):
        price = base * math.exp(path(minute)) if path is not None else base
        bar_volume = 1000.0 if volume is None else volume
        if zero_volume_through is not None and minute <= zero_volume_through:
            bar_volume = 0.0
        rows.append(
            {
                "symbol": state.symbol,
                "event_time": session_open + pd.Timedelta(minutes=minute),
                "open": price,
                "high": price * 1.0001,
                "low": price * 0.9999,
                "close": price,
                "volume": bar_volume,
                "vwap": price,
            }
        )
    frame = pd.DataFrame(rows)
    frame["et"] = frame["event_time"].dt.tz_convert(_ET)
    session = features.SessionBars(
        symbol=state.symbol,
        date=target,
        frame=frame,
        early_close=count < 390,
    )
    sessions = dict(state.sessions)
    sessions[target] = session
    intervals = dict(state.intervals)
    intervals[target] = features.aggregate_intervals(session)
    return engine.SymbolSessions(
        symbol=state.symbol,
        sessions=sessions,
        ordered_dates=list(state.ordered_dates),
        intervals=intervals,
    )


def _central(spec: engine.CandidateSpec) -> engine.Variant:
    return engine.Variant("central", "central", dict(spec.central))


def _falsify(spec: engine.CandidateSpec, removal: str) -> engine.Variant:
    return engine.Variant(
        f"falsify_remove_{removal}", "falsification", dict(spec.central), removal
    )


@pytest.fixture(scope="module")
def gap_bars() -> pd.DataFrame:
    gaps = {
        date: 0.002 * (1.0 if position % 2 == 0 else -1.0)
        for position, date in enumerate(GAP_DATES)
        if position > 0
    }
    gaps[GAP_TARGET] = 0.012
    return _synthetic_bars(
        "SMH", GAP_DATES, gap_by_date=gaps, drift_by_date={GAP_TARGET: 0.03}
    )


@pytest.fixture(scope="module")
def orb_bars() -> pd.DataFrame:
    return _synthetic_bars(
        "SOXL",
        ORB_DATES,
        drift_by_date={ORB_TARGET: 0.02},
        volume_by_date={ORB_TARGET: 2000.0},
    )


@pytest.fixture(scope="module")
def gap_state(gap_bars: pd.DataFrame) -> engine.SymbolSessions:
    return engine.build_symbol_sessions(gap_bars, "SMH")


@pytest.fixture(scope="module")
def orb_state(orb_bars: pd.DataFrame) -> engine.SymbolSessions:
    return engine.build_symbol_sessions(orb_bars, "SOXL")


@pytest.fixture(scope="module")
def gap_spec() -> engine.CandidateSpec:
    return engine.load_candidate_spec(REPO_ROOT, _GAP_CANDIDATE)


@pytest.fixture(scope="module")
def orb_spec() -> engine.CandidateSpec:
    return engine.load_candidate_spec(REPO_ROOT, _ORB_CANDIDATE)


@pytest.fixture(scope="module")
def gap_evaluate(gap_spec: engine.CandidateSpec):
    evaluate, content_hash = engine.plugin_signal(REPO_ROOT, gap_spec.plugin_id)
    assert content_hash.startswith("sha256:")
    return evaluate


@pytest.fixture(scope="module")
def orb_evaluate(orb_spec: engine.CandidateSpec):
    evaluate, content_hash = engine.plugin_signal(REPO_ROOT, orb_spec.plugin_id)
    assert content_hash.startswith("sha256:")
    return evaluate


@pytest.fixture(scope="module")
def gap_run(tmp_path_factory, gap_bars: pd.DataFrame) -> Path:
    output = tmp_path_factory.mktemp("gap-run")
    return engine.run_pair_cell(
        repo_root=REPO_ROOT,
        bars=gap_bars,
        candidate_id=_GAP_CANDIDATE,
        symbol="SMH",
        start_date=GAP_RANGE_START,
        end_date=GAP_TARGET,
        output=output,
        bootstrap_reps=30,
    )


@pytest.fixture(scope="module")
def gap_run_repeat(tmp_path_factory, gap_bars: pd.DataFrame) -> Path:
    output = tmp_path_factory.mktemp("gap-run-repeat")
    return engine.run_pair_cell(
        repo_root=REPO_ROOT,
        bars=gap_bars,
        candidate_id=_GAP_CANDIDATE,
        symbol="SMH",
        start_date=GAP_RANGE_START,
        end_date=GAP_TARGET,
        output=output,
        bootstrap_reps=30,
    )


def test_gap_variant_enumeration_matches_the_frozen_table(gap_spec) -> None:
    variants = engine.enumerate_variants(gap_spec)
    assert [variant.variant_id for variant in variants] == [
        "central",
        "diag_continuation_ratio_threshold_0.00",
        "diag_continuation_ratio_threshold_0.50",
        "diag_gap_z_threshold_0.75",
        "diag_gap_z_threshold_1.25",
        "diag_time_exit_minutes_45",
        "diag_time_exit_minutes_90",
        "falsify_remove_continuation",
        "falsify_remove_session_vwap",
    ]
    by_id = {variant.variant_id: variant for variant in variants}
    assert by_id["central"].kind == "central"
    assert by_id["central"].removal is None
    assert by_id["central"].parameters["gap_z_threshold"] == "1.00"
    exit_45 = by_id["diag_time_exit_minutes_45"]
    assert exit_45.parameters["time_exit_minutes"] == 45
    assert isinstance(exit_45.parameters["time_exit_minutes"], int)
    falsify = by_id["falsify_remove_continuation"]
    assert falsify.kind == "falsification"
    assert falsify.removal == "continuation"


def test_orb_variant_enumeration_matches_the_frozen_table(orb_spec) -> None:
    variants = engine.enumerate_variants(orb_spec)
    assert [variant.variant_id for variant in variants] == [
        "central",
        "diag_break_fraction_threshold_0.05",
        "diag_break_fraction_threshold_0.15",
        "diag_time_exit_minutes_45",
        "diag_time_exit_minutes_90",
        "diag_volume_ratio_threshold_1.00",
        "diag_volume_ratio_threshold_1.50",
        "falsify_remove_volume_ratio",
        "falsify_remove_session_vwap",
    ]
    by_id = {variant.variant_id: variant for variant in variants}
    assert by_id["diag_volume_ratio_threshold_1.50"].parameters[
        "volume_ratio_threshold"
    ] == "1.50"
    assert by_id["falsify_remove_session_vwap"].removal == "session_vwap"


def test_unknown_candidate_refuses_with_declared_reason() -> None:
    with pytest.raises(engine.GroupBPairCellError, match="CANDIDATE_NOT_RECOGNIZED_"):
        engine.load_candidate_spec(REPO_ROOT, "momentum__all_feasible__o2_v1")


def test_gap_replay_enters_only_on_the_gap_target_session(
    gap_spec, gap_state, gap_evaluate
) -> None:
    signals, trades = engine.replay_variant(
        spec=gap_spec,
        variant=_central(gap_spec),
        symbol_state=gap_state,
        evaluate=gap_evaluate,
        start_date=GAP_RANGE_START,
        end_date=GAP_TARGET,
    )
    assert len(signals) == 6
    refusals = [row for row in signals if row["action"] == "NO_TRADE"]
    assert len(refusals) == 5
    assert all(
        row["reason_code"] == "GAP_CONTINUATION_GATE_NOT_MET" for row in refusals
    )
    entries = [row for row in signals if row["action"] == "BUY"]
    assert len(entries) == 1
    assert entries[0]["reason_code"] == "GAP_CONTINUATION_BULLISH"
    assert entries[0]["decision_time"] == f"{GAP_TARGET}T10:30:01-04:00"
    assert float(entries[0]["score"]) > 0
    assert len(trades) == 1
    trade = trades[0]
    assert trade["session_date"] == GAP_TARGET
    assert trade["action"] == "BUY"
    assert "10:31:00" in trade["entry_time"]
    assert trade["exit_reason"] == "TIME_EXIT"
    assert "11:31:00" in trade["exit_time"]
    assert trade["trade_return"] > 0
    assert trade["missing_exit"] is False


def test_gap_exit_replays_the_adverse_vwap_cross(
    gap_spec, gap_state, gap_evaluate
) -> None:
    declined = _rewritten_session(gap_state, GAP_TARGET, path=_peak_decline)
    signals, trades = engine.replay_variant(
        spec=gap_spec,
        variant=_central(gap_spec),
        symbol_state=declined,
        evaluate=gap_evaluate,
        start_date=GAP_TARGET,
        end_date=GAP_TARGET,
    )
    assert len(signals) == 1
    assert signals[0]["action"] == "BUY"
    assert len(trades) == 1
    trade = trades[0]
    assert trade["exit_reason"] == "TREND_VWAP_CROSS"
    assert "11:15:01" in trade["exit_time"]
    assert trade["trade_return"] < 0
    assert trade["missing_exit"] is False


def test_zero_volume_morning_is_data_missing(
    gap_spec, gap_state, gap_evaluate
) -> None:
    silent = _rewritten_session(gap_state, GAP_TARGET, zero_volume_through=74)
    signals, trades = engine.replay_variant(
        spec=gap_spec,
        variant=_central(gap_spec),
        symbol_state=silent,
        evaluate=gap_evaluate,
        start_date=GAP_TARGET,
        end_date=GAP_TARGET,
    )
    assert len(signals) == 1
    assert signals[0]["action"] == "NO_TRADE"
    assert signals[0]["reason_code"] == "DATA_MISSING"
    assert trades == []


def test_short_session_is_early_close(gap_spec, gap_state, gap_evaluate) -> None:
    shortened = _rewritten_session(gap_state, GAP_TARGET, minutes=200)
    signals, trades = engine.replay_variant(
        spec=gap_spec,
        variant=_central(gap_spec),
        symbol_state=shortened,
        evaluate=gap_evaluate,
        start_date=GAP_TARGET,
        end_date=GAP_TARGET,
    )
    assert len(signals) == 1
    assert signals[0]["action"] == "NO_TRADE"
    assert signals[0]["reason_code"] == "EARLY_CLOSE_SESSION"
    assert trades == []


def test_orb_replay_uses_the_decision_grid_once_per_day(
    orb_spec, orb_state, orb_evaluate
) -> None:
    signals, trades = engine.replay_variant(
        spec=orb_spec,
        variant=_central(orb_spec),
        symbol_state=orb_state,
        evaluate=orb_evaluate,
        start_date=ORB_RANGE_START,
        end_date=ORB_TARGET,
    )
    assert len(signals) == 45
    counts = Counter(row["reason_code"] for row in signals)
    assert counts["OPENING_RANGE_BREAKOUT_GATE_NOT_MET"] == 36
    assert counts["DAILY_ENTRY_ALREADY_USED"] == 8
    buys = [row for row in signals if row["action"] == "BUY"]
    assert len(buys) == 1
    assert buys[0]["reason_code"] == "OPENING_RANGE_BREAKOUT_BULLISH"
    assert buys[0]["decision_time"] == f"{ORB_TARGET}T10:30:01-04:00"
    assert float(buys[0]["score"]) == pytest.approx(1.6, abs=1e-9)
    assert len(trades) == 1
    trade = trades[0]
    assert "10:31:00" in trade["entry_time"]
    assert trade["exit_reason"] == "TIME_EXIT"
    assert "11:31:00" in trade["exit_time"]
    assert trade["trade_return"] > 0


def test_gap_falsification_removals_flip_declared_refusals(
    gap_spec, gap_state, gap_evaluate
) -> None:
    weak = _rewritten_session(gap_state, GAP_TARGET, path=_weak_drift)
    signals, trades = engine.replay_variant(
        spec=gap_spec,
        variant=_central(gap_spec),
        symbol_state=weak,
        evaluate=gap_evaluate,
        start_date=GAP_TARGET,
        end_date=GAP_TARGET,
    )
    assert signals[0]["reason_code"] == "GAP_CONTINUATION_GATE_NOT_MET"
    assert trades == []
    signals, trades = engine.replay_variant(
        spec=gap_spec,
        variant=_falsify(gap_spec, "continuation"),
        symbol_state=weak,
        evaluate=gap_evaluate,
        start_date=GAP_TARGET,
        end_date=GAP_TARGET,
    )
    assert signals[0]["action"] == "BUY"
    assert signals[0]["reason_code"] == "FALSIFY_WITHOUT_CONTINUATION"
    assert len(trades) == 1
    signals, trades = engine.replay_variant(
        spec=gap_spec,
        variant=_falsify(gap_spec, "session_vwap"),
        symbol_state=gap_state,
        evaluate=gap_evaluate,
        start_date=GAP_TARGET,
        end_date=GAP_TARGET,
    )
    assert signals[0]["action"] == "BUY"
    assert signals[0]["reason_code"] == "FALSIFY_WITHOUT_VWAP"
    assert len(trades) == 1


def test_orb_falsification_removal_flips_the_volume_refusal(
    orb_spec, orb_state, orb_evaluate
) -> None:
    quiet = _rewritten_session(orb_state, ORB_TARGET, path=_orb_drift, volume=1000.0)
    signals, trades = engine.replay_variant(
        spec=orb_spec,
        variant=_central(orb_spec),
        symbol_state=quiet,
        evaluate=orb_evaluate,
        start_date=ORB_TARGET,
        end_date=ORB_TARGET,
    )
    assert signals[0]["action"] == "NO_TRADE"
    assert signals[0]["reason_code"] == "OPENING_RANGE_BREAKOUT_GATE_NOT_MET"
    assert trades == []
    signals, trades = engine.replay_variant(
        spec=orb_spec,
        variant=_falsify(orb_spec, "volume_ratio"),
        symbol_state=quiet,
        evaluate=orb_evaluate,
        start_date=ORB_TARGET,
        end_date=ORB_TARGET,
    )
    assert signals[0]["action"] == "BUY"
    assert signals[0]["reason_code"] == "FALSIFY_WITHOUT_VOLUME"
    assert len(trades) == 1


def test_same_time_volume_median_reuses_precomputed_interval_frames(
    orb_state,
) -> None:
    lazy = features.same_time_volume_median(
        orb_state.sessions, orb_state.ordered_dates, ORB_TARGET, 3
    )
    cached = features.same_time_volume_median(
        orb_state.sessions,
        orb_state.ordered_dates,
        ORB_TARGET,
        3,
        prior_intervals=orb_state.intervals,
    )
    assert lazy == cached == 15000.0


def test_synchronized_bootstrap_is_deterministic_and_synchronized() -> None:
    dates = [
        stamp.date().isoformat()
        for stamp in pd.bdate_range("2025-01-06", periods=10)
    ]
    first = pd.Series(0.002, index=pd.Index(dates[:8], name="session_date"))
    second = pd.Series(-0.001, index=pd.Index(dates[2:], name="session_date"))
    report_a = engine.synchronized_bootstrap(
        {"gap/SMH": first, "orb/SOXL": second}, reps=50
    )
    report_b = engine.synchronized_bootstrap(
        {"gap/SMH": first, "orb/SOXL": second}, reps=50
    )
    assert report_a == report_b
    assert set(report_a) == {"gap/SMH", "orb/SOXL"}
    for entry in report_a.values():
        assert entry["reps"] == 50
        assert entry["block_sessions"] == 5
        assert entry["seed"] == 20260829
        assert 0.0 <= float(entry["familywise_one_sided_p"]) <= 1.0
    # Missing union-calendar dates are zero-filled before the statistic (plan
    # section 11), so each observed mean divides by the shared date union.
    assert report_a["gap/SMH"]["observed_mean"] == "0.0016000000"
    assert report_a["orb/SOXL"]["observed_mean"] == "-0.0008000000"


def test_compute_metrics_matches_the_section_twelve_formulas() -> None:
    dates = [
        stamp.date().isoformat()
        for stamp in pd.bdate_range("2025-04-07", periods=4)
    ]
    trades = [
        {"session_date": dates[index], "trade_return": value, "missing_exit": False}
        for index, value in enumerate((0.01, 0.02, -0.01, 0.03))
    ]
    daily = engine.daily_return_frame(
        trades, dates, candidate_id="c", variant_id="central"
    )
    metrics = engine.compute_metrics(daily)
    assert metrics["status"] == "OK"
    assert metrics["sessions"] == 4
    assert metrics["total_return"] == "0.0504949400"
    assert metrics["worst_day"] == "-0.0100000000"
    assert metrics["best_day"] == "0.0300000000"
    assert metrics["max_drawdown"] == "-0.0100000000"
    assert metrics["expected_shortfall_95"] == "0.0100000000"
    returns = daily["daily_return"].to_numpy(dtype=float)
    sharpe = (
        math.sqrt(252.0)
        * float(np.mean(returns))
        / float(np.std(returns, ddof=1))
    )
    assert metrics["sharpe"] == f"{sharpe:.10f}"
    downside = math.sqrt(float(np.mean(np.minimum(returns, 0.0) ** 2)))
    sortino = math.sqrt(252.0) * float(np.mean(returns)) / downside
    assert metrics["sortino"] == f"{sortino:.10f}"


def test_compute_metrics_refuses_below_two_sessions() -> None:
    daily = engine.daily_return_frame(
        [{"session_date": "2025-04-07", "trade_return": 0.01, "missing_exit": False}],
        ["2025-04-07"],
        candidate_id="c",
        variant_id="central",
    )
    assert engine.compute_metrics(daily) == {
        "status": "INSUFFICIENT_DATES",
        "sessions": 1,
    }


def test_trade_diagnostics_reports_concentration_over_closed_trades() -> None:
    trades = [
        {"session_date": "2025-04-07", "trade_return": 0.01, "missing_exit": False},
        {"session_date": "2025-04-07", "trade_return": 0.02, "missing_exit": False},
        {"session_date": "2025-04-08", "trade_return": -0.01, "missing_exit": False},
        {"session_date": "2025-04-09", "trade_return": 0.05, "missing_exit": True},
    ]
    report = engine.trade_diagnostics(trades)
    assert report["status"] == "OK"
    assert report["closed_trades"] == 3
    assert report["hit_rate"] == "0.6666666667"
    assert report["top_trade_concentration"] == "0.6666666667"
    assert report["top_day_concentration"] == "1.0000000000"
    assert report["missing_exit_count"] == 1


def test_run_pair_cell_writes_the_frozen_artifact_tree(gap_run, gap_spec) -> None:
    normalized = gap_run / "normalized"
    signals = pd.read_parquet(normalized / "signals.parquet")
    trades = pd.read_parquet(normalized / "trades.parquet")
    daily = pd.read_parquet(normalized / "daily_returns.parquet")
    folds = pd.read_parquet(normalized / "fold_metrics.parquet")
    assert list(signals.columns) == list(engine._SIGNAL_COLUMNS)
    assert list(trades.columns) == list(engine._TRADE_COLUMNS)
    assert len(folds) == 36
    quarter = folds[
        (folds["variant_id"] == "central") & (folds["fold_id"] == "2025Q2")
    ]
    assert int(quarter["sessions"].iloc[0]) == 6
    assert len(trades[trades["variant_id"] == "central"]) == 1
    assert len(daily) == 6
    manifest = json.loads((gap_run / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "group-b-pair-cell-run/v1"
    assert manifest["candidate_id"] == gap_spec.candidate_id
    assert manifest["plugin_id"] == gap_spec.plugin_id
    assert manifest["metrics"]["status"] == "OK"
    assert manifest["cost_stress"] == {
        "applied": False,
        "reason": "OPTION_PROXY_NOT_RUN_WITHOUT_STEWARD_OBSERVATIONS",
    }
    assert manifest["split_adjustment_audit"]["applied"] is False
    assert manifest["engine_module_hash"].startswith("sha256:")
    assert manifest["features_module_hash"].startswith("sha256:")
    variant_ids = {
        variant.variant_id for variant in engine.enumerate_variants(gap_spec)
    }
    assert set(manifest["bootstrap"]) == variant_ids
    assert manifest["run_parameters"]["variants"].__len__() == 9
    limitations = (gap_run / "limitations.md").read_text(encoding="utf-8")
    assert "Underlying diagnostic only" in limitations


def test_run_pair_cell_is_bit_deterministic(gap_run, gap_run_repeat) -> None:
    assert (gap_run / "run_manifest.json").read_bytes() == (
        gap_run_repeat / "run_manifest.json"
    ).read_bytes()
    for name in ("signals", "trades", "daily_returns", "fold_metrics"):
        left = pd.read_parquet(gap_run / "normalized" / f"{name}.parquet")
        right = pd.read_parquet(gap_run_repeat / "normalized" / f"{name}.parquet")
        pd.testing.assert_frame_equal(left, right, check_dtype=False)


def test_run_pair_cell_refusal_gates(gap_bars, tmp_path) -> None:
    with pytest.raises(
        engine.GroupBPairCellError, match="VARIANT_FILTER_MATCHED_NOTHING"
    ):
        engine.run_pair_cell(
            repo_root=REPO_ROOT,
            bars=gap_bars,
            candidate_id=_GAP_CANDIDATE,
            symbol="SMH",
            start_date=GAP_RANGE_START,
            end_date=GAP_TARGET,
            output=tmp_path / "filter",
            variant_filter=["nonexistent"],
            bootstrap_reps=5,
        )
    short = gap_bars[gap_bars["event_time"] < pd.Timestamp("2025-03-12", tz="UTC")]
    with pytest.raises(
        engine.GroupBPairCellError, match="SESSION_CALENDAR_EMPTY_IN_RANGE"
    ):
        engine.run_pair_cell(
            repo_root=REPO_ROOT,
            bars=short,
            candidate_id=_GAP_CANDIDATE,
            symbol="SMH",
            start_date="2024-01-01",
            end_date="2024-12-31",
            output=tmp_path / "calendar",
            bootstrap_reps=5,
        )


def test_cli_fails_closed_when_steward_manifests_are_missing(tmp_path) -> None:
    output = tmp_path / "refusal"
    code = engine.main(
        [
            "--data-manifest",
            str(tmp_path / "absent-data.json"),
            "--feasibility-manifest",
            str(tmp_path / "absent-feasibility.json"),
            "--candidate",
            _GAP_CANDIDATE,
            "--symbol",
            "SMH",
            "--start-date",
            "2025-01-01",
            "--end-date",
            "2025-12-31",
            "--output",
            str(output),
        ]
    )
    assert code == 2
    doc = json.loads((output / "pair_cell_refusal.json").read_text(encoding="utf-8"))
    assert doc["status"] == "REFUSED"
    assert doc["reason"].startswith("DATA_MANIFEST_MISSING_")


def test_cli_refuses_even_with_valid_steward_gates(tmp_path) -> None:
    data = tmp_path / "data.json"
    data.write_text(
        json.dumps({"status": "COLLECTED", "symbols": ["SMH", "SOXL"]}),
        encoding="utf-8",
    )
    feasibility = tmp_path / "feasibility.json"
    feasibility.write_text(json.dumps({"status": "READY_FOR_REPLAY"}), encoding="utf-8")
    output = tmp_path / "blocked"
    code = engine.main(
        [
            "--data-manifest",
            str(data),
            "--feasibility-manifest",
            str(feasibility),
            "--candidate",
            _ORB_CANDIDATE,
            "--symbol",
            "SOXL",
            "--start-date",
            "2025-01-01",
            "--end-date",
            "2025-12-31",
            "--output",
            str(output),
        ]
    )
    assert code == 2
    doc = json.loads((output / "pair_cell_refusal.json").read_text(encoding="utf-8"))
    assert doc == {
        "status": "REFUSED",
        "reason": "OUTCOME_RUNS_BLOCKED_UNTIL_STEWARD_DATASETS_PUBLISHED",
    }
