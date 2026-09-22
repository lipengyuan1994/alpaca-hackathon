import json

import pandas as pd
import pytest

from packages.contracts.canonical import canonical_hash
from packages.etf_cash_research.corrections_v2 import correct_saved_statistics
from packages.etf_cash_research.evaluation import chain_evaluation_windows, pooled_paired_bootstrap
from packages.etf_cash_research.metrics import compute_metrics
from packages.etf_cash_research.protocol_v2 import DEFAULT_STUDY_PROTOCOL
from packages.etf_cash_research.simulator_v2 import _intersection_sessions
from packages.etf_cash_research.study_v2 import load_v2_bars_from_manifest, rank_study


def test_chained_drawdown_carries_losses_across_account_resets():
    frame = pd.DataFrame({"window_id": ["W1", "W1", "W2", "W2"], "date": pd.date_range("2024-01-01", periods=4), "equity": [1000., 800., 900., 800.]})
    result = chain_evaluation_windows(frame, ["W1", "W2"])
    assert result.evaluation_index.tolist() == pytest.approx([1000, 800, 720, 640])
    assert result.drawdown.max() == pytest.approx(.36)  # each account loses only 20%


def test_internal_missing_session_is_not_silently_removed():
    frame = pd.DataFrame({"symbol": ["A", "A", "A", "B", "B"], "date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-02", "2024-01-04"], utc=True)})
    with pytest.raises(ValueError, match="INTERNAL_SESSION_GAP"):
        _intersection_sessions(frame, ("A", "B"))


def test_correction_rejects_existing_output(tmp_path):
    with pytest.raises(ValueError, match="OUTPUT_EXISTS"):
        correct_saved_statistics(tmp_path / "absent_source", tmp_path)


def test_saved_input_hash_mismatch_rejected_before_reading_prices(tmp_path):
    (tmp_path / "raw.parquet").write_bytes(b"altered input")
    manifest = {"schema_version": "etf-cash-data-manifest/v1", "status": "COLLECTED", "datasets": [{"dataset_id": "stock_bars_raw", "feed": "sip", "artifact": {"path": "raw.parquet", "sha256": "sha256:wrong"}}]}
    manifest["manifest_hash"] = canonical_hash(manifest)
    path = tmp_path / "data_manifest.json"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="RAW_BARS_HASH_MISMATCH"):
        load_v2_bars_from_manifest(path)


def test_first_day_loss_and_missing_window():
    frame = pd.DataFrame({"window_id": ["W1"], "date": ["2024-01-02"], "equity": [990.]})
    assert chain_evaluation_windows(frame, ["W1"]).drawdown.iloc[0] == pytest.approx(.01)
    with pytest.raises(ValueError, match="COVERAGE_INCOMPLETE"):
        chain_evaluation_windows(frame, ["W1", "W2"])


def test_bootstrap_preserves_window_boundaries_and_pairs():
    strategy = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=4), "window_id": ["W1", "W1", "W2", "W2"], "daily_return": [.1, .1, -.1, -.1]})
    benchmark = strategy.assign(daily_return=0.)
    result = pooled_paired_bootstrap(strategy, benchmark, samples=100, block_length=1)
    assert result["confidence_level"] == .95
    assert result["return_gap_quantiles"]["p025"] == pytest.approx(1.1 ** 2 * .9 ** 2 - 1)
    assert result["return_gap_quantiles"]["p975"] == pytest.approx(1.1 ** 2 * .9 ** 2 - 1)


def test_metrics_first_day_risk_and_real_win_rate():
    equity = pd.DataFrame({"date": ["2024-01-02", "2024-01-03"], "equity": [900., 950.]})
    trades = pd.DataFrame({"gross_pnl": [20., -10., 0.], "fees": [0., 0., 0.], "exit_date": ["2024-01-03"] * 3})
    result = compute_metrics(equity, pd.DataFrame(), initial_cash=1000, start="2024-01-02", end="2024-01-03", trades=trades)
    assert result["starting_equity"] == 1000
    assert result["max_drawdown"] == pytest.approx(.10)
    assert result["win_rate"] == pytest.approx(1 / 3)


def test_ranking_rejects_cross_window_drawdown_hidden_by_resets(tmp_path):
    cid = "A01__QQQM_SMH__primary"
    rows, daily = [], []
    common = {"candidate_id": cid, "track_id": "a", "pair_id": "QQQM_SMH", "turnover": 10, "starting_equity": 1000}
    for cost in ("base", "stress"):
        for i, (window, start, _) in enumerate(DEFAULT_STUDY_PROTOCOL.evaluation_windows):
            marks = [2000, 1500] if i == 0 else [800, 750] if i == 1 else [1050, 1100]
            rows.append({**common, "cost_scenario": cost, "phase": "evaluation", "window_id": window, "net_return": marks[-1] / 1000 - 1, "max_drawdown": .25})
            for offset, mark in enumerate(marks):
                daily.append({**common, "cost_scenario": cost, "phase": "evaluation", "window_id": window, "date": pd.Timestamp(start) + pd.Timedelta(days=offset), "equity": mark})
        rows.append({**common, "cost_scenario": cost, "phase": "continuous", "window_id": None, "net_return": .5, "max_drawdown": .1})
        rows.append({**common, "cost_scenario": cost, "phase": "stress", "window_id": "stress_2022", "net_return": .1, "max_drawdown": .1})
    (tmp_path / "normalized").mkdir()
    pd.DataFrame(rows).to_csv(tmp_path / "leaderboard.csv", index=False)
    pd.DataFrame(daily).to_parquet(tmp_path / "normalized/equity_daily.parquet")
    (tmp_path / "data_quality.json").write_text(json.dumps({"missing_dividend_payable_dates_in_study": 0}))
    rank_study(tmp_path)
    selected = pd.read_csv(tmp_path / "selection_leaderboard.csv").iloc[0]
    assert selected.evaluation_drawdown == pytest.approx(.4375)
    assert not selected.qualifies
    assert "evaluation_base_drawdown_exceeds_ceiling" in selected.qualification_reasons
