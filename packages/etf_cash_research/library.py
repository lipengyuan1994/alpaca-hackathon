"""Immutable, hash-checked ETF research library on the T9 volume."""

from __future__ import annotations

import json
import os
import plistlib
import shutil
import subprocess
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from packages.contracts.canonical import canonical_hash
from packages.research_data.artifacts import atomic_json, file_hash, write_parquet

from .collector import ETFCollectionSpec, ETFDataCollector, load_manifest

T9_ROOT = Path("/Volumes/T9")
T9_UUID = "8D84339B-38CA-350F-A7C6-A3DDE650156E"
DATASET = "alpaca/us-etf-daily"


def require_library(library: Path) -> Path:
    """Reject an absent/replaced T9 before any filesystem mutation."""
    if not T9_ROOT.is_dir() or not os.path.ismount(T9_ROOT):
        raise ValueError("ETF_T9_NOT_MOUNTED")
    result = subprocess.run(["/usr/sbin/diskutil", "info", "-plist", str(T9_ROOT)], capture_output=True, check=True)
    info = plistlib.loads(result.stdout)
    if str(info.get("VolumeUUID", "")).upper() != T9_UUID or info.get("MountPoint") != str(T9_ROOT) or info.get("WritableVolume") is not True:
        raise ValueError("ETF_T9_VOLUME_IDENTITY_MISMATCH")
    candidate = library.expanduser().absolute()
    if candidate != T9_ROOT / "TradingResearch" or candidate.is_symlink():
        raise ValueError("ETF_LIBRARY_PATH_INVALID")
    if any(part.is_symlink() for part in (T9_ROOT, candidate)):
        raise ValueError("ETF_LIBRARY_SYMLINK_REJECTED")
    return candidate


def _catalog_path(library: Path) -> Path:
    return library / "catalog.json"


def _load_catalog(library: Path) -> dict[str, Any]:
    path = _catalog_path(library)
    if not path.exists():
        return {"schema_version": "etf-research-library/v1", "volume_uuid": T9_UUID, "datasets": {}, "studies": {}, "catalog_hash": None}
    value = json.loads(path.read_text())
    expected = canonical_hash({key: item for key, item in value.items() if key != "catalog_hash"})
    if value.get("catalog_hash") != expected or value.get("volume_uuid") != T9_UUID:
        raise ValueError("ETF_LIBRARY_CATALOG_INVALID")
    return value


def _save_catalog(library: Path, catalog: dict[str, Any]) -> None:
    catalog["catalog_hash"] = canonical_hash({key: item for key, item in catalog.items() if key != "catalog_hash"})
    atomic_json(_catalog_path(library), catalog)


def _safe_files(root: Path) -> list[Path]:
    files = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("ETF_LIBRARY_SOURCE_SYMLINK_REJECTED")
        if path.is_file():
            files.append(path)
    return sorted(files)


def _inventory(root: Path, *, exclude: tuple[str, ...] = ("archive_inventory.json",)) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): file_hash(path) for path in _safe_files(root) if path.name not in exclude and not path.name.startswith("._") and path.name != ".DS_Store"}


def _verify_inventory(root: Path) -> dict[str, str]:
    inventory = json.loads((root / "archive_inventory.json").read_text())
    files = _inventory(root)
    if inventory.get("files") != files or inventory.get("inventory_hash") != canonical_hash(files):
        raise ValueError("ETF_LIBRARY_INVENTORY_MISMATCH")
    return files


def _verify_manifest(root: Path) -> dict[str, Any]:
    manifest = load_manifest(root / "data_manifest.json")
    for dataset in manifest["datasets"]:
        for item in [dataset["artifact"], *dataset.get("raw_pages", [])]:
            path = root / item["path"]
            if not path.is_file() or file_hash(path) != item["sha256"]:
                raise ValueError("ETF_LIBRARY_SOURCE_HASH_MISMATCH")
            if "rows" in item and len(pd.read_parquet(path)) != item["rows"]:
                raise ValueError("ETF_LIBRARY_SOURCE_ROWS_MISMATCH")
    return manifest


def _coverage(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    item = next(d for d in manifest["datasets"] if d["dataset_id"] == "stock_bars_raw")
    bars = pd.read_parquet(root / item["artifact"]["path"])
    days = pd.to_datetime(bars["event_time"], utc=True).dt.date
    return {"start": str(days.min()), "end": str(days.max()), "symbols": sorted(bars["symbol"].unique().tolist()), "feed": sorted(item.get("feed", [])), "sessions_per_symbol": bars.groupby("symbol").size().astype(int).to_dict()}


def _publish_snapshot(library: Path, staging: Path, snapshot_id: str, parent_id: str | None) -> Path:
    manifest = _verify_manifest(staging)
    coverage = _coverage(staging, manifest)
    atomic_json(staging / "archive_inventory.json", {"files": _inventory(staging), "inventory_hash": canonical_hash(_inventory(staging))})
    _verify_inventory(staging)
    target = library / "datasets" / DATASET / snapshot_id
    if target.exists():
        raise ValueError("ETF_LIBRARY_SNAPSHOT_EXISTS")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging.rename(target)
    _verify_inventory(target)
    catalog = _load_catalog(library)
    catalog["datasets"][snapshot_id] = {"path": str(target.relative_to(library)), "manifest_hash": manifest["manifest_hash"], "coverage": coverage, "parent_snapshot_id": parent_id}
    catalog["latest_dataset"] = snapshot_id
    try:
        _save_catalog(library, catalog)
    except Exception:
        target.rename(staging)
        raise
    return target


def _require_space(library: Path, required_bytes: int) -> None:
    if shutil.disk_usage(library.parent).free < required_bytes * 2 + 100_000_000:
        raise ValueError("ETF_LIBRARY_INSUFFICIENT_SPACE")


def archive_existing(library: Path, collection: Path, study_sources: dict[str, Path], *, run_id: str, selection_files: dict[str, Path] | None = None) -> dict[str, str]:
    library = require_library(library)
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise ValueError("ETF_LIBRARY_RUN_ID_INVALID")
    source_manifest = _verify_manifest(collection)
    snapshot_id = source_manifest["manifest_hash"].split(":", 1)[1][:16]
    catalog = _load_catalog(library)
    if run_id in catalog["studies"]:
        raise ValueError("ETF_LIBRARY_ENTRY_EXISTS")
    _require_space(library, sum(path.stat().st_size for source in study_sources.values() for path in _safe_files(source)) + (0 if snapshot_id in catalog["datasets"] else sum(path.stat().st_size for path in _safe_files(collection))))
    library.mkdir(parents=True, exist_ok=True)
    if snapshot_id in catalog["datasets"]:
        verify_data(library, snapshot_id=snapshot_id)
        snapshot = library / catalog["datasets"][snapshot_id]["path"]
        if catalog["datasets"][snapshot_id]["manifest_hash"] != source_manifest["manifest_hash"]:
            raise ValueError("ETF_LIBRARY_PARENT_HASH_MISMATCH")
    else:
        stage = library / ".staging" / f"dataset-{uuid.uuid4().hex}"
        stage.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(collection, stage)
        _verify_manifest(stage)
        snapshot = _publish_snapshot(library, stage, snapshot_id, None)
    study_stage = library / ".staging" / f"study-{uuid.uuid4().hex}"
    study_stage.mkdir()
    for name, source in study_sources.items():
        if not source.is_dir() or name not in {"development", "validation", "holdout", "continuous", "reports"}:
            raise ValueError("ETF_LIBRARY_STUDY_SOURCE_INVALID")
        shutil.copytree(source, study_stage / name)
        if _inventory(source) != _inventory(study_stage / name):
            raise ValueError("ETF_LIBRARY_STUDY_COPY_MISMATCH")
    for name, source in (selection_files or {}).items():
        if name not in {"selection.json", "final_selection.json"} or not source.is_file():
            raise ValueError("ETF_LIBRARY_SELECTION_SOURCE_INVALID")
        selection = json.loads(source.read_text())
        hash_field = "selection_hash" if name == "selection.json" else "final_selection_hash"
        if selection.get(hash_field) != canonical_hash({key: value for key, value in selection.items() if key != hash_field}):
            raise ValueError("ETF_LIBRARY_SELECTION_HASH_INVALID")
        shutil.copy2(source, study_stage / name)
        if file_hash(source) != file_hash(study_stage / name):
            raise ValueError("ETF_LIBRARY_SELECTION_COPY_MISMATCH")
    atomic_json(study_stage / "study_binding.json", {"snapshot_id": snapshot_id, "manifest_hash": source_manifest["manifest_hash"], "run_id": run_id, "status": "DETERMINISTIC_RESEARCH_ONLY"})
    files = _inventory(study_stage)
    atomic_json(study_stage / "archive_inventory.json", {"files": files, "inventory_hash": canonical_hash(files)})
    _verify_inventory(study_stage)
    study = library / "studies" / "etf-cash-v1" / run_id
    study.parent.mkdir(parents=True, exist_ok=True)
    if study.exists():
        raise ValueError("ETF_LIBRARY_STUDY_EXISTS")
    study_stage.rename(study)
    _verify_inventory(study)
    catalog = _load_catalog(library)
    catalog["studies"][run_id] = {"path": str(study.relative_to(library)), "snapshot_id": snapshot_id, "inventory_hash": json.loads((study / "archive_inventory.json").read_text())["inventory_hash"]}
    try:
        _save_catalog(library, catalog)
    except Exception:
        study.rename(study_stage)
        raise
    return {"snapshot": str(snapshot), "study": str(study), "snapshot_id": snapshot_id}


def verify_data(library: Path, *, snapshot_id: str | None = None) -> dict[str, Any]:
    library = require_library(library)
    catalog = _load_catalog(library)
    targets = [snapshot_id] if snapshot_id else list(catalog["datasets"])
    for key in targets:
        if key not in catalog["datasets"]:
            raise ValueError("ETF_LIBRARY_SNAPSHOT_UNKNOWN")
        item = catalog["datasets"][key]
        root = library / item["path"]
        _verify_inventory(root)
        manifest = _verify_manifest(root)
        if manifest["manifest_hash"] != item["manifest_hash"] or _coverage(root, manifest) != item["coverage"]:
            raise ValueError("ETF_LIBRARY_CATALOG_DATA_MISMATCH")
    for item in catalog["studies"].values():
        root = library / item["path"]
        _verify_inventory(root)
        if json.loads((root / "archive_inventory.json").read_text())["inventory_hash"] != item["inventory_hash"]:
            raise ValueError("ETF_LIBRARY_STUDY_HASH_MISMATCH")
    return {"status": "VERIFIED", "snapshots": targets, "studies": list(catalog["studies"])}


def list_data(library: Path) -> dict[str, Any]:
    library = require_library(library)
    return _load_catalog(library)


def _business_overlap_end(days: list[date], overlap: int = 5) -> date:
    return days[max(0, len(days) - overlap)]


def _rows_by_key(frame: pd.DataFrame, dataset_id: str) -> dict[tuple[str, str], dict[str, Any]]:
    if dataset_id.startswith("stock_bars"):
        keys = ("symbol", "event_time")
    elif dataset_id == "calendar":
        keys = ("date", "date")
    else:
        keys = ("id", "id") if "id" in frame.columns else ("symbol", "ex_date")
    output = {}
    for record in frame.to_dict("records"):
        key = (str(record.get(keys[0])), str(record.get(keys[1])))
        if key in output:
            raise ValueError("ETF_LIBRARY_DUPLICATE_KEY")
        output[key] = record
    return output


def _compare_rows(left: dict[str, Any], right: dict[str, Any], dataset_id: str) -> bool:
    fields = ("open", "high", "low", "close", "volume", "trade_count", "vwap") if dataset_id.startswith("stock_bars") else tuple(key for key in left if key not in {"ingested_at", "available_time", "raw_response_hash", "endpoint", "source_page_token"})
    return all(str(left.get(field)) == str(right.get(field)) for field in fields)


def _event_day(row: dict[str, Any], dataset_id: str) -> date | None:
    field = "event_time" if dataset_id.startswith("stock_bars") else "date" if dataset_id == "calendar" else "ex_date"
    value = row.get(field)
    return pd.Timestamp(value).date() if value is not None and pd.notna(value) else None


def update_data(library: Path, dataset: str, through: str, client: Any) -> dict[str, Any]:
    library = require_library(library)
    if dataset != DATASET:
        raise ValueError("ETF_LIBRARY_DATASET_INVALID")
    end = date.fromisoformat(through)
    catalog = _load_catalog(library)
    parent_id = catalog.get("latest_dataset")
    if not parent_id:
        raise ValueError("ETF_LIBRARY_NO_PARENT")
    verify_data(library, snapshot_id=parent_id)
    parent = library / catalog["datasets"][parent_id]["path"]
    _require_space(library, sum(path.stat().st_size for path in _safe_files(parent)))
    parent_manifest = load_manifest(parent / "data_manifest.json")
    last = date.fromisoformat(catalog["datasets"][parent_id]["coverage"]["end"])
    if end <= last:
        raise ValueError("ETF_LIBRARY_END_NOT_AFTER_PARENT")
    calendar_dataset = next(d for d in parent_manifest["datasets"] if d["dataset_id"] == "calendar")
    calendar = pd.read_parquet(parent / calendar_dataset["artifact"]["path"])
    overlap = _business_overlap_end(sorted(pd.to_datetime(calendar["date"]).dt.date.tolist()))
    spec = ETFCollectionSpec(collection_id="etf_cash_incremental_v1", symbols=tuple(parent_manifest["symbols"]), start=f"{overlap}T00:00:00Z", end=f"{end}T23:59:59Z", feeds=tuple(catalog["datasets"][parent_id]["coverage"]["feed"]))
    stage = library / ".staging" / f"update-{uuid.uuid4().hex}"
    stage.mkdir(parents=True, exist_ok=False)
    delta = stage / "delta"
    spec_path = stage / "incremental_spec.json"
    atomic_json(spec_path, {"start": spec.start, "end": spec.end, "symbols": list(spec.symbols), "feeds": list(spec.feeds)})
    ETFDataCollector(client).collect(spec=spec, spec_path=spec_path, output=delta)
    fresh = load_manifest(delta / "data_manifest.json")
    fresh_calendar = next(d for d in fresh["datasets"] if d["dataset_id"] == "calendar")
    fresh_days = pd.to_datetime(pd.read_parquet(delta / fresh_calendar["artifact"]["path"])["date"]).dt.date
    if fresh_days.empty or fresh_days.max() <= last:
        shutil.rmtree(stage, ignore_errors=True)
        return {"status": "NO_NEW_SESSIONS", "snapshot_id": parent_id}
    current = stage / "snapshot"
    shutil.copytree(parent, current, ignore=shutil.ignore_patterns("archive_inventory.json"))
    merged_manifest = json.loads((current / "data_manifest.json").read_text())
    diffs = []
    for item in merged_manifest["datasets"]:
        name = item["dataset_id"]
        newer = next(d for d in fresh["datasets"] if d["dataset_id"] == name)
        old_frame = pd.read_parquet(current / item["artifact"]["path"])
        new_frame = pd.read_parquet(delta / newer["artifact"]["path"])
        old_rows = _rows_by_key(old_frame, name)
        new_rows = _rows_by_key(new_frame, name)
        for key, row in old_rows.items():
            event_day = _event_day(row, name)
            if event_day is not None and event_day >= overlap and key not in new_rows:
                diffs.append({"dataset": name, "key": key, "issue": "PREVIOUS_EVENT_MISSING_FROM_REFRESH"})
        for key, row in new_rows.items():
            event_day = _event_day(row, name)
            if event_day is not None and event_day <= last and key not in old_rows:
                diffs.append({"dataset": name, "key": key, "issue": "NEW_HISTORICAL_EVENT"})
        for key in old_rows.keys() & new_rows.keys():
            if not _compare_rows(old_rows[key], new_rows[key], name):
                diffs.append({"dataset": name, "key": key, "old": {field: str(value) for field, value in old_rows[key].items()}, "new": {field: str(value) for field, value in new_rows[key].items()}})
        if diffs:
            continue
        merged = list(old_rows.values()) + [row for key, row in new_rows.items() if key not in old_rows]
        item["artifact"] = write_parquet(current, name, merged, tuple(old_frame.columns))
        for page in newer.get("raw_pages", []):
            source = delta / page["path"]
            destination = current / "raw" / name / f"incremental-{uuid.uuid4().hex}.json"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            item["raw_pages"].append({**page, "path": destination.relative_to(current).as_posix()})
    if diffs:
        atomic_json(stage / "differences.json", {"status": "REVIEW_REQUIRED", "parent_snapshot_id": parent_id, "differences": diffs})
        raise ValueError(f"ETF_LIBRARY_PROVIDER_REVISION:{stage / 'differences.json'}")
    calendar_item = next(d for d in merged_manifest["datasets"] if d["dataset_id"] == "calendar")
    all_calendar = pd.read_parquet(current / calendar_item["artifact"]["path"])
    expected = set(pd.to_datetime(all_calendar["date"]).dt.date)
    for name in ("stock_bars_raw", "stock_bars_split"):
        item = next(d for d in merged_manifest["datasets"] if d["dataset_id"] == name)
        bars = pd.read_parquet(current / item["artifact"]["path"])
        for symbol in spec.symbols:
            actual = set(pd.to_datetime(bars.loc[bars.symbol == symbol, "event_time"], utc=True).dt.date)
            if expected != actual:
                atomic_json(stage / "coverage_failure.json", {"status": "INCOMPLETE", "dataset": name, "symbol": symbol, "missing": sorted(str(day) for day in expected - actual)})
                raise ValueError(f"ETF_LIBRARY_COVERAGE_INCOMPLETE:{stage / 'coverage_failure.json'}")
    merged_manifest["spec_hash"] = file_hash(spec_path)
    merged_manifest["parent_manifest_hash"] = parent_manifest["manifest_hash"]
    merged_manifest["collection_id"] = "etf_cash_incremental_v1"
    merged_manifest["manifest_hash"] = canonical_hash({key: value for key, value in merged_manifest.items() if key != "manifest_hash"})
    atomic_json(current / "data_manifest.json", merged_manifest)
    snapshot_id = merged_manifest["manifest_hash"].split(":", 1)[1][:16]
    result = _publish_snapshot(library, current, snapshot_id, parent_id)
    shutil.rmtree(stage, ignore_errors=True)
    return {"status": "PUBLISHED", "snapshot_id": snapshot_id, "path": str(result)}
