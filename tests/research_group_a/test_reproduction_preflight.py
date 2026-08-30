from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from packages.contracts.canonical import canonical_hash
from packages.research_data.artifacts import file_hash
from strategy_plugins.intraday_continuation_v1.reproduce import main as continuation_reproduce
from strategy_plugins.vwap_reversion_v1.reproduce import main as reversion_reproduce


def _write_inputs(root: Path, *, ready: bool = True) -> tuple[Path, Path]:
    normalized = root / "normalized"
    normalized.mkdir(parents=True)
    bars = normalized / "stock_bars_split.parquet"
    pd.DataFrame(
        [{"symbol": "SPY", "event_time": "2026-08-28T14:30:00Z", "close": "100"}]
    ).to_parquet(bars, index=False)
    manifest = {
        "schema_version": "research-data-manifest/v1",
        "status": "COLLECTED",
        "datasets": [
            {
                "dataset_id": "stock_bars_split",
                "feed": "iex",
                "adjustment": "split",
                "artifact": {"path": "normalized/stock_bars_split.parquet", "sha256": file_hash(bars)},
            }
        ],
        "manifest_hash": None,
    }
    manifest["manifest_hash"] = canonical_hash(
        {key: value for key, value in manifest.items() if key != "manifest_hash"}
    )
    data_path = root / "data_manifest.json"
    data_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    feasibility = {
        "schema_version": "option-proxy-feasibility/v1",
        "status": "READY_FOR_REPLAY" if ready else "PENDING",
        "data_manifest_hash": manifest["manifest_hash"],
        "selected_symbols": ["SPY"],
        "attestations": [
            {"role": "data_steward", "signed_at": "2026-08-29T00:00:00Z"},
            {"role": "non_author_reviewer", "signed_at": "2026-08-29T00:00:00Z"},
        ],
        "manifest_hash": None,
    }
    feasibility["manifest_hash"] = canonical_hash(
        {key: value for key, value in feasibility.items() if key != "manifest_hash"}
    )
    feasibility_path = root / "option_proxy_feasibility_manifest.json"
    feasibility_path.write_text(json.dumps(feasibility, sort_keys=True), encoding="utf-8")
    return data_path, feasibility_path


def test_reproduce_refuses_unready_feasibility(tmp_path: Path) -> None:
    data, feasibility = _write_inputs(tmp_path / "input", ready=False)
    output = tmp_path / "output"
    assert continuation_reproduce(
        ["--data-manifest", str(data), "--feasibility-manifest", str(feasibility), "--output", str(output)]
    ) == 2
    refusal = json.loads((output / "reproduction_refusal.json").read_text(encoding="utf-8"))
    assert refusal == {"reason": "FEASIBILITY_MANIFEST_NOT_READY", "status": "REFUSED"}


def test_reproductions_are_deterministic_preflights(tmp_path: Path) -> None:
    data, feasibility = _write_inputs(tmp_path / "input")
    first = tmp_path / "continuation"
    second = tmp_path / "reversion"
    assert continuation_reproduce(
        ["--data-manifest", str(data), "--feasibility-manifest", str(feasibility), "--output", str(first)]
    ) == 0
    assert reversion_reproduce(
        ["--data-manifest", str(data), "--feasibility-manifest", str(feasibility), "--output", str(second)]
    ) == 0
    continuation = json.loads((first / "run_manifest.json").read_text(encoding="utf-8"))
    reversion = json.loads((second / "run_manifest.json").read_text(encoding="utf-8"))
    assert continuation["status"] == reversion["status"] == "INPUTS_VALIDATED_OUTCOME_RUN_PENDING"
    assert continuation["option_proxy_selected_symbols"] == reversion["option_proxy_selected_symbols"] == ["SPY"]
