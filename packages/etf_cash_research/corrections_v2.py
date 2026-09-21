"""Offline, versioned correction of derived statistics; never overwrite a study."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import pandas as pd

from packages.research_data.artifacts import atomic_json, file_hash

from .evaluation import pooled_paired_bootstrap
from .metrics import compute_metrics
from .protocol_v2 import DEFAULT_STUDY_PROTOCOL
from .study_v2 import _enrich_benchmark_metrics, load_v2_bars_from_manifest, rank_study


def correct_saved_statistics(source: Path, output: Path) -> None:
    if output.exists():
        raise ValueError("ETF_CORRECTION_OUTPUT_EXISTS")
    # Validate the exact saved manifest and all referenced normalized inputs.
    load_v2_bars_from_manifest(source / "data_manifest.json")
    shutil.copytree(source, output)
    frame = pd.read_csv(output / "leaderboard.csv")
    changes = []
    for index, row in frame.iterrows():
        directory = output / row["artifact_path"]
        def read(name, directory=directory):
            value = pd.read_parquet(directory / "normalized" / f"{name}.parquet")
            return value[value["status"] != "EMPTY"] if "status" in value else value
        equity = read("equity_daily").sort_values("date").reset_index(drop=True)
        equity["drawdown"] = equity["equity"] / equity["equity"].cummax().clip(lower=1000.0) - 1.0
        equity.to_parquet(directory / "normalized" / "equity_daily.parquet", index=False)
        metrics = compute_metrics(equity, read("fills"), initial_cash=1000., start=equity["date"].iloc[0], end=equity["date"].iloc[-1], trades=read("trades"), cash_ledger=read("cash_ledger"))
        old_metrics = json.loads((directory / "metrics.json").read_text())
        for field in ("max_drawdown", "win_rate", "sortino_zero_mar", "starting_equity"):
            if old_metrics.get(field) != metrics.get(field):
                changes.append({"candidate_id": row["candidate_id"], "cost_scenario": row["cost_scenario"], "phase": row["phase"], "window_id": row["window_id"] if pd.notna(row["window_id"]) else None, "metric": field, "before": old_metrics.get(field), "after": metrics.get(field)})
        atomic_json(directory / "metrics.json", {**old_metrics, **metrics})
        for key, value in metrics.items():
            if not isinstance(value, (dict, list)):
                frame.loc[index, key] = value
    daily = pd.read_parquet(output / "normalized" / "equity_daily.parquet")
    keys = ["candidate_id", "phase", "cost_scenario", "window_id"]
    daily = daily.sort_values(keys + ["date"])
    daily["drawdown"] = daily["equity"] / daily.groupby(keys, dropna=False)["equity"].cummax().clip(lower=1000.) - 1.
    daily.to_parquet(output / "normalized" / "equity_daily.parquet", index=False)
    frame = _enrich_benchmark_metrics(output, frame, DEFAULT_STUDY_PROTOCOL)
    frame.to_csv(output / "leaderboard.csv", index=False)
    pd.DataFrame(changes).to_csv(output / "metric_corrections.csv", index=False)
    atomic_json(output / "correction_provenance.json", {
        "source": str(source.resolve()),
        "source_manifest_sha256": file_hash(source / "data_manifest.json"),
        "source_leaderboard_sha256": file_hash(source / "leaderboard.csv"),
        "scope": "derived statistics recalculated from saved daily accounts; original execution paths preserved",
        "corrected_run_count": len(frame),
        "source_hashes": {path.name: file_hash(path) for path in Path(__file__).parent.glob("*.py")},
    })
    atomic_json(output / "correction_audit.json", {
        "status": "CALCULATIONS_CORRECTED_EXECUTION_CONFORMANCE_INCOMPLETE",
        "scope": "Recalculated saved execution paths; not a new execution simulation.",
        "blockers": [
            "Saved target quantities use current-session opening prices instead of prior-close prices.",
            "Saved scheduled reviews and unchanged-target rebalances do not follow the required execution-session schedule.",
            "Saved holding ages and cooldowns lack actual fill feedback.",
            "Saved delayed execution does not preserve frozen original quantities.",
            "Saved A09 shadow and ensemble ledgers do not provide required cash/component accounting.",
        ],
    })
    rank_study(output)
    dispositions = pd.read_csv(output / "selection_leaderboard.csv")
    dispositions[dispositions["performance_gates_pass"]].groupby("pair_id", sort=False).head(3).to_csv(output / "numerical_shortlist.csv", index=False)
    daily_index = pd.read_parquet(output / "evaluation_index.parquet")
    daily_index = daily_index[daily_index["phase"] == "evaluation"]
    intervals = []
    primary = daily_index[daily_index["candidate_id"].str.endswith("__primary") & daily_index["cost_scenario"].isin(["base", "stress"])]
    for (candidate_id, cost), account in primary.groupby(["candidate_id", "cost_scenario"]):
        for benchmark in ("BENCH__SPY", "BENCH__QQQ"):
            reference = daily_index[(daily_index["candidate_id"] == benchmark) & (daily_index["cost_scenario"] == cost)]
            intervals.append({"candidate_id": candidate_id, "cost_scenario": cost, "benchmark": benchmark, **pooled_paired_bootstrap(account, reference)})
    atomic_json(output / "pooled_bootstrap.json", {"rows": intervals, "method": "2000 paired moving-block resamples within windows; length20; seed135; 95% descriptive intervals"})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    correct_saved_statistics(args.source, args.output)
