"""Atomic, hash-addressed artifacts for the research data steward."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

from packages.contracts.canonical import canonical_hash, canonical_json


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def ensure_empty_output(path: Path) -> Path:
    output = path.expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("RESEARCH_OUTPUT_DIRECTORY_NOT_EMPTY")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _fsync_directory(path: Path) -> None:
    """Best-effort directory fsync; a no-op where the platform refuses it."""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    atomic_bytes(path, (canonical_json(value) + "\n").encode("utf-8"))


def write_raw_pages(root: Path, dataset_id: str, pages: Iterable[Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, page in enumerate(pages):
        relative = Path("raw") / dataset_id / f"{index:06d}.json"
        target = root / relative
        atomic_bytes(target, page.raw_bytes)
        records.append(
            {
                "path": relative.as_posix(),
                "sha256": file_hash(target),
                "endpoint": page.endpoint,
                "page_token": page.page_token,
                "request": {
                    key: ("<redacted>" if key == "page_token" else value)
                    for key, value in sorted(page.request_params.items())
                },
                "rate_limit": {
                    key: value
                    for key, value in sorted(page.response_headers.items())
                    if key.startswith("x-ratelimit")
                },
            }
        )
    return records


def write_parquet(root: Path, dataset_id: str, rows: list[dict[str, Any]], columns: tuple[str, ...]) -> dict[str, Any]:
    relative = Path("normalized") / f"{dataset_id}.parquet"
    target = root / relative
    frame = pd.DataFrame(rows, columns=columns)
    frame = frame.sort_values(list(columns[:2]), kind="stable").reset_index(drop=True) if not frame.empty else frame
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.stem}.", suffix=".tmp", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        os.close(descriptor)
        frame.to_parquet(temporary, index=False, engine="pyarrow", compression="zstd", row_group_size=65536)
        with temporary.open("r+b") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {
        "path": relative.as_posix(),
        "sha256": file_hash(target),
        "rows": len(frame),
        "columns": list(columns),
        "schema_hash": canonical_hash({"columns": list(columns)}),
    }
