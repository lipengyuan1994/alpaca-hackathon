"""Determinism, state transitions, and binding guarantees.

No wall clock, no randomness, no I/O: repeated evaluations over independent
plug-in instances must be bit-identical, and next-state transitions must
preserve prior payload semantics exactly.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from opening_range_breakout_v1.plugin import Plugin
from opening_range_breakout_v1.signal import evaluate_signal
from orb_test_support import (
    AS_OF,
    HASH,
    SMH_BULLISH,
    build_config,
    build_context,
    build_universe,
    decimal_features,
)

from packages.contracts.models import (
    ArtifactRefV1,
    EntryTemplateRequestV1,
    NoTradeV1,
)
from packages.strategy_sdk import UNBOUND_PLUGIN_CONTENT_HASH


def test_independent_plugin_instances_are_bit_identical() -> None:
    context = build_context(values=build_universe(smh=SMH_BULLISH))
    config = build_config()
    first = Plugin().evaluate(context, config)
    second = Plugin().evaluate(context, config)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.evaluation_hash == second.evaluation_hash
    assert first.decision == second.decision


def test_entry_transition_marks_session_and_preserves_prior_payload() -> None:
    context = build_context(
        values=build_universe(smh=SMH_BULLISH),
        payload={"research_marker": "preserve"},
        sequence=5,
    )
    evaluation = Plugin().evaluate(context, build_config())
    decision = evaluation.decision
    assert isinstance(decision, EntryTemplateRequestV1)
    next_state = evaluation.next_state
    assert next_state.plugin_id == "opening_range_breakout"
    assert next_state.plugin_version == "1.0.0"
    assert next_state.sequence == 6
    assert next_state.as_of == AS_OF
    assert next_state.payload == {
        "research_marker": "preserve",
        "last_entry_session_SMH": "2026-08-28",
    }


def test_no_trade_transition_preserves_payload_exactly() -> None:
    prior_payload = {"marker": "keep", "last_entry_session_QQQ": "2026-08-25"}
    context = build_context(values=build_universe(), payload=prior_payload, sequence=0)
    evaluation = Plugin().evaluate(context, build_config())
    decision = evaluation.decision
    assert isinstance(decision, NoTradeV1)
    assert decision.primary_reason_code == "OPENING_RANGE_BREAKOUT_GATE_NOT_MET"
    next_state = evaluation.next_state
    assert next_state.sequence == 1
    assert next_state.payload == prior_payload


def test_entry_bindings_match_the_frozen_contract() -> None:
    context = build_context(values=build_universe(smh=SMH_BULLISH))
    pure = evaluate_signal(underlying="SMH", features=decimal_features(SMH_BULLISH))
    assert pure.score == Decimal("1.12")
    evaluation = Plugin().evaluate(context, build_config())
    decision = evaluation.decision
    assert isinstance(decision, EntryTemplateRequestV1)
    assert evaluation.plugin_id == "opening_range_breakout"
    assert evaluation.plugin_version == "1.0.0"
    assert evaluation.plugin_content_hash == UNBOUND_PLUGIN_CONTENT_HASH
    assert evaluation.context_hash == context.context_hash
    assert evaluation.config_hash == context.config_hash
    assert decision.underlying == pure.underlying
    assert decision.template_id == "CALL_DEBIT_SPREAD_V1"
    assert decision.horizon_bucket == "INTRADAY_15_60M"
    assert decision.risk_tier == "TINY"
    assert decision.signal_strength_bucket == "LOW"
    assert decision.intent_expires_at == AS_OF + timedelta(seconds=300)
    assert decision.entry_reason_codes == pure.reason_codes
    assert decision.evidence_refs == (
        ArtifactRefV1(
            artifact_type="FEATURE_VECTOR",
            content_hash=HASH,
            record_id="fixture-features",
        ),
    )
