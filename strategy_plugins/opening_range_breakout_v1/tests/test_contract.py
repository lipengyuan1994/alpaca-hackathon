"""Contract-surface tests for the opening_range_breakout_v1 package.

Freeze discipline: the manifest (17 declarations), the defaults (13 pinned
parameters), the 48-key feature universe, the closed reason-code namespace,
and the public exports are pinned to the frozen declaration under
``research/candidates/opening_range_breakout__all_feasible__o2_v1/``.
"""

from __future__ import annotations

import re
from pathlib import Path

import opening_range_breakout_v1 as package
from opening_range_breakout_v1.plugin import (
    ALLOWED_UNDERLYINGS,
    FEATURE_CONTRACT_HASH,
    INTENT_TTL_SECONDS,
    LOCAL_FEATURE_KEYS,
    REQUIRED_FEATURE_KEYS,
    Plugin,
)
from opening_range_breakout_v1.reason_codes import (
    ALL_CODES,
    COMMON_NO_TRADE_CODES,
    ENTRY_CODES,
)
from orb_test_support import TESTS_DIR, build_config

from packages.contracts.models import DataRequirementsV1, StrategyMetadataV1

PACKAGE_ROOT = TESTS_DIR.parent
MANIFEST_PATH = PACKAGE_ROOT / "manifest.yaml"
DEFAULTS_PATH = PACKAGE_ROOT / "defaults.yaml"

_FROZEN_SYMBOLS = ("SPY", "QQQ", "TQQQ", "SMH", "SOXL", "IGV")
_FROZEN_FEATURES = (
    "close_completed_15m_v1",
    "down_break_fraction_or30_v1",
    "opening_range_high_0930_1000_adjusted_v1",
    "opening_range_low_0930_1000_adjusted_v1",
    "opening_range_width_log_v1",
    "session_iex_vwap_v1",
    "up_break_fraction_or30_v1",
    "volume_ratio_same_time_20_v1",
)

EXPECTED_MANIFEST: dict[str, str] = {
    "api_version": "strategy-plugin/v1",
    "plugin_id": "opening_range_breakout",
    "plugin_version": "1.0.0",
    "entrypoint": "opening_range_breakout_v1.plugin:Plugin",
    "owner": "assigned_group_b_research_owner",
    "reviewer": "independent_group_c_research_reviewer",
    "economic_hypothesis_id": "OPENING_RANGE_BREAKOUT",
    "allowed_underlyings": "[SPY, QQQ, TQQQ, SMH, SOXL, IGV]",
    "allowed_templates": "[CALL_DEBIT_SPREAD_V1, PUT_DEBIT_SPREAD_V1]",
    "required_feature_schema": "feature-vector/v1",
    "feature_contract_hash": "sha256:0ef3d29fd8680508b0c02cacda2aef31f495f0960373848dc46837ff4a259654",
    "pair_cell_symbols": "[SMH, SOXL]",
    "position_policy": "TREND_VWAP_OR_60M_V1",
    "decision_schema_version": "strategy-evaluation/v1",
    "network_access": "false",
    "deterministic": "true",
    "lifecycle": "research_only",
}

EXPECTED_DEFAULTS: dict[str, str] = {
    "opening_range_start_et": "09:30:00",
    "opening_range_end_et": "10:00:00",
    "break_fraction_threshold": "0.10",
    "volume_ratio_threshold": "1.25",
    "same_time_volume_lookback_sessions": "20",
    "range_floor": "0.000001",
    "decision_start_et": "10:30:01",
    "decision_end_et": "14:30:01",
    "decision_step_minutes": "30",
    "max_entries_per_symbol_session": "1",
    "time_exit_minutes": "60",
    "risk_tier": "TINY",
    "intent_ttl_seconds": "300",
}


def _flat_pairs(path: Path) -> dict[str, str]:
    """Flat YAML scan: exact key/value pairs, no schema interpretation."""
    pairs: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition(":")
        if not separator:
            continue
        text = value.strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
            text = text[1:-1]
        pairs[key.strip()] = text
    return pairs


def test_manifest_matches_the_frozen_declaration() -> None:
    assert _flat_pairs(MANIFEST_PATH) == EXPECTED_MANIFEST


def test_defaults_pin_the_frozen_parameters() -> None:
    assert _flat_pairs(DEFAULTS_PATH) == EXPECTED_DEFAULTS


def test_feature_contract_hash_is_the_frozen_binding() -> None:
    assert FEATURE_CONTRACT_HASH == (
        "sha256:0ef3d29fd8680508b0c02cacda2aef31f495f0960373848dc46837ff4a259654"
    )
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", FEATURE_CONTRACT_HASH)
    assert _flat_pairs(MANIFEST_PATH)["feature_contract_hash"] == FEATURE_CONTRACT_HASH


def test_required_feature_keys_are_the_frozen_universe() -> None:
    expected = tuple(
        f"{symbol}__{feature}" for symbol in _FROZEN_SYMBOLS for feature in _FROZEN_FEATURES
    )
    assert ALLOWED_UNDERLYINGS == _FROZEN_SYMBOLS
    assert LOCAL_FEATURE_KEYS == _FROZEN_FEATURES
    assert REQUIRED_FEATURE_KEYS == expected
    assert len(REQUIRED_FEATURE_KEYS) == 48
    assert len(set(REQUIRED_FEATURE_KEYS)) == 48


def test_data_requirements_bind_the_frozen_contract() -> None:
    requirements = Plugin().data_requirements(build_config())
    assert isinstance(requirements, DataRequirementsV1)
    assert requirements.underlyings == _FROZEN_SYMBOLS
    assert requirements.feature_schema_version == "feature-vector/v1"
    assert requirements.feature_contract_hash == FEATURE_CONTRACT_HASH
    assert requirements.required_feature_keys == REQUIRED_FEATURE_KEYS
    assert requirements.maximum_observation_age_seconds == 60
    assert requirements.needs_logical_positions is False


def test_metadata_declares_the_frozen_identity() -> None:
    metadata = Plugin().metadata
    assert isinstance(metadata, StrategyMetadataV1)
    assert metadata.plugin_id == "opening_range_breakout"
    assert metadata.plugin_version == "1.0.0"
    assert metadata.owner == "assigned_group_b_research_owner"
    assert metadata.economic_hypothesis_id == "OPENING_RANGE_BREAKOUT"
    assert metadata.deterministic is True
    assert metadata.api_version == "strategy-plugin/v1"
    assert metadata.decision_schema_version == "strategy-evaluation/v1"


def test_intent_ttl_matches_the_frozen_default() -> None:
    assert INTENT_TTL_SECONDS == 300
    assert EXPECTED_DEFAULTS["intent_ttl_seconds"] == str(INTENT_TTL_SECONDS)


def test_reason_code_namespace_is_closed() -> None:
    expected_common = {
        "DATA_MISSING",
        "DATA_STALE",
        "DATA_QUALITY_REJECTED",
        "FEATURE_SCHEMA_MISMATCH",
        "OUTSIDE_DECISION_WINDOW",
        "EARLY_CLOSE_SESSION",
        "DAILY_ENTRY_ALREADY_USED",
        "NO_SIGNAL",
        "DIRECTION_AMBIGUOUS",
        "UNDERLYING_NOT_ALLOWED",
        "TEMPLATE_NOT_ALLOWED",
        "TUPLE_NOT_ALLOWED",
    }
    expected_entry = {
        "OPENING_RANGE_BREAKOUT_BULLISH",
        "OPENING_RANGE_BREAKOUT_BEARISH",
    }
    assert COMMON_NO_TRADE_CODES == expected_common
    assert ENTRY_CODES == expected_entry
    assert ALL_CODES == (
        COMMON_NO_TRADE_CODES | ENTRY_CODES | {"OPENING_RANGE_BREAKOUT_GATE_NOT_MET"}
    )
    assert len(ALL_CODES) == 15
    assert all(re.fullmatch(r"[A-Z][A-Z0-9_]{2,95}", code) for code in ALL_CODES)


def test_public_exports_match_the_declared_surface() -> None:
    assert package.__all__ == [
        "ALLOWED_UNDERLYINGS",
        "FEATURE_CONTRACT_HASH",
        "Plugin",
        "SignalResult",
        "evaluate_signal",
    ]
    for name in package.__all__:
        assert getattr(package, name) is not None
