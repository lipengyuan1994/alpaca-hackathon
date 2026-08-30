"""Portable, credential-free Group A reversion reproduction preflight."""

from __future__ import annotations

import argparse
import platform
from pathlib import Path

from packages.contracts.canonical import canonical_hash
from packages.research_data.artifacts import atomic_json, ensure_empty_output, file_hash
from packages.research_data.group_a_preflight import GroupAInputError, validate_frozen_inputs

from .plugin import FEATURE_CONTRACT_HASH


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-manifest", required=True, type=Path)
    parser.add_argument("--feasibility-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    output = ensure_empty_output(args.output)
    try:
        inputs = validate_frozen_inputs(
            data_manifest_path=args.data_manifest,
            feasibility_manifest_path=args.feasibility_manifest,
            required_symbols=("SPY", "QQQ"),
        )
    except GroupAInputError as exc:
        atomic_json(output / "reproduction_refusal.json", {"status": "REFUSED", "reason": str(exc)})
        return 2
    run = {
        "schema_version": "group-a-reproduction-preflight/v1",
        "candidate_id": "vwap_reversion__spy_qqq_smh_igv__o2_v1",
        "status": "INPUTS_VALIDATED_OUTCOME_RUN_PENDING",
        "reason": "OPTION_PROXY_OBSERVATION_MANIFEST_REQUIRED",
        "feature_contract_hash": FEATURE_CONTRACT_HASH,
        "data_manifest_hash": inputs["data_manifest"]["manifest_hash"],
        "data_manifest_file_hash": file_hash(args.data_manifest),
        "feasibility_manifest_file_hash": file_hash(args.feasibility_manifest),
        "option_proxy_selected_symbols": list(inputs["option_proxy_selected_symbols"]),
        "platform": {"system": platform.system(), "machine": platform.machine()},
        "run_hash": None,
    }
    run["run_hash"] = canonical_hash({key: value for key, value in run.items() if key != "run_hash"})
    atomic_json(output / "run_manifest.json", run)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
