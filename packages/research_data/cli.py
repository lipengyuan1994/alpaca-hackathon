"""GET-only Alpaca collection and deterministic research replay entry points."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Mapping

from packages.runtime_secrets import SecretConfigurationError, require_yaml_file_secret

from .client import ReadOnlyAlpacaClient
from .collector import CollectionSpec, ResearchDataCollector, ResearchDataError
from .feasibility import FeasibilityError, write_feasibility_draft
from .group_a_option_requests import GroupARequestError, generate_requests
from .group_a_parallel_v2_option_requests import generate_requests as generate_parallel_v2_requests
from .group_a_proxy_backtest import GroupAReplayError
from .group_a_proxy_backtest import run as run_group_a_proxy_backtest
from .group_a_sensitivity_option_requests import generate_requests as generate_sensitivity_requests
from .group_a_wheel_v12_option_requests import generate_requests as generate_wheel_v12_requests
from .group_a_wheel_v13_option_requests import generate_requests as generate_wheel_v13_requests
from .group_a_wheel_v13_variants_backtest import run as run_wheel_v13

_DEFAULT_SECRETS_DIRECTORY = Path("/Users/lipengyuan/.config/great_secrets")
_SECRETS_DIRECTORY_ENV = "REGIMESWITCH_SECRETS_DIR"


def _client_from_environment(
    environ: Mapping[str, str] | None = None,
) -> ReadOnlyAlpacaClient:
    """Create the GET-only collector client from the fixed Compose secret bundle.

    The directory location is non-secret configuration.  The loader accepts no
    secret-valued environment variables and never logs the YAML values.
    """
    values = os.environ if environ is None else environ
    root = Path(values.get(_SECRETS_DIRECTORY_ENV, str(_DEFAULT_SECRETS_DIRECTORY))).expanduser()
    bundle = root / "alpaca" / "alpaca_api_key.yaml"
    file_environment = {"RESEARCH_ALPACA_BUNDLE_FILE": str(bundle)}
    try:
        key = require_yaml_file_secret(
            "RESEARCH_ALPACA_BUNDLE",
            key_path=("paper_alpaca_api_key",),
            environ=file_environment,
            allowed_roots=(root,),
        )
        secret = require_yaml_file_secret(
            "RESEARCH_ALPACA_BUNDLE",
            key_path=("paper_alpaca_api_secret",),
            environ=file_environment,
            allowed_roots=(root,),
        )
    except SecretConfigurationError as exc:
        raise SystemExit("ALPACA_READ_ONLY_CREDENTIALS_UNAVAILABLE") from exc
    return ReadOnlyAlpacaClient(headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect immutable, read-only Alpaca research data")
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--option-observation-requests", type=Path)
    parser.add_argument("--quote-symbols", type=Path)
    args = parser.parse_args()
    try:
        manifest = ResearchDataCollector(_client_from_environment()).collect(
            spec=CollectionSpec.from_yaml(args.spec),
            spec_path=args.spec,
            output=args.output,
            option_request_path=args.option_observation_requests,
            quote_symbols_path=args.quote_symbols,
        )
    except ResearchDataError as exc:
        raise SystemExit(str(exc)) from exc
    print(manifest)
    return 0


def options_main() -> int:
    """Collect only frozen historical option observations for a base data manifest."""
    parser = argparse.ArgumentParser(description=options_main.__doc__)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--base-data-manifest", required=True, type=Path)
    parser.add_argument("--option-observation-requests", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--quote-symbols", type=Path)
    args = parser.parse_args()
    try:
        manifest = ResearchDataCollector(_client_from_environment()).collect_option_observations_only(
            spec=CollectionSpec.from_yaml(args.spec),
            spec_path=args.spec,
            base_data_manifest_path=args.base_data_manifest,
            option_request_path=args.option_observation_requests,
            quote_symbols_path=args.quote_symbols,
            output=args.output,
        )
    except ResearchDataError as exc:
        raise SystemExit(str(exc)) from exc
    print(manifest)
    return 0


def stage_main() -> int:
    """Collect one immutable, resumable base-data stage using read-only Alpaca data."""
    parser = argparse.ArgumentParser(description=stage_main.__doc__)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--stage", required=True, choices=("stock_raw", "stock_split", "calendar", "contracts"))
    args = parser.parse_args()
    try:
        staging = ResearchDataCollector(_client_from_environment()).collect_base_stage(
            spec=CollectionSpec.from_yaml(args.spec), spec_path=args.spec, output=args.output, stage=args.stage
        )
    except ResearchDataError as exc:
        raise SystemExit(str(exc)) from exc
    print(staging)
    return 0


def finalize_main() -> int:
    """Finalize staged immutable base-data collection without retrieving data."""
    parser = argparse.ArgumentParser(description=finalize_main.__doc__)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        manifest = ResearchDataCollector(_client_from_environment()).finalize_base_collection(
            spec_path=args.spec, output=args.output
        )
    except ResearchDataError as exc:
        raise SystemExit(str(exc)) from exc
    print(manifest)
    return 0


def group_a_option_requests_main() -> int:
    """Generate frozen Group A O2 historical option requests from immutable inputs."""
    parser = argparse.ArgumentParser(description=group_a_option_requests_main.__doc__)
    parser.add_argument("--data-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--observation-minutes", type=int, choices=(65, 95), default=65)
    args = parser.parse_args()
    try:
        target = generate_requests(
            data_manifest_path=args.data_manifest,
            output_path=args.output,
            observation_minutes=args.observation_minutes,
        )
    except GroupARequestError as exc:
        raise SystemExit(str(exc)) from exc
    print(target)
    return 0


def group_a_sensitivity_option_requests_main() -> int:
    """Generate predeclared Group A sensitivity option requests without reading option prices."""
    parser = argparse.ArgumentParser(description=group_a_sensitivity_option_requests_main.__doc__)
    parser.add_argument("--data-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        target = generate_sensitivity_requests(data_manifest_path=args.data_manifest, output_path=args.output)
    except GroupARequestError as exc:
        raise SystemExit(str(exc)) from exc
    print(target)
    return 0


def group_a_parallel_v2_option_requests_main() -> int:
    """Generate expanded-scope Group A V2 option requests before option data is read."""
    parser = argparse.ArgumentParser(description=group_a_parallel_v2_option_requests_main.__doc__)
    parser.add_argument("--data-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        target = generate_parallel_v2_requests(data_manifest_path=args.data_manifest, output_path=args.output)
    except GroupARequestError as exc:
        raise SystemExit(str(exc)) from exc
    print(target)
    return 0


def group_a_wheel_v12_option_requests_main() -> int:
    """Generate fixed weekly CSP/covered-call research requests without option I/O."""
    parser = argparse.ArgumentParser(description=group_a_wheel_v12_option_requests_main.__doc__)
    parser.add_argument("--data-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        target = generate_wheel_v12_requests(data_manifest_path=args.data_manifest, output_path=args.output)
    except GroupARequestError as exc:
        raise SystemExit(str(exc)) from exc
    print(target)
    return 0


def group_a_wheel_v13_option_requests_main() -> int:
    """Generate the shared frozen request set for five QQQ V13 wheel variants."""
    parser = argparse.ArgumentParser(description=group_a_wheel_v13_option_requests_main.__doc__)
    parser.add_argument("--data-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        target = generate_wheel_v13_requests(data_manifest_path=args.data_manifest, output_path=args.output)
    except GroupARequestError as exc:
        raise SystemExit(str(exc)) from exc
    print(target)
    return 0


def group_a_wheel_v13_backtest_main() -> int:
    """Replay all five V13 variants from one finalized option manifest."""
    parser = argparse.ArgumentParser(description=group_a_wheel_v13_backtest_main.__doc__)
    parser.add_argument("--option-manifest", required=True, type=Path)
    parser.add_argument("--request-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--base-data-manifest", type=Path)
    args = parser.parse_args()
    target = run_wheel_v13(
        option_manifest_path=args.option_manifest,
        request_path=args.request_manifest,
        output=args.output,
        base_data_manifest_path=args.base_data_manifest,
    )
    print(target)
    return 0


def group_a_proxy_backtest_main() -> int:
    """Replay a frozen Group A option-observation manifest without network I/O."""
    parser = argparse.ArgumentParser(description=group_a_proxy_backtest_main.__doc__)
    parser.add_argument("--option-manifest", required=True, type=Path)
    parser.add_argument("--request-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--base-data-manifest", type=Path)
    parser.add_argument("--structure", choices=("debit", "credit", "single_long", "long_straddle", "calendar"), default="debit")
    parser.add_argument("--execution-model", choices=("buffered", "bar_open"), default="buffered")
    parser.add_argument("--exit-minutes", choices=(45, 60, 90, 240), type=int, default=60)
    parser.add_argument("--force-exit-minutes", choices=(45, 60, 90, 240), type=int)
    args = parser.parse_args()
    try:
        target = run_group_a_proxy_backtest(
            option_manifest_path=args.option_manifest,
            request_path=args.request_manifest,
            output=args.output,
            structure=args.structure,
            execution_model=args.execution_model,
            exit_minutes=args.exit_minutes,
            force_exit_minutes=args.force_exit_minutes,
            base_data_manifest_path=args.base_data_manifest,
        )
    except GroupAReplayError as exc:
        raise SystemExit(str(exc)) from exc
    print(target)
    return 0


def option_stage_main() -> int:
    """Collect a checkpointed range of frozen historical option observations."""
    parser = argparse.ArgumentParser(description=option_stage_main.__doc__)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--base-data-manifest", required=True, type=Path)
    parser.add_argument("--option-observation-requests", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--first-request", required=True, type=int)
    parser.add_argument("--last-request", required=True, type=int)
    args = parser.parse_args()
    try:
        target = ResearchDataCollector(_client_from_environment()).collect_option_observation_stage(
            spec=CollectionSpec.from_yaml(args.spec), spec_path=args.spec, base_data_manifest_path=args.base_data_manifest,
            option_request_path=args.option_observation_requests, output=args.output,
            first_request=args.first_request, last_request=args.last_request,
        )
    except ResearchDataError as exc:
        raise SystemExit(str(exc)) from exc
    print(target)
    return 0


def option_finalize_main() -> int:
    """Finalize checkpointed historical option observations without retrieving data."""
    parser = argparse.ArgumentParser(description=option_finalize_main.__doc__)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--base-data-manifest", required=True, type=Path)
    parser.add_argument("--option-observation-requests", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        target = ResearchDataCollector(_client_from_environment()).finalize_option_observations(
            spec_path=args.spec, base_data_manifest_path=args.base_data_manifest,
            option_request_path=args.option_observation_requests, output=args.output,
        )
    except ResearchDataError as exc:
        raise SystemExit(str(exc)) from exc
    print(target)
    return 0


def feasibility_main() -> int:
    """Generate a blinded, unsigned feasibility draft from immutable collection output."""
    parser = argparse.ArgumentParser(description=feasibility_main.__doc__)
    parser.add_argument("--data-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        target = write_feasibility_draft(
            data_manifest_path=args.data_manifest,
            output_path=args.output,
        )
    except FeasibilityError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(target)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
