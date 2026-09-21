"""Command line entry point for deterministic ETF cash research."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import pandas as pd

from packages.research_data.cli import _client_from_environment

from .collector import ETFCollectionSpec, ETFDataCollector, load_manifest
from .library import DATASET, T9_ROOT, archive_existing, list_data, update_data, verify_data
from .protocol import DEFAULT_PROTOCOL, ResearchProtocol
from .protocol_v2 import DEFAULT_STUDY_PROTOCOL, StudyProtocolV2
from .reporting import (
    build_report,
    finalize_selection,
    freeze_selection,
    load_bars_from_manifest,
    run_suite,
)
from .reporting_v2 import build_v2_report
from .study_v2 import (
    load_v2_bars_from_manifest,
    merge_studies,
    rank_study,
    reproduce_study,
    run_study,
)


def _protocol(path: Path | None) -> ResearchProtocol:
    return ResearchProtocol.from_yaml(path) if path else DEFAULT_PROTOCOL


def _load_bars(path: Path) -> pd.DataFrame:
    if path.name == "data_manifest.json":
        return load_bars_from_manifest(path)
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    try:
        return pd.DataFrame(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise SystemExit("ETF_BARS_INPUT_INVALID") from exc


def validate_data_main(args: argparse.Namespace) -> int:
    bars = _load_bars(args.bars or args.data_manifest)
    required = {"date", "symbol", "open", "high", "low", "close"}
    missing = required - set(bars.columns)
    if missing:
        raise SystemExit(f"ETF_BARS_COLUMNS_MISSING:{','.join(sorted(missing))}")
    bars["date"] = pd.to_datetime(bars["date"], utc=True)
    if bars.duplicated(["date", "symbol"]).any():
        raise SystemExit("ETF_BARS_DUPLICATE_SESSION")
    coverage = bars.groupby("symbol")["date"].agg(["min", "max", "count"]).reset_index().to_dict(orient="records")
    print(json.dumps({"status": "OK", "rows": len(bars), "coverage": coverage}, default=str))
    return 0


def collect_main(args: argparse.Namespace) -> int:
    spec = ETFCollectionSpec.from_yaml(args.spec)
    manifest = ETFDataCollector(_client_from_environment()).collect(spec=spec, spec_path=args.spec, output=args.output)
    print(manifest)
    return 0


def backtest_main(args: argparse.Namespace) -> int:
    protocol = _protocol(args.protocol)
    bars = _load_bars(args.bars or args.data_manifest)
    target = run_suite(bars=bars, output=args.output, phase=args.phase, protocol=protocol, variants=args.variants, selection_path=args.selection)
    print(target)
    return 0


def freeze_main(args: argparse.Namespace) -> int:
    target = freeze_selection(suite_dir=args.suite, output_path=args.output, protocol=_protocol(args.protocol))
    print(target)
    return 0


def report_main(args: argparse.Namespace) -> int:
    target = build_report(suite_dir=args.suite, output_path=args.output)
    print(target)
    return 0


def finalize_main(args: argparse.Namespace) -> int:
    target = finalize_selection(selection_path=args.selection, holdout_suite=args.holdout_suite, continuous_suite=args.continuous_suite, output_path=args.output, protocol=_protocol(args.protocol))
    print(target)
    return 0


def reproduce_main(args: argparse.Namespace) -> int:
    load_manifest(args.data_manifest)
    bars = load_bars_from_manifest(args.data_manifest)
    protocol = _protocol(args.protocol)
    target = run_suite(bars=bars, output=args.output, phase=args.phase, protocol=protocol, variants=False, selection_path=args.selection)
    print(target)
    return 0


def _study_protocol(path: Path | None) -> StudyProtocolV2:
    return StudyProtocolV2.from_yaml(path) if path else DEFAULT_STUDY_PROTOCOL


def collect_v2_main(args: argparse.Namespace) -> int:
    spec = ETFCollectionSpec.from_yaml_v2(args.spec)
    manifest = ETFDataCollector(_client_from_environment()).collect(spec=spec, spec_path=args.spec, output=args.output)
    print(manifest)
    return 0


def run_study_main(args: argparse.Namespace) -> int:
    protocol = _study_protocol(args.protocol)
    bars = load_v2_bars_from_manifest(args.data_manifest) if args.data_manifest else _load_bars(args.bars)
    target = run_study(
        bars=bars,
        output=args.output,
        track=args.track,
        protocol=protocol,
        include_sensitivities=args.sensitivities,
        run_windows=not args.no_windows,
        run_continuous=not args.no_continuous,
        run_delay=not args.no_delay,
        run_delay_windows=args.delay_windows,
        run_stress=not args.no_stress,
        pair_id=args.pair,
    )
    if args.data_manifest:
        shutil.copy2(args.data_manifest, target / "data_manifest.json")
    print(target)
    return 0


def rank_study_main(args: argparse.Namespace) -> int:
    target = rank_study(args.study, protocol=_study_protocol(args.protocol))
    print(target)
    return 0


def report_v2_main(args: argparse.Namespace) -> int:
    target = build_v2_report(args.study, args.output)
    print(target)
    return 0


def reproduce_study_main(args: argparse.Namespace) -> int:
    target = reproduce_study(args.study, output=args.output, protocol=_study_protocol(args.protocol), track=args.track)
    print(target)
    return 0


def merge_study_main(args: argparse.Namespace) -> int:
    target = merge_studies(args.studies, output=args.output, protocol=_study_protocol(args.protocol))
    print(target)
    return 0


def audit_engine_main(args: argparse.Namespace) -> int:
    from .audit_v2 import run_engine_audit

    result = run_engine_audit()
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status") == "OK" else 1


def archive_main(args: argparse.Namespace) -> int:
    sources = {name: getattr(args, name) for name in ("development", "validation", "holdout", "continuous", "reports")}
    selections = {"selection.json": args.selection, "final_selection.json": args.final_selection}
    print(json.dumps(archive_existing(args.library, args.collection, sources, run_id=args.run_id, selection_files=selections), sort_keys=True))
    return 0


def update_data_main(args: argparse.Namespace) -> int:
    print(json.dumps(update_data(args.library, args.dataset, args.through, _client_from_environment()), sort_keys=True))
    return 0


def list_data_main(args: argparse.Namespace) -> int:
    print(json.dumps(list_data(args.library), sort_keys=True))
    return 0


def verify_data_main(args: argparse.Namespace) -> int:
    print(json.dumps(verify_data(args.library, snapshot_id=args.snapshot_id), sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    from .cli_v3 import dispatch
    dispatched = dispatch(argv)
    if dispatched is not None:
        return dispatched
    parser = argparse.ArgumentParser(description="Deterministic QQQM and semiconductor ETF cash research")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate-data", help="validate a local daily-bar input or immutable manifest")
    validate_source = validate.add_mutually_exclusive_group(required=True)
    validate_source.add_argument("--bars", type=Path)
    validate_source.add_argument("--data-manifest", type=Path)
    validate.set_defaults(function=validate_data_main)

    collect = sub.add_parser("collect", help="GET-only collect immutable Alpaca inputs")
    collect.add_argument("--spec", required=True, type=Path)
    collect.add_argument("--output", required=True, type=Path)
    collect.set_defaults(function=collect_main)

    collect_v2 = sub.add_parser("collect-v2", help="GET-only collect the extended eight-symbol ETF universe")
    collect_v2.add_argument("--spec", required=True, type=Path)
    collect_v2.add_argument("--output", required=True, type=Path)
    collect_v2.set_defaults(function=collect_v2_main)

    backtest = sub.add_parser("backtest", help="run the complete candidate suite")
    source = backtest.add_mutually_exclusive_group(required=True)
    source.add_argument("--bars", type=Path)
    source.add_argument("--data-manifest", type=Path)
    backtest.add_argument("--phase", choices=("development", "validation", "holdout", "continuous"), required=True)
    backtest.add_argument("--output", required=True, type=Path)
    backtest.add_argument("--protocol", type=Path)
    backtest.add_argument("--variants", action="store_true")
    backtest.add_argument("--selection", type=Path, help="hash-checked frozen selection required for holdout")
    backtest.set_defaults(function=backtest_main)

    freeze = sub.add_parser("freeze-selection", help="freeze the validation shortlist")
    freeze.add_argument("--suite", required=True, type=Path)
    freeze.add_argument("--output", required=True, type=Path)
    freeze.add_argument("--protocol", type=Path)
    freeze.set_defaults(function=freeze_main)

    report = sub.add_parser("report", help="write an HTML leaderboard report")
    report.add_argument("--suite", required=True, type=Path)
    report.add_argument("--output", required=True, type=Path)
    report.set_defaults(function=report_main)

    finalize = sub.add_parser("finalize-selection", help="apply the frozen shortlist to holdout and full-period runs")
    finalize.add_argument("--selection", required=True, type=Path)
    finalize.add_argument("--holdout-suite", required=True, type=Path)
    finalize.add_argument("--continuous-suite", required=True, type=Path)
    finalize.add_argument("--output", required=True, type=Path)
    finalize.add_argument("--protocol", type=Path)
    finalize.set_defaults(function=finalize_main)

    reproduce = sub.add_parser("reproduce", help="replay from a hash-checked manifest")
    reproduce.add_argument("--data-manifest", required=True, type=Path)
    reproduce.add_argument("--phase", choices=("development", "validation", "holdout", "continuous"), required=True)
    reproduce.add_argument("--output", required=True, type=Path)
    reproduce.add_argument("--protocol", type=Path)
    reproduce.add_argument("--selection", type=Path)
    reproduce.set_defaults(function=reproduce_main)

    audit = sub.add_parser("audit-engine", help="run deterministic v2 accounting and execution fixtures")
    audit.set_defaults(function=audit_engine_main)

    study = sub.add_parser("run-study", help="run Track A, Track B, or both v2 research tracks")
    source_v2 = study.add_mutually_exclusive_group(required=True)
    source_v2.add_argument("--bars", type=Path)
    source_v2.add_argument("--data-manifest", type=Path)
    study.add_argument("--track", choices=("a", "b", "all"), default="all")
    study.add_argument("--pair", choices=("TQQQ_SOXL", "SPXL_SOXL"), help="run only one Track B leveraged pair")
    study.add_argument("--output", required=True, type=Path)
    study.add_argument("--protocol", type=Path)
    study.add_argument("--sensitivities", action="store_true")
    study.add_argument("--no-windows", action="store_true")
    study.add_argument("--no-continuous", action="store_true")
    study.add_argument("--no-delay", action="store_true")
    study.add_argument("--delay-windows", action="store_true", help="also run one-session delay for each evaluation window")
    study.add_argument("--no-stress", action="store_true", help="skip 2020/2022 diagnostic stress periods")
    study.set_defaults(function=run_study_main)

    rank = sub.add_parser("rank-study", help="rank v2 candidates and write the frozen exploratory selection")
    rank.add_argument("--study", required=True, type=Path)
    rank.add_argument("--protocol", type=Path)
    rank.set_defaults(function=rank_study_main)

    report_v2 = sub.add_parser("report-v2", help="write the v2 HTML/Markdown study report")
    report_v2.add_argument("--study", required=True, type=Path)
    report_v2.add_argument("--output", required=True, type=Path)
    report_v2.set_defaults(function=report_v2_main)

    reproduce_v2 = sub.add_parser("reproduce-study", help="replay a completed v2 study from its saved input snapshot")
    reproduce_v2.add_argument("--study", required=True, type=Path)
    reproduce_v2.add_argument("--output", required=True, type=Path)
    reproduce_v2.add_argument("--protocol", type=Path)
    reproduce_v2.add_argument("--track", choices=("a", "b", "all"))
    reproduce_v2.set_defaults(function=reproduce_study_main)

    merge_v2 = sub.add_parser("merge-study", help="merge pair-parallel v2 studies after hash checks")
    merge_v2.add_argument("--study", dest="studies", action="append", type=Path, required=True, help="source study directory; repeat for each track or pair")
    merge_v2.add_argument("--output", required=True, type=Path)
    merge_v2.add_argument("--protocol", type=Path)
    merge_v2.set_defaults(function=merge_study_main)

    archive = sub.add_parser("archive-existing", help="copy and hash-verify an existing collection and research release")
    archive.add_argument("--library", type=Path, default=T9_ROOT / "TradingResearch")
    archive.add_argument("--collection", required=True, type=Path)
    for name in ("development", "validation", "holdout", "continuous", "reports"):
        archive.add_argument(f"--{name}", required=True, type=Path)
    archive.add_argument("--run-id", required=True)
    archive.add_argument("--selection", required=True, type=Path)
    archive.add_argument("--final-selection", required=True, type=Path)
    archive.set_defaults(function=archive_main)

    update = sub.add_parser("update-data", help="collect new sessions into a new immutable T9 snapshot")
    update.add_argument("--library", type=Path, default=T9_ROOT / "TradingResearch")
    update.add_argument("--dataset", default=DATASET)
    update.add_argument("--through", required=True)
    update.set_defaults(function=update_data_main)

    listing = sub.add_parser("list-data", help="list T9 snapshots and archived studies")
    listing.add_argument("--library", type=Path, default=T9_ROOT / "TradingResearch")
    listing.set_defaults(function=list_data_main)

    verification = sub.add_parser("verify-data", help="verify T9 catalog, snapshot inputs, and studies")
    verification.add_argument("--library", type=Path, default=T9_ROOT / "TradingResearch")
    verification.add_argument("--snapshot-id")
    verification.set_defaults(function=verify_data_main)
    args = parser.parse_args(argv)
    return int(args.function(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
