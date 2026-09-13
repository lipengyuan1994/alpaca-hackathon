"""Fail-closed refusals: every degraded input yields a declared reason code.

Each case pins one degraded dimension (binding, freshness, quality, window,
completeness, state, arbitration, template authority, config validity) to its
declared code in the frozen reason namespace.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest
from opening_range_breakout_v1.plugin import Plugin
from orb_test_support import (
    AS_OF,
    CLEAN_QUALITY_FLAGS,
    HASH,
    SMH_BULLISH,
    SOXL_BEARISH,
    build_config,
    build_context,
    build_universe,
)

from packages.contracts.models import (
    IntentTupleV1,
    NoTradeV1,
    StrategyConfigV1,
    StrategyContextV1,
)

_PUT_ONLY = (
    IntentTupleV1(
        template_id="PUT_DEBIT_SPREAD_V1",
        horizon_bucket="INTRADAY_15_60M",
        risk_tier="TINY",
        max_intent_ttl_seconds=300,
    ),
)
_CALL_STANDARD = (
    IntentTupleV1(
        template_id="CALL_DEBIT_SPREAD_V1",
        horizon_bucket="INTRADAY_15_60M",
        risk_tier="STANDARD",
        max_intent_ttl_seconds=300,
    ),
)
_CALL_SHORT_TTL = (
    IntentTupleV1(
        template_id="CALL_DEBIT_SPREAD_V1",
        horizon_bucket="INTRADAY_15_60M",
        risk_tier="TINY",
        max_intent_ttl_seconds=60,
    ),
)

_NEUTRAL_UNIVERSE = build_universe()
_MISSING_KEY_UNIVERSE = {
    key: value
    for key, value in _NEUTRAL_UNIVERSE.items()
    if key != "SMH__up_break_fraction_or30_v1"
}

_CASES: list[tuple[str, Callable[[], StrategyContextV1], StrategyConfigV1, str]] = [
    (
        "wrong_feature_contract_hash",
        lambda: build_context(values=_NEUTRAL_UNIVERSE, feature_hash=HASH),
        build_config(),
        "FEATURE_SCHEMA_MISMATCH",
    ),
    (
        "stale_observation_age",
        lambda: build_context(
            values=_NEUTRAL_UNIVERSE,
            feature_available_time=AS_OF - timedelta(seconds=61),
        ),
        build_config(),
        "DATA_STALE",
    ),
    (
        "early_close_session_flag",
        lambda: build_context(
            values=_NEUTRAL_UNIVERSE,
            quality_flags=("EARLY_CLOSE_SESSION",),
        ),
        build_config(),
        "EARLY_CLOSE_SESSION",
    ),
    (
        "undeclared_extra_quality_flag",
        lambda: build_context(
            values=_NEUTRAL_UNIVERSE,
            quality_flags=(*CLEAN_QUALITY_FLAGS, "LULD_HALTED"),
        ),
        build_config(),
        "DATA_QUALITY_REJECTED",
    ),
    (
        "missing_required_feature_key",
        lambda: build_context(values=_MISSING_KEY_UNIVERSE),
        build_config(),
        "DATA_MISSING",
    ),
    (
        "daily_entry_already_used",
        lambda: build_context(
            values=build_universe(smh=SMH_BULLISH),
            payload={"last_entry_session_SMH": "2026-08-28"},
        ),
        build_config(),
        "DAILY_ENTRY_ALREADY_USED",
    ),
    (
        "two_qualifying_candidates",
        lambda: build_context(values=build_universe(smh=SMH_BULLISH, soxl=SOXL_BEARISH)),
        build_config(),
        "DIRECTION_AMBIGUOUS",
    ),
    (
        "call_template_absent",
        lambda: build_context(values=build_universe(smh=SMH_BULLISH), tuples=_PUT_ONLY),
        build_config(),
        "TEMPLATE_NOT_ALLOWED",
    ),
    (
        "standard_risk_tier_refused",
        lambda: build_context(values=build_universe(smh=SMH_BULLISH), tuples=_CALL_STANDARD),
        build_config(),
        "TUPLE_NOT_ALLOWED",
    ),
    (
        "intent_ttl_mismatch",
        lambda: build_context(values=build_universe(smh=SMH_BULLISH), tuples=_CALL_SHORT_TTL),
        build_config(intent_ttl_seconds=300),
        "TUPLE_NOT_ALLOWED",
    ),
    (
        "non_numeric_break_threshold",
        lambda: build_context(values=_NEUTRAL_UNIVERSE),
        build_config(break_fraction_threshold="abc"),
        "FEATURE_SCHEMA_MISMATCH",
    ),
    (
        "nonpositive_range_floor",
        lambda: build_context(values=_NEUTRAL_UNIVERSE),
        build_config(range_floor=-1),
        "FEATURE_SCHEMA_MISMATCH",
    ),
    (
        "zero_volume_threshold",
        lambda: build_context(values=_NEUTRAL_UNIVERSE),
        build_config(volume_ratio_threshold="0"),
        "FEATURE_SCHEMA_MISMATCH",
    ),
]


@pytest.mark.parametrize(
    ("label", "make_context", "config", "reason"),
    _CASES,
    ids=[case[0] for case in _CASES],
)
def test_plugin_fails_closed_with_the_declared_reason(
    label: str,
    make_context: Callable[[], StrategyContextV1],
    config: StrategyConfigV1,
    reason: str,
) -> None:
    evaluation = Plugin().evaluate(make_context(), config)
    decision = evaluation.decision
    assert isinstance(decision, NoTradeV1)
    assert decision.kind == "NO_TRADE"
    assert decision.primary_reason_code == reason
