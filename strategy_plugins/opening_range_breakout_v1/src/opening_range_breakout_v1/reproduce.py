"""Offline, credential-free reproduction preflight for the ORB package.

Implements Group B plan section 9.3: refuses a nonempty output directory,
validates the frozen candidate/config hashes, runs the package tests, and
writes deterministically ordered evidence.  This module is a preflight, not a
backtester; no historical P&L is computed here.  Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
_CANDIDATE_DIR = _REPO_ROOT / "research" / "candidates" / "opening_range_breakout__all_feasible__o2_v1"
_TESTS_DIR = _PACKAGE_ROOT / "tests"

FROZEN_FILE_HASHES = {
    "feature_contract.yaml": "sha256:0ef3d29fd8680508b0c02cacda2aef31f495f0960373848dc46837ff4a259654",
    "central_config.json": "sha256:607b30f351d8ca5203d75293a45018bb5a3cc381409c253530d8bdea38f6e081",
    "hypothesis.yaml": "sha256:79302bbecd9f775ddfa691a982451819261339f7175f91ef5c56a22e6b42525a",
    "sensitivities.yaml": "sha256:4439fa95496edb1e8992362838c1767a5a975802ca7f37e0fbd4c6ef6f21829b",
    "reason_codes.yaml": "sha256:4e2ea70681c1918ec7543865726f6deabd2575b5c76a5fd26da8844bf8b9c856",
    "state_schema.json": "sha256:1bb2bf5fb452e3a30ef71c436a0ddc6ff9ae46afb9ed32714b1c248d7c85cf8d",
}
CENTRAL_BINDING_HASHES = {
    "packages/strategy_sdk/arbitration.py": (
        "sha256:864fe5d419717bb424eb10ed54b5ad8ac5095bfc235d3f10a2d894e39826edd5"
    ),
    "packages/position_manager/manager.py": (
        "sha256:c77bfe135c4cb13eb530e9d390f2c6ceab1e8aaced762489b849ee0d82bd69b7"
    ),
}
RECORDED_BINDINGS = {
    "template_catalog_loaded": (
        "sha256:74906ee706cef3a52b77cb84e2f7b80c66bbc6b0e63ad3982be9e0ef0e02076e"
    ),
    "feature_contract_fixture_registry": (
        "sha256:fdbe412038def1df8b3c1e552cbbfa42c300d4e73e5ac74cd92b4db233893a04"
    ),
}
REQUIRED_PAIR_SYMBOLS = ("SMH", "SOXL")
_SYMBOL_FIELDS = (
    "symbols",
    "selected_symbols",
    "eligible_symbols",
    "option_proxy_selected_symbols",
    "collected_symbols",
)


def _lf_sha256(path: Path) -> str:
    normalized = path.read_bytes().replace(b"\r\n", b"\n")
    return f"sha256:{hashlib.sha256(normalized).hexdigest()}"


def _raw_sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _ensure_empty_output(path: Path) -> Path:
    if path.exists() and any(path.iterdir()):
        raise SystemExit(f"REPRODUCTION_OUTPUT_NOT_EMPTY: {path}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _atomic_json(path: Path, payload: object) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    path.with_suffix(path.suffix + ".tmp").write_bytes(text.encode("utf-8"))
    path.with_suffix(path.suffix + ".tmp").replace(path)


def _load_manifest(path: Path, kind: str) -> dict:
    if not path.is_file():
        raise ValueError(f"{kind}_MANIFEST_MISSING: {path}")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"{kind}_MANIFEST_INVALID_JSON: {path}") from exc
    if not isinstance(doc, dict) or not doc:
        raise ValueError(f"{kind}_MANIFEST_INVALID_OBJECT: {path}")
    return doc


def _observed_symbols(doc: dict) -> tuple[str, ...]:
    found: list[str] = []
    for field in _SYMBOL_FIELDS:
        value = doc.get(field)
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            found.extend(value)
    return tuple(dict.fromkeys(found))


def _verify_frozen_files(self_hash: str) -> dict:
    report: dict[str, dict] = {}
    for name, expected in FROZEN_FILE_HASHES.items():
        path = _CANDIDATE_DIR / name
        if not path.is_file():
            report[name] = {"expected": expected, "actual": None, "status": "MISSING"}
        else:
            actual = _lf_sha256(path)
            report[name] = {
                "expected": expected,
                "actual": actual,
                "status": "VERIFIED" if actual == expected else "MISMATCH",
            }
    if any(item["status"] != "VERIFIED" for item in report.values()):
        raise ValueError(f"FROZEN_CANDIDATE_HASH_MISMATCH: {self_hash}")
    return report


def _verify_central_bindings() -> dict:
    report: dict[str, dict] = {}
    for relative, expected in CENTRAL_BINDING_HASHES.items():
        path = _REPO_ROOT / relative
        if not path.is_file():
            report[relative] = {"expected": expected, "actual": None, "status": "MISSING"}
            continue
        raw = _raw_sha256(path)
        lf = _lf_sha256(path)
        status = "VERIFIED" if expected in (raw, lf) else "MISMATCH"
        report[relative] = {"expected": expected, "actual_lf": lf, "actual_raw": raw, "status": status}
    if any(item["status"] != "VERIFIED" for item in report.values()):
        raise ValueError("CENTRAL_BINDING_HASH_MISMATCH")
    return report


def _commit_hash() -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "UNAVAILABLE"
    if completed.returncode != 0:
        return "UNAVAILABLE"
    return completed.stdout.strip()


def _run_package_tests() -> dict:
    command = [sys.executable, "-m", "pytest", "-q", str(_TESTS_DIR)]
    try:
        completed = subprocess.run(
            command,
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"command": command, "status": "NOT_RUN", "reason": str(exc)}
    tail = [line for line in completed.stdout.splitlines() if line.strip()][-20:]
    return {
        "command": command,
        "status": "PASSED" if completed.returncode == 0 else "FAILED",
        "returncode": completed.returncode,
        "stdout_tail": tail,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-manifest", required=True, type=Path)
    parser.add_argument("--feasibility-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    output = _ensure_empty_output(args.output)
    try:
        data_doc = _load_manifest(args.data_manifest, "DATA")
        feasibility_doc = _load_manifest(args.feasibility_manifest, "FEASIBILITY")
        observed = _observed_symbols(data_doc)
        if not observed:
            raise ValueError(f"DATA_MANIFEST_SYMBOLS_FIELD_MISSING: one of {list(_SYMBOL_FIELDS)}")
        missing = [symbol for symbol in REQUIRED_PAIR_SYMBOLS if symbol not in observed]
        if missing:
            raise ValueError(f"DATA_MANIFEST_SYMBOLS_MISSING_PAIR: {missing}")
        frozen = _verify_frozen_files("opening_range_breakout")
        bindings = _verify_central_bindings()
    except ValueError as exc:
        _atomic_json(output / "reproduction_refusal.json", {"status": "REFUSED", "reason": str(exc)})
        return 2
    tests = _run_package_tests()
    run = {
        "schema_version": "group-b-reproduction-preflight/v1",
        "candidate_id": "opening_range_breakout__all_feasible__o2_v1",
        "plugin_id": "opening_range_breakout",
        "plugin_version": "1.0.0",
        "status": (
            "INPUTS_VALIDATED_TESTS_PASSED_OUTCOME_RUN_PENDING"
            if tests["status"] == "PASSED"
            else "INPUTS_VALIDATED_PACKAGE_TESTS_FAILED"
        ),
        "reason": "STEWARD_OUTCOME_ARTIFACTS_AND_OUTCOME_RUN_PENDING",
        "feature_contract_hash": FROZEN_FILE_HASHES["feature_contract.yaml"],
        "frozen_file_hashes": frozen,
        "central_bindings": bindings,
        "recorded_bindings": RECORDED_BINDINGS,
        "data_manifest": {
            "path": str(args.data_manifest),
            "file_hash": _raw_sha256(args.data_manifest),
            "symbols_observed": list(observed),
        },
        "feasibility_manifest": {
            "path": str(args.feasibility_manifest),
            "file_hash": _raw_sha256(args.feasibility_manifest),
            "symbols_observed": list(_observed_symbols(feasibility_doc)),
        },
        "commit": _commit_hash(),
        "uv_lock_hash": _lf_sha256(_REPO_ROOT / "uv.lock"),
        "package_tests": tests,
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "run_hash": None,
    }
    run["run_hash"] = f"sha256:{hashlib.sha256(json.dumps(run, sort_keys=True).encode('utf-8')).hexdigest()}"
    _atomic_json(output / "run_manifest.json", run)
    return 0 if tests["status"] == "PASSED" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
