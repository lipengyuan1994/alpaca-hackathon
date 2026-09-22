"""Add per-window one-session delay diagnostics to a completed v2 study."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from packages.etf_cash_research.protocol_v2 import StudyProtocolV2
from packages.etf_cash_research.simulator_v2 import run_generic_backtest
from packages.etf_cash_research.study_v2 import (
    _concat_artifacts,
    _study_candidates,
    _write_result,
    build_strategy,
    load_v2_bars_from_manifest,
)
from packages.research_data.artifacts import write_parquet


def main() -> int:
    if len(sys.argv) != 5:
        raise SystemExit("usage: etf_delay_windows_runner.py ROOT TRACK PAIR_OR_EMPTY DATA_MANIFEST")
    root = Path(sys.argv[1])
    track = sys.argv[2]
    pair = sys.argv[3] or None
    protocol = StudyProtocolV2.from_yaml(Path("configs/etf_cash_research_v2.yaml"))
    manifest = Path(sys.argv[4])
    bars = load_v2_bars_from_manifest(manifest)
    board_path = root / "leaderboard.csv"
    board = pd.read_csv(board_path)
    candidates = _study_candidates(track, pair_id=pair, include_sensitivities=False)
    candidate_ids = set(board.loc[board["candidate_id"].astype(str).str.endswith("__primary"), "candidate_id"].astype(str))
    candidates = [candidate for candidate in candidates if candidate.candidate_id in candidate_ids]
    rows: list[dict[str, object]] = []
    costs = protocol.costs_track_a if track == "a" else protocol.costs_track_b
    for candidate in candidates:
        for cost in costs:
            for window_id, start, end in protocol.evaluation_windows:
                result = run_generic_backtest(
                    bars,
                    candidate=candidate,
                    strategy=build_strategy(candidate),
                    cost=cost,
                    protocol=protocol,
                    start=start,
                    end=end,
                    execution_delay_sessions=protocol.delay_stress_sessions,
                )
                path = root / candidate.candidate_id / cost.name / f"delay_{window_id}"
                _write_result(result, path, window_id=window_id, phase="delay")
                rows.append({
                    **result.metrics,
                    "phase": "delay",
                    "window_id": window_id,
                    "benchmark": False,
                    "artifact_path": str(path.relative_to(root)),
                })
    if rows:
        board = pd.concat([board, pd.DataFrame(rows)], ignore_index=True, sort=False)
        board = board.drop_duplicates(subset=["candidate_id", "phase", "cost_scenario", "window_id"], keep="first")
        board.to_csv(board_path, index=False)
        for name in ("equity_daily", "signals", "orders", "fills", "cash_ledger", "trades", "component_ledger"):
            frame = _concat_artifacts(root, name)
            write_parquet(root, name, frame, tuple(frame.columns))
    metadata_path = root / "study_metadata.json"
    if metadata_path.is_file():
        import json

        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["delay_windows_executed"] = True
        metadata["delay_window_count"] = len(protocol.evaluation_windows)
        metadata_path.write_text(json.dumps(metadata, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    print(root, "delay window rows", len(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
