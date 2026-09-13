"""Shared deterministic fixtures for opening_range_breakout_v1 package tests.

Everything here is offline and credential-free.  The context builder mirrors
the reviewed Group A fixture shape; feature blocks are frozen delivered
snapshots (see ``tests/fixtures/README.md`` for the snapshot semantics).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from opening_range_breakout_v1.plugin import (
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
    "close_completed_15m_v1": "100",
    "down_break_fraction_or30_v1": "-1",
    "opening_range_high_0930_1000_adjusted_v1": "100",
    "opening_range_low_0930_1000_adjusted_v1": "99",
    "opening_range_width_log_v1": "0.01005034",
    "session_iex_vwap_v1": "100",
    "up_break_fraction_or30_v1": "0",
    "volume_ratio_same_time_20_v1": "1",
}

SMH_BULLISH: dict[str, str] = {
    "close_completed_15m_v1": "203.18592",
    "down_break_fraction_or30_v1": "-1.15",
    "opening_range_high_0930_1000_adjusted_v1": "200",
    "opening_range_low_0930_1000_adjusted_v1": "180",
    "opening_range_width_log_v1": "0.10536052",
    "session_iex_vwap_v1": "195",
    "up_break_fraction_or30_v1": "0.15",
    "volume_ratio_same_time_20_v1": "1.40",
}

SOXL_BEARISH: dict[str, str] = {
    "close_completed_15m_v1": "37.75710",
    "down_break_fraction_or30_v1": "0.125",
    "opening_range_high_0930_1000_adjusted_v1": "40",
    "opening_range_low_0930_1000_adjusted_v1": "38",
    "opening_range_width_log_v1": "0.05129329",
    "session_iex_vwap_v1": "38.20",
    "up_break_fraction_or30_v1": "-1.125",
    "volume_ratio_same_time_20_v1": "1.5625",
}

SPY_HIGH: dict[str, str] = {
    "close_completed_15m_v1": "404.12457",
    "down_break_fraction_or30_v1": "-1.20",
    "opening_range_high_0930_1000_adjusted_v1": "400",
    "opening_range_low_0930_1000_adjusted_v1": "380",
    "opening_range_width_log_v1": "0.05129329",
    "session_iex_vwap_v1": "400.50",
    "up_break_fraction_or30_v1": "0.20",
    "volume_ratio_same_time_20_v1": "2.1875",
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
    config = build_config()
    return StrategyContextV1(
        evaluation_id="opening_range_breakout-evaluation",
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
            plugin_id="opening_range_breakout",
            plugin_version="1.0.0",
            as_of=as_of - timedelta(seconds=1),
            sequence=sequence,
            payload=payload or {},
        ),
        config_hash=config.config_hash,
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
    blocks = {
        symbol: block if block is not None else NEUTRAL
        for symbol, block in given.items()
    }
    return {
        f"{symbol}__{feature}": Decimal(text)
        for symbol in ALLOWED_UNDERLYINGS
        for feature, text in blocks[symbol].items()
    }


def golden_cases() -> list[dict[str, object]]:
    document = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    cases = document["cases"]
    assert isinstance(cases, list) and cases, "golden case file must be a non-empty list"
    return cases


def decimal_features(block: dict[str, str]) -> dict[str, Decimal]:
    return {key: Decimal(value) for key, value in block.items()}


def load_universe_file(name: str) -> dict[str, Decimal]:
    document = json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
    return {key: Decimal(value) for key, value in document.items()}
