from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from packages.contracts.canonical import canonical_hash
from packages.research_data.artifacts import file_hash
from packages.research_data.feasibility import build_feasibility_draft


def test_feasibility_draft_is_blinded_deterministic_and_unsigned(tmp_path: Path) -> None:
    normalized = tmp_path / "normalized"
    normalized.mkdir()
    parquet = normalized / "stock_bars_split.parquet"
    pd.DataFrame(
        [
            {
                "symbol": symbol,
                "event_time": "2026-08-28T14:30:00Z",
                "open": "100",
                "high": "101",
                "low": "99",
                "close": "100",
                "volume": "10",
            }
            for symbol in ("SPY", "QQQ", "TQQQ")
        ]
    ).to_parquet(parquet, index=False)
    manifest = {
        "schema_version": "research-data-manifest/v1",
        "status": "COLLECTED_UNATTESTED",
        "datasets": [
            {
                "dataset_id": "stock_bars_split",
                "feed": "iex",
                "adjustment": "split",
                "artifact": {"path": "normalized/stock_bars_split.parquet", "sha256": file_hash(parquet)},
            }
        ],
        "manifest_hash": None,
    }
    manifest["manifest_hash"] = canonical_hash(
        {key: value for key, value in manifest.items() if key != "manifest_hash"}
    )
    path = tmp_path / "data_manifest.json"
    path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")

    first = build_feasibility_draft(data_manifest_path=path)
    second = build_feasibility_draft(data_manifest_path=path)

    assert first == second
    assert first["status"] == "READY_FOR_REPLAY"
    assert first["attestations"] == []
    assert first["selected_symbols"] == ["SPY", "QQQ", "TQQQ"]
    assert [row["symbol"] for row in first["ranked_symbols"]] == [
        "SPY",
        "QQQ",
        "TQQQ",
        "SMH",
        "SOXL",
        "IGV",
    ]
