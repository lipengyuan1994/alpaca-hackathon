from __future__ import annotations

import json
import plistlib
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from packages.contracts.canonical import canonical_hash
from packages.etf_cash_research import library as lib
from packages.research_data.artifacts import atomic_json, write_parquet


def test_mount_requires_exact_t9_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lib.os.path, "ismount", lambda path: True)
    monkeypatch.setattr(Path, "is_dir", lambda self: True)
    def fake(value: dict[str, str]) -> SimpleNamespace:
        return SimpleNamespace(stdout=plistlib.dumps(value))
    monkeypatch.setattr(lib.subprocess, "run", lambda *args, **kwargs: fake({"VolumeUUID": "another-drive", "MountPoint": "/Volumes/T9"}))
    with pytest.raises(ValueError, match="IDENTITY_MISMATCH"):
        lib.require_library(Path("/Volumes/T9/TradingResearch"))
    monkeypatch.setattr(lib.subprocess, "run", lambda *args, **kwargs: fake({"VolumeUUID": lib.T9_UUID, "MountPoint": "/Volumes/T9", "WritableVolume": False}))
    with pytest.raises(ValueError, match="IDENTITY_MISMATCH"):
        lib.require_library(Path("/Volumes/T9/TradingResearch"))
    monkeypatch.setattr(lib.os.path, "ismount", lambda path: False)
    with pytest.raises(ValueError, match="NOT_MOUNTED"):
        lib.require_library(Path("/Volumes/T9/TradingResearch"))


def test_inventory_detects_changes_and_ignores_exfat_sidecars(tmp_path: Path) -> None:
    (tmp_path / "bars.parquet").write_bytes(b"data")
    files = lib._inventory(tmp_path)
    atomic_json(tmp_path / "archive_inventory.json", {"files": files, "inventory_hash": canonical_hash(files)})
    (tmp_path / "._archive_inventory.json").write_bytes(b"mac metadata")
    lib._verify_inventory(tmp_path)
    (tmp_path / "bars.parquet").write_bytes(b"changed")
    with pytest.raises(ValueError, match="INVENTORY_MISMATCH"):
        lib._verify_inventory(tmp_path)


def test_overlap_revision_and_duplicate_detection() -> None:
    frame = pd.DataFrame([{"symbol": "QQQM", "event_time": "2026-09-18", "close": 100.0}])
    old = lib._rows_by_key(frame, "stock_bars_raw")
    new = lib._rows_by_key(frame.assign(close=101.0), "stock_bars_raw")
    key = ("QQQM", "2026-09-18")
    assert not lib._compare_rows(old[key], new[key], "stock_bars_raw")
    assert lib._compare_rows(old[key], old[key], "stock_bars_raw")
    with pytest.raises(ValueError, match="DUPLICATE_KEY"):
        lib._rows_by_key(pd.concat([frame, frame]), "stock_bars_raw")


def test_source_hash_failure_blocks_publication(tmp_path: Path) -> None:
    artifact = write_parquet(tmp_path, "stock_bars_raw", [{"symbol": "QQQM", "event_time": "2026-09-18", "close": 100.0}], ("symbol", "event_time", "close"))
    manifest = {"schema_version": "etf-cash-data-manifest/v1", "status": "COLLECTED", "datasets": [{"dataset_id": "stock_bars_raw", "artifact": artifact}], "manifest_hash": None}
    manifest["manifest_hash"] = canonical_hash({k: v for k, v in manifest.items() if k != "manifest_hash"})
    atomic_json(tmp_path / "data_manifest.json", manifest)
    lib._verify_manifest(tmp_path)
    (tmp_path / artifact["path"]).write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="SOURCE_HASH_MISMATCH"):
        lib._verify_manifest(tmp_path)


def test_catalog_write_failure_returns_snapshot_to_staging(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    library = tmp_path / "library"
    stage = library / ".staging" / "candidate"
    stage.mkdir(parents=True)
    bars = [{"symbol": "QQQM", "event_time": "2026-09-18", "close": 100.0}]
    artifact = write_parquet(stage, "stock_bars_raw", bars, tuple(bars[0]))
    manifest = {"schema_version": "etf-cash-data-manifest/v1", "status": "COLLECTED", "datasets": [{"dataset_id": "stock_bars_raw", "artifact": artifact}], "manifest_hash": None}
    manifest["manifest_hash"] = canonical_hash({k: v for k, v in manifest.items() if k != "manifest_hash"})
    atomic_json(stage / "data_manifest.json", manifest)
    monkeypatch.setattr(lib, "_save_catalog", lambda *args: (_ for _ in ()).throw(OSError("disk error")))
    with pytest.raises(OSError, match="disk error"):
        lib._publish_snapshot(library, stage, "candidate", None)
    assert stage.exists()
    assert not (library / "datasets" / lib.DATASET / "candidate").exists()


def test_catalog_ignores_unpublished_staging(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(lib, "require_library", lambda path: tmp_path)
    (tmp_path / ".staging" / "unfinished").mkdir(parents=True)
    assert lib.list_data(tmp_path)["datasets"] == {}
    bad = {"schema_version": "etf-research-library/v1", "volume_uuid": lib.T9_UUID, "datasets": {}, "studies": {}, "catalog_hash": "bad"}
    (tmp_path / "catalog.json").write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="CATALOG_INVALID"):
        lib.list_data(tmp_path)


@pytest.mark.parametrize("revision", ["none", "price", "action_removed", "missing_bar", "no_new_session"])
def test_incremental_snapshot_or_revision_report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, revision: str) -> None:
    monkeypatch.setattr(lib, "require_library", lambda path: tmp_path)
    symbols = ("QQQM", "SOXX", "SMH", "QQQ", "SPY")
    dates = ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18"]

    def make_collection(root: Path, days: list[str], *, altered: bool = False, action: bool = False, missing_bar: bool = False) -> dict:
        root.mkdir(parents=True)
        bars = []
        for symbol in symbols:
            for day in days:
                if missing_bar and symbol == "QQQM" and day == "2026-09-18":
                    continue
                close = 101.0 if altered and symbol == "QQQM" and day == "2026-09-18" else 100.0
                bars.append({"symbol": symbol, "event_time": f"{day}T00:00:00+00:00", "open": close, "high": close, "low": close, "close": close, "volume": 1000.0, "trade_count": 1, "vwap": close})
        datasets = []
        for name in ("stock_bars_raw", "stock_bars_split"):
            artifact = write_parquet(root, name, bars, tuple(bars[0]))
            datasets.append({"dataset_id": name, "feed": ["sip"], "adjustment": "raw" if name.endswith("raw") else "split", "artifact": artifact, "raw_pages": []})
        calendar = [{"date": day, "open": "09:30", "close": "16:00"} for day in days]
        datasets.append({"dataset_id": "calendar", "artifact": write_parquet(root, "calendar", calendar, tuple(calendar[0])), "raw_pages": []})
        actions = [{"id": "a1", "symbol": "QQQM", "ex_date": "2026-09-18", "rate": 0.10}] if action else []
        datasets.append({"dataset_id": "corporate_actions", "artifact": write_parquet(root, "corporate_actions", actions, ("id", "symbol", "ex_date", "rate")), "raw_pages": []})
        manifest = {"schema_version": "etf-cash-data-manifest/v1", "status": "COLLECTED", "symbols": list(symbols), "datasets": datasets, "manifest_hash": None}
        manifest["manifest_hash"] = canonical_hash({k: v for k, v in manifest.items() if k != "manifest_hash"})
        atomic_json(root / "data_manifest.json", manifest)
        return manifest

    parent = tmp_path / "datasets" / lib.DATASET / "parent"
    manifest = make_collection(parent, dates, action=revision == "action_removed")
    files = lib._inventory(parent)
    atomic_json(parent / "archive_inventory.json", {"files": files, "inventory_hash": canonical_hash(files)})
    catalog = lib._load_catalog(tmp_path)
    catalog["datasets"]["parent"] = {"path": str(parent.relative_to(tmp_path)), "manifest_hash": manifest["manifest_hash"], "coverage": lib._coverage(parent, manifest), "parent_snapshot_id": None}
    catalog["latest_dataset"] = "parent"
    lib._save_catalog(tmp_path, catalog)

    class FakeCollector:
        def __init__(self, client: object) -> None:
            pass

        def collect(self, *, spec: object, spec_path: Path, output: Path) -> Path:
            assert spec.start.startswith("2026-09-14")
            refreshed_days = dates if revision == "no_new_session" else [*dates, "2026-09-21"]
            make_collection(output, refreshed_days, altered=revision == "price", missing_bar=revision == "missing_bar")
            return output / "data_manifest.json"

    monkeypatch.setattr(lib, "ETFDataCollector", FakeCollector)
    if revision == "no_new_session":
        result = lib.update_data(tmp_path, lib.DATASET, "2026-09-20", object())
        assert result == {"status": "NO_NEW_SESSIONS", "snapshot_id": "parent"}
        assert lib.list_data(tmp_path)["latest_dataset"] == "parent"
    elif revision != "none":
        with pytest.raises(ValueError, match="PROVIDER_REVISION"):
            lib.update_data(tmp_path, lib.DATASET, "2026-09-21", object())
        assert lib.list_data(tmp_path)["latest_dataset"] == "parent"
        assert list((tmp_path / ".staging").glob("update-*/differences.json"))
    else:
        result = lib.update_data(tmp_path, lib.DATASET, "2026-09-21", object())
        assert result["status"] == "PUBLISHED"
        assert lib.list_data(tmp_path)["latest_dataset"] == result["snapshot_id"]
        lib.verify_data(tmp_path)
        assert lib._verify_inventory(parent)
