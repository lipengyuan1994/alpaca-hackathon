"""Derive a frozen V8 VWAP-protected exit policy from V7 signal records."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from packages.contracts.canonical import canonical_hash

from .artifacts import atomic_json
from .group_a_option_requests import GroupARequestError
from .group_a_proxy_backtest import _load


def generate_requests(*, v7_request_path: Path, output_path: Path) -> Path:
    """Create a new, immutable V8 request manifest without market-data I/O."""
    if output_path.exists():
        raise GroupARequestError("OPTION_REQUEST_OUTPUT_EXISTS")
    source = _load(v7_request_path)
    records = deepcopy(source["selection_records"])
    for record in records:
        record["strategy"] = "qqq_relative_strength_residual_vwap_exit_3s_v8"
        record["strategy_family"] = "trend_vwap_or_session_exit_v8"
    payload: dict[str, Any] = {
        "schema_version": "group-a-relative-strength-v8-option-requests/v1",
        "research_status": "EXPANDED_SCOPE_EXPLORATORY_V8_NOT_PROMOTION_ELIGIBLE",
        "base_data_manifest_hash": source["base_data_manifest_hash"],
        "source_request_manifest_hash": source["manifest_hash"],
        "selection_rule": "GROUP_A_V8_V7_SIGNAL_WITH_ADVERSE_COMPLETED_VWAP_EXIT_V1",
        "requests": deepcopy(source["requests"]),
        "selection_records": records,
        "manifest_hash": None,
    }
    payload["manifest_hash"] = canonical_hash({key: value for key, value in payload.items() if key != "manifest_hash"})
    atomic_json(output_path, payload)
    return output_path
