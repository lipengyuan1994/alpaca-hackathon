"""Fail-closed refusals: every degraded input yields a declared reason code.

Each case pins one degraded dimension (binding, freshness, completeness,
session continuity, corporate-action clarity, quality, window, state,
arbitration, template authority, config validity) to its declared code in the
frozen reason namespace.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from gap_continuation_v1.plugin import Plugin
from gap_test_support import (
    AS_OF,
    CLEAN_QUALITY_FLAGS,
    HASH,
    NEUTRAL,
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
    if key != "SMH__sigma_gap_60_v1"
}
_MISSING_FLAG_QUALITY = tuple(
    flag for flag in CLEAN_QUALITY_FLAGS if flag != "IEX_COMPLETE"
)

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
        "max_observation_age_exceeded",
        lambda: build_context(
            values=_NEUTRAL_UNIVERSE,
            feature_available_time=AS_OF - timedelta(seconds=3600),
        ),
        build_config(),
        "DATA_STALE",
    ),
    (
        "missing_required_feature_key",
        lambda: build_context(values=_MISSING_KEY_UNIVERSE),
        build_config(),
        "DATA_MISSING",
    ),
    (
        "early_close_session_feature",
        lambda: build_context(
            values=build_universe(smh={**NEUTRAL, "early_close_session_v1": "1"}),
        ),
        build_config(),
        "EARLY_CLOSE_SESSION",
    ),
    (
        "corporate_action_continuity_unclear",
        lambda: build_context(
            values=build_universe(
                smh={**NEUTRAL, "corporate_action_continuity_clear_v1": "0"},
            ),
        ),
        build_config(),
        "CORPORATE_ACTION_AMBIGUOUS",
    ),
    (
        "adjustment_basis_unclear",
        lambda: build_context(
            values=build_universe(smh={**NEUTRAL, "adjustment_basis_v1": "2"}),
        ),
        build_config(),
        "CORPORATE_ACTION_AMBIGUOUS",
    ),
    (
        "missing_quality_flag",
        lambda: build_context(
            values=_NEUTRAL_UNIVERSE,
            quality_flags=_MISSING_FLAG_QUALITY,
        ),
        build_config(),
        "DATA_QUALITY_REJECTED",
    ),
    (
        "off_grid_time",
        lambda: build_context(
            values=_NEUTRAL_UNIVERSE,
            as_of=datetime(2026, 8, 28, 15, 0, 1, tzinfo=UTC),
        ),
        build_config(),
        "OUTSIDE_DECISION_WINDOW",
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
        "neutral_universe",
        lambda: build_context(values=_NEUTRAL_UNIVERSE),
        build_config(),
        "GAP_CONTINUATION_GATE_NOT_MET",
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
        "intent_ttl_mismatch",
        lambda: build_context(values=build_universe(smh=SMH_BULLISH), tuples=_CALL_SHORT_TTL),
        build_config(intent_ttl_seconds=300),
        "TUPLE_NOT_ALLOWED",
    ),
    (
        "non_numeric_gap_z_threshold",
        lambda: build_context(values=_NEUTRAL_UNIVERSE),
        build_config(gap_z_threshold="abc"),
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
