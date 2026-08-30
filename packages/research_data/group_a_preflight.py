"""Fail-closed immutable-input verification shared by Group A packages."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from packages.contracts.canonical import canonical_hash

from .artifacts import file_hash


class GroupAInputError(ValueError):
    """A Group A outcome run must not proceed with these inputs."""


def _load_json(path: Path, reason: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GroupAInputError(reason) from exc
    if not isinstance(value, dict):
        raise GroupAInputError(reason)
    return value


def validate_frozen_inputs(
    *, data_manifest_path: Path, feasibility_manifest_path: Path, required_symbols: tuple[str, ...]
) -> dict[str, Any]:
    """Validate frozen collector output plus deterministic feasibility evidence."""
    data_manifest = _load_json(data_manifest_path, "DATA_MANIFEST_INVALID")
    supplied_hash = data_manifest.get("manifest_hash")
    expected_hash = canonical_hash(
        {key: value for key, value in data_manifest.items() if key != "manifest_hash"}
    )
    if supplied_hash != expected_hash:
        raise GroupAInputError("DATA_MANIFEST_HASH_MISMATCH")
    if data_manifest.get("schema_version") != "research-data-manifest/v1":
        raise GroupAInputError("DATA_MANIFEST_SCHEMA_MISMATCH")
    # `COLLECTED_UNATTESTED` is the prior collector's completed, hash-bound
    # status.  It is a compatibility marker, not incomplete data; new
    # collectors emit `COLLECTED` directly.
    if data_manifest.get("status") not in {"COLLECTED", "COLLECTED_UNATTESTED"}:
        raise GroupAInputError("DATA_MANIFEST_COLLECTION_STATUS_INVALID")

    feasibility = _load_json(feasibility_manifest_path, "FEASIBILITY_MANIFEST_INVALID")
    if feasibility.get("schema_version") != "option-proxy-feasibility/v1":
        raise GroupAInputError("FEASIBILITY_MANIFEST_SCHEMA_MISMATCH")
    feasibility_hash = canonical_hash(
        {key: value for key, value in feasibility.items() if key != "manifest_hash"}
    )
    if feasibility.get("manifest_hash") != feasibility_hash:
        raise GroupAInputError("FEASIBILITY_MANIFEST_HASH_MISMATCH")
    if feasibility.get("status") != "READY_FOR_REPLAY":
        raise GroupAInputError("FEASIBILITY_MANIFEST_NOT_READY")
    if feasibility.get("data_manifest_hash") != supplied_hash:
        raise GroupAInputError("FEASIBILITY_DATA_MANIFEST_BINDING_MISMATCH")
    attestations = feasibility.get("attestations", [])
    if not isinstance(attestations, list):
        raise GroupAInputError("FEASIBILITY_ATTESTATIONS_INVALID")

    by_id = {
        item.get("dataset_id"): item
        for item in data_manifest.get("datasets", [])
        if isinstance(item, dict) and isinstance(item.get("dataset_id"), str)
    }
    split = by_id.get("stock_bars_split")
    if not isinstance(split, dict) or split.get("feed") != "iex" or split.get("adjustment") != "split":
        raise GroupAInputError("SPLIT_IEX_BARS_NOT_DECLARED")
    artifact = split.get("artifact")
    if not isinstance(artifact, dict) or not isinstance(artifact.get("path"), str):
        raise GroupAInputError("SPLIT_IEX_BARS_ARTIFACT_INVALID")
    dataset_path = data_manifest_path.parent / artifact["path"]
    if not dataset_path.is_file() or file_hash(dataset_path) != artifact.get("sha256"):
        raise GroupAInputError("SPLIT_IEX_BARS_HASH_MISMATCH")

    selected = feasibility.get("selected_symbols")
    if not isinstance(selected, list) or any(not isinstance(item, str) for item in selected):
        raise GroupAInputError("FEASIBILITY_SELECTED_SYMBOLS_INVALID")
    selected_set = set(selected)
    if not set(required_symbols).issubset({"SPY", "QQQ"}):
        raise GroupAInputError("GROUP_A_SYMBOL_POLICY_INVALID")
    return {
        "data_manifest": data_manifest,
        "data_manifest_path": data_manifest_path.resolve(),
        "split_bars_path": dataset_path.resolve(),
        "selected_symbols": tuple(sorted(selected_set)),
        "option_proxy_selected_symbols": tuple(
            symbol for symbol in required_symbols if symbol in selected_set
        ),
    }


# Compatibility alias for existing package imports. New callers should use the
# accurate name above: review is optional, while hashes and schema are not.
validate_attested_inputs = validate_frozen_inputs
