"""Golden parity: frozen cases, fixture universes, pure/plugin agreement."""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

import pytest
from opening_range_breakout_v1.plugin import REQUIRED_FEATURE_KEYS, Plugin
from opening_range_breakout_v1.signal import evaluate_signal
from orb_test_support import (
    AS_OF,
    FIXTURES_DIR,
    SMH_BULLISH,
    build_config,
    build_context,
    build_universe,
    decimal_features,
    golden_cases,
    load_universe_file,
)

from packages.contracts.models import EntryTemplateRequestV1


@pytest.mark.parametrize("case", golden_cases(), ids=lambda case: str(case["name"]))
def test_golden_signal_cases_match_the_frozen_expectations(
    case: dict[str, object],
) -> None:
    features = case["features"]
    assert isinstance(features, dict)
    result = evaluate_signal(
        underlying=str(case["underlying"]),
        features=decimal_features(features),
    )
    assert result.action == str(case["expected_action"])
    expected_score = case["expected_score"]
    if expected_score is None:
        assert result.score is None
    else:
        assert result.score == Decimal(str(expected_score))
    expected_codes = case["expected_reason_codes"]
    assert isinstance(expected_codes, list)
    assert list(result.reason_codes) == expected_codes


def test_plugin_agrees_with_the_pure_signal_on_the_bullish_block() -> None:
    pure = evaluate_signal(underlying="SMH", features=decimal_features(SMH_BULLISH))
    evaluation = Plugin().evaluate(
        build_context(values=build_universe(smh=SMH_BULLISH)),
        build_config(),
    )
    decision = evaluation.decision
    assert isinstance(decision, EntryTemplateRequestV1)
    assert decision.underlying == pure.underlying == "SMH"
    assert decision.entry_reason_codes == pure.reason_codes
    assert decision.template_id == "CALL_DEBIT_SPREAD_V1"
    assert decision.signal_strength_bucket == "LOW"
    assert decision.intent_expires_at == AS_OF + timedelta(seconds=300)


@pytest.mark.parametrize(
    "name",
    ["feature_vector_smh_bullish.json", "feature_vector_soxl_bearish.json"],
    ids=["smh_file", "soxl_file"],
)
def test_fixture_universe_files_follow_the_contract_key_order(name: str) -> None:
    document = json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
    assert list(document) == list(REQUIRED_FEATURE_KEYS)
    assert len(document) == 48
    values = load_universe_file(name)
    assert len(values) == 48
    assert all(isinstance(value, Decimal) for value in values.values())


def test_smh_bullish_fixture_universe_enters_a_call_spread_request() -> None:
    values = load_universe_file("feature_vector_smh_bullish.json")
    evaluation = Plugin().evaluate(build_context(values=values), build_config())
    decision = evaluation.decision
    assert isinstance(decision, EntryTemplateRequestV1)
    assert decision.underlying == "SMH"
    assert decision.template_id == "CALL_DEBIT_SPREAD_V1"
    assert decision.signal_strength_bucket == "LOW"
    assert decision.entry_reason_codes == ("OPENING_RANGE_BREAKOUT_BULLISH",)


def test_soxl_bearish_fixture_universe_enters_a_put_spread_request() -> None:
    values = load_universe_file("feature_vector_soxl_bearish.json")
    evaluation = Plugin().evaluate(build_context(values=values), build_config())
    decision = evaluation.decision
    assert isinstance(decision, EntryTemplateRequestV1)
    assert decision.underlying == "SOXL"
    assert decision.template_id == "PUT_DEBIT_SPREAD_V1"
    assert decision.signal_strength_bucket == "MEDIUM"
    assert decision.entry_reason_codes == ("OPENING_RANGE_BREAKOUT_BEARISH",)
