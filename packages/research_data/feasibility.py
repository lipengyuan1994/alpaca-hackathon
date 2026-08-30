"""Blinded option-proxy feasibility manifest generation for research packets.

This module never reads signals, returns, P&L, or strategy parameters. Its
deterministic output is immediately usable for offline research; independent
review may be added later as non-blocking provenance.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from packages.contracts.canonical import canonical_hash

from .artifacts import atomic_json, file_hash

SYMBOL_ORDER = ("SPY", "QQQ", "TQQQ", "SMH", "SOXL", "IGV")


class FeasibilityError(ValueError):
    """The collector evidence is unsuitable for a blinded feasibility scan."""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FeasibilityError("FEASIBILITY_DATA_MANIFEST_INVALID") from exc
    if not isinstance(value, dict):
        raise FeasibilityError("FEASIBILITY_DATA_MANIFEST_INVALID")
    return value


def _manifest_hash(manifest: dict[str, Any]) -> str:
    return canonical_hash({key: value for key, value in manifest.items() if key != "manifest_hash"})


def _dataset(manifest: dict[str, Any], dataset_id: str) -> dict[str, Any]:
    matches = [
        item
        for item in manifest.get("datasets", [])
        if isinstance(item, dict) and item.get("dataset_id") == dataset_id
    ]
    if len(matches) != 1:
        raise FeasibilityError(f"FEASIBILITY_DATASET_MISSING_{dataset_id}")
    return matches[0]


def _artifact_path(root: Path, dataset: dict[str, Any]) -> Path:
    artifact = dataset.get("artifact")
    if not isinstance(artifact, dict) or not isinstance(artifact.get("path"), str):
        raise FeasibilityError("FEASIBILITY_ARTIFACT_INVALID")
    path = (root / artifact["path"]).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise FeasibilityError("FEASIBILITY_ARTIFACT_OUTSIDE_ROOT") from exc
    if not path.is_file() or file_hash(path) != artifact.get("sha256"):
        raise FeasibilityError("FEASIBILITY_ARTIFACT_HASH_MISMATCH")
    return path


def build_feasibility_draft(*, data_manifest_path: Path) -> dict[str, Any]:
    """Return a deterministic rank-only draft from immutable collector outputs."""
    manifest = _load_json(data_manifest_path)
    if manifest.get("schema_version") != "research-data-manifest/v1":
        raise FeasibilityError("FEASIBILITY_DATA_MANIFEST_SCHEMA_MISMATCH")
    if manifest.get("manifest_hash") != _manifest_hash(manifest):
        raise FeasibilityError("FEASIBILITY_DATA_MANIFEST_HASH_MISMATCH")
    root = data_manifest_path.resolve().parent
    split = _dataset(manifest, "stock_bars_split")
    if split.get("feed") != "iex" or split.get("adjustment") != "split":
        raise FeasibilityError("FEASIBILITY_SPLIT_IEX_REQUIRED")
    bars = pd.read_parquet(_artifact_path(root, split))
    required = {"symbol", "event_time", "open", "high", "low", "close", "volume"}
    if not required.issubset(bars.columns):
        raise FeasibilityError("FEASIBILITY_BARS_SCHEMA_INVALID")
    bars = bars[bars["symbol"].isin(SYMBOL_ORDER)].copy()
    for column in ("open", "high", "low", "close", "volume"):
        bars[column] = pd.to_numeric(bars[column], errors="coerce")
    rows: list[dict[str, Any]] = []
    for symbol in SYMBOL_ORDER:
        cell = bars[bars["symbol"] == symbol]
        duplicate_count = int(cell.duplicated(["symbol", "event_time"]).sum())
        finite = not cell[["open", "high", "low", "close", "volume"]].isna().any().any()
        valid_ohlc = bool(
            finite
            and (cell["low"] <= cell[["open", "close"]].min(axis=1)).all()
            and (cell["high"] >= cell[["open", "close"]].max(axis=1)).all()
            and (cell["volume"] >= 0).all()
        )
        rows.append(
            {
                "symbol": symbol,
                "bar_rows": int(len(cell)),
                "duplicate_bars": duplicate_count,
                "ohlc_valid": valid_ohlc,
                "contract_existence": "NOT_EVALUATED",
                "simultaneous_leg_coverage": "NOT_EVALUATED",
                "eligible": bool(len(cell) > 0 and duplicate_count == 0 and valid_ohlc),
            }
        )
    ranked = sorted(rows, key=lambda item: (not item["eligible"], SYMBOL_ORDER.index(item["symbol"])))
    for rank, row in enumerate(ranked, start=1):
        row["rank"] = rank
    selected = [row["symbol"] for row in ranked if row["eligible"]][:3]
    draft: dict[str, Any] = {
        "schema_version": "option-proxy-feasibility/v1",
        "status": "READY_FOR_REPLAY",
        "data_manifest_hash": manifest["manifest_hash"],
        "selection_method": "blinded_data_quality_then_frozen_symbol_order/v1",
        "ranked_symbols": ranked,
        "selected_symbols": selected,
        "selection_cutoff_rank": len(selected),
        "attestations": [],
        "manifest_hash": None,
    }
    draft["manifest_hash"] = _manifest_hash(draft)
    return draft


def write_feasibility_draft(*, data_manifest_path: Path, output_path: Path) -> Path:
    if output_path.exists():
        raise FeasibilityError("FEASIBILITY_OUTPUT_EXISTS")
    draft = build_feasibility_draft(data_manifest_path=data_manifest_path)
    atomic_json(output_path, draft)
    return output_path
