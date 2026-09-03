"""Shared deterministic fixtures for gap_continuation_v1 package tests.

The presets are internally consistent Decimal snapshots of a delivered feature
vector: the log gap is ln(session open / prior regular close), the first-hour
return is ln(completed-interval close / session open), the continuation ratio
is first_hour/gap_log, and the delivered z-score is gap_log/sigma_gap_60.
Tests consume only the delivered values, never recomputations.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from gap_continuation_v1.plugin import (
    ALLOWED_UNDERLYINGS,
    EXPECTED_QUALITY_FLAGS,
    FEATURE_CONTRACT_HASH,
)

from packages.contracts.models import (
    FeedIdentityV1,
    IntentTupleV1,
    StrategyConfigV1,
    StrategyContextV1,
    StrategyStateV1,
)

AS_OF = datetime(2026, 8, 28, 14, 30, 1, tzinfo=UTC)  # Friday 10:30:01 ET
HASH = "sha256:" + "a" * 64
CLEAN_QUALITY_FLAGS = tuple(sorted(EXPECTED_QUALITY_FLAGS))
TESTS_DIR = Path(__file__).resolve().parent
GOLDEN_PATH = TESTS_DIR / "golden" / "signal_cases.json"
FIXTURES_DIR = TESTS_DIR / "fixtures"

DEFAULT_TUPLES = (
    IntentTupleV1(
        template_id="CALL_DEBIT_SPREAD_V1",
        horizon_bucket="INTRADAY_15_60M",
        risk_tier="TINY",
        max_intent_ttl_seconds=300,
    ),
    IntentTupleV1(
        template_id="PUT_DEBIT_SPREAD_V1",
        horizon_bucket="INTRADAY_15_60M",
        risk_tier="TINY",
        max_intent_ttl_seconds=300,
    ),
)

NEUTRAL: dict[str, str] = {
    "adjustment_basis_v1": "1",
    "close_completed_15m_v1": "100",
    "continuation_ratio_v1": "0",
    "corporate_action_continuity_clear_v1": "1",
    "early_close_session_v1": "0",
    "first_hour_return_v1": "0",
    "gap_log_adjusted_v1": "0",
    "gap_z_60_v1": "0",
    "open_0930_adjusted_v1": "100",
    "open_0930_raw_v1": "100",
    "prior_regular_close_adjusted_v1": "100",
    "prior_regular_close_raw_v1": "100",
    "session_iex_vwap_v1": "100",
    "sigma_gap_60_v1": "0.02",
}

SMH_BULLISH: dict[str, str] = {
    "adjustment_basis_v1": "1",
    "close_completed_15m_v1": "192.89830",
    "continuation_ratio_v1": "0.28",
    "corporate_action_continuity_clear_v1": "1",
    "early_close_session_v1": "0",
    "first_hour_return_v1": "0.01513882",
    "gap_log_adjusted_v1": "0.05406722",
    "gap_z_60_v1": "1.12",
    "open_0930_adjusted_v1": "190",
    "open_0930_raw_v1": "190",
    "prior_regular_close_adjusted_v1": "180",
    "prior_regular_close_raw_v1": "180",
    "session_iex_vwap_v1": "191",
    "sigma_gap_60_v1": "0.04827430",
}

SOXL_BEARISH: dict[str, str] = {
    "adjustment_basis_v1": "1",
    "close_completed_15m_v1": "37.39572",
    "continuation_ratio_v1": "0.3125",
    "corporate_action_continuity_clear_v1": "1",
    "early_close_session_v1": "0",
    "first_hour_return_v1": "-0.01602915",
    "gap_log_adjusted_v1": "-0.05129329",
    "gap_z_60_v1": "-1.25",
    "open_0930_adjusted_v1": "38",
    "open_0930_raw_v1": "38",
    "prior_regular_close_adjusted_v1": "40",
    "prior_regular_close_raw_v1": "40",
    "session_iex_vwap_v1": "37.75",
    "sigma_gap_60_v1": "0.04103463",
}

SPY_HIGH: dict[str, str] = {
    "adjustment_basis_v1": "1",
    "close_completed_15m_v1": "409.07780",
    "continuation_ratio_v1": "0.4375",
    "corporate_action_continuity_clear_v1": "1",
    "early_close_session_v1": "0",
    "first_hour_return_v1": "0.02244081",
    "gap_log_adjusted_v1": "0.05129329",
    "gap_z_60_v1": "1.75",
    "open_0930_adjusted_v1": "400",
    "open_0930_raw_v1": "400",
    "prior_regular_close_adjusted_v1": "380",
    "prior_regular_close_raw_v1": "380",
    "session_iex_vwap_v1": "405",
    "sigma_gap_60_v1": "0.02931045",
}


def build_config(**values: str | int | Decimal | bool) -> StrategyConfigV1:
    return StrategyConfigV1(values=values)


def build_context(
    *,
    values: dict[str, Decimal],
    as_of: datetime = AS_OF,
    feature_hash: str = FEATURE_CONTRACT_HASH,
    quality_flags: tuple[str, ...] = CLEAN_QUALITY_FLAGS,
    feature_available_time: datetime | None = None,
    payload: dict[str, str] | None = None,
    sequence: int = 0,
    tuples: tuple[IntentTupleV1, ...] = DEFAULT_TUPLES,
) -> StrategyContextV1:
    return StrategyContextV1(
        evaluation_id="gap_continuation-evaluation",
        as_of=as_of,
        market_snapshot_id="fixture-market",
        market_snapshot_hash=HASH,
        feature_vector_id="fixture-features",
        feature_vector_hash=HASH,
        feature_contract_hash=feature_hash,
        feature_available_time=feature_available_time or as_of - timedelta(seconds=1),
        feed_identity=FeedIdentityV1(entitlement="fixture-only"),
        quality_flags=quality_flags,
        universe_features=values,
        allowed_intent_tuples=tuples,
        prior_state=StrategyStateV1(
            plugin_id="gap_continuation",
            plugin_version="1.0.0",
            as_of=as_of - timedelta(seconds=1),
            sequence=sequence,
            payload=payload or {},
        ),
        config_hash=build_config().config_hash,
    )


def build_universe(
    *,
    spy: dict[str, str] | None = None,
    qqq: dict[str, str] | None = None,
    tqqq: dict[str, str] | None = None,
    smh: dict[str, str] | None = None,
    soxl: dict[str, str] | None = None,
    igv: dict[str, str] | None = None,
) -> dict[str, Decimal]:
    given = {"SPY": spy, "QQQ": qqq, "TQQQ": tqqq, "SMH": smh, "SOXL": soxl, "IGV": igv}
    universe: dict[str, Decimal] = {}
    for symbol in ALLOWED_UNDERLYINGS:
        block = given[symbol] if given[symbol] is not None else NEUTRAL
        for feature, value in block.items():
            universe[f"{symbol}__{feature}"] = Decimal(value)
    return universe


def golden_cases() -> list[dict[str, object]]:
    doc = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    cases = doc["cases"]
    assert isinstance(cases, list) and cases
    return cases


def decimal_features(block: dict[str, str]) -> dict[str, Decimal]:
    return {key: Decimal(value) for key, value in block.items()}


def load_universe_file(name: str) -> dict[str, Decimal]:
    doc = json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
    return {key: Decimal(value) for key, value in doc.items()}
