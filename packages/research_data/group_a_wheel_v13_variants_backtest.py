"""Expand and replay the five predeclared QQQ wheel V13 variants."""

from __future__ import annotations

import argparse
import json
import platform as runtime_platform
from pathlib import Path
from typing import Any

from packages.contracts.canonical import canonical_hash

from .artifacts import atomic_json, file_hash
from .group_a_proxy_backtest import _load
from .group_a_wheel_v12_backtest import WheelReplayError
from .group_a_wheel_v12_backtest import run as run_wheel


def expand_variant_records(requests: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand shared weekly observations without consulting option outcomes."""
    variants = requests.get("variants")
    selections = requests.get("selection_records")
    request_rows = requests.get("requests")
    if (
        not isinstance(variants, list)
        or len(variants) != 5
        or not isinstance(selections, list)
        or not isinstance(request_rows, list)
        or len(selections) != len(request_rows)
    ):
        raise WheelReplayError("V13_REQUEST_MANIFEST_INVALID")
    results: list[dict[str, Any]] = []
    for selection, request in zip(selections, request_rows, strict=True):
        contract_map = selection["contract_map"]
        for variant in variants:
            if variant["regime"] == "prior_50_session_trend":
                trend_up = selection.get("prior_50_session_trend_up")
                if trend_up is None:
                    continue
                put_pct, call_pct = (1, 3) if trend_up else (3, 1)
            else:
                put_pct = int(variant["put_otm_pct"])
                call_pct = int(variant["call_otm_pct"])
            results.append(
                {
                    **selection,
                    "request_id": request["request_id"],
                    "strategy": f"qqq_wheel_{variant['variant_id'].replace('.', '_')}",
                    "strategy_family": "RESEARCH_ONLY_QQQ_WHEEL_V13_VARIANTS",
                    "variant_id": variant["variant_id"],
                    "put_symbol": contract_map[f"put_{put_pct}pct"],
                    "call_symbol": contract_map[f"call_{call_pct}pct"],
                    "put_moneyness": 1.0 - put_pct / 100.0,
                    "call_moneyness": 1.0 + call_pct / 100.0,
                    "take_profit_fraction": float(variant["take_profit_fraction"]),
                }
            )
    return sorted(results, key=lambda item: (item["strategy"], item["decision_time"]))


def run(*, option_manifest_path: Path, request_path: Path, output: Path, base_data_manifest_path: Path | None = None) -> Path:
    """Run all five V13 variants through the shared V12 wheel accounting engine."""
    requests = _load(request_path)
    records = expand_variant_records(requests)
    metrics_path = run_wheel(
        option_manifest_path=option_manifest_path,
        request_path=request_path,
        output=output,
        base_data_manifest_path=base_data_manifest_path,
        records_override=records,
        report_schema_version="group-a-wheel-v13-variants-replay/v1",
        report_family="V13_QQQ_FIVE_VARIANTS",
    )
    report = json.loads(metrics_path.read_text(encoding="utf-8"))
    rows = [{"strategy": strategy, **values} for strategy, values in report["metrics"].items()]
    rows.sort(
        key=lambda row: (
            -float(row["net_return"]),
            -float(row["sharpe"] if row["sharpe"] is not None else float("-inf")),
            -float(row["max_drawdown"]),
            row["strategy"],
        )
    )
    comparison: dict[str, Any] = {
        "schema_version": "group-a-wheel-v13-variants-comparison/v1",
        "primary_ranking_metric": "net_return",
        "tie_break_metrics": ["sharpe", "max_drawdown"],
        "best_strategy": rows[0]["strategy"],
        "ranking": [{"rank": index, **row} for index, row in enumerate(rows, start=1)],
        "metrics_report_hash": report["report_hash"],
        "comparison_hash": None,
    }
    comparison["comparison_hash"] = canonical_hash(
        {key: value for key, value in comparison.items() if key != "comparison_hash"}
    )
    atomic_json(output / "comparison.json", comparison)
    artifact_paths = (
        "metrics.json",
        "comparison.json",
        "normalized/trades.parquet",
        "normalized/daily_returns.parquet",
        "plots/cumulative_pnl.svg",
        "plots/cumulative_pnl_spec.json",
    )
    run_manifest: dict[str, Any] = {
        "schema_version": "group-a-wheel-v13-run-manifest/v1",
        "source_module": "packages.research_data.group_a_wheel_v13_variants_backtest",
        "platform": {
            "system": runtime_platform.system(),
            "machine": runtime_platform.machine(),
            "python_version": runtime_platform.python_version(),
        },
        "option_observation_manifest_hash": report["option_observation_manifest_hash"],
        "option_request_manifest_hash": report["option_request_manifest_hash"],
        "metrics_report_hash": report["report_hash"],
        "comparison_hash": comparison["comparison_hash"],
        "artifacts": {relative: file_hash(output / relative) for relative in artifact_paths},
        "run_manifest_hash": None,
    }
    run_manifest["run_manifest_hash"] = canonical_hash(
        {key: value for key, value in run_manifest.items() if key != "run_manifest_hash"}
    )
    atomic_json(output / "run_manifest.json", run_manifest)
    return metrics_path


def main() -> int:
    """Replay all five V13 variants from one finalized option manifest."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--option-manifest", required=True, type=Path)
    parser.add_argument("--request-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--base-data-manifest", type=Path)
    args = parser.parse_args()
    target = run(
        option_manifest_path=args.option_manifest,
        request_path=args.request_manifest,
        output=args.output,
        base_data_manifest_path=args.base_data_manifest,
    )
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
