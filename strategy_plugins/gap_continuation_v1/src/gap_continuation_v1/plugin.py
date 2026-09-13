"""Framework adapter for the frozen Group B standardized-gap-continuation signal.

Entry-only and deterministic: the plug-in emits semantic debit-spread entry
requests or ``NO_TRADE`` and never touches contracts, strikes, prices,
accounts, or orders.  The decision flow mirrors the reviewed Group A adapter
pattern; the sole authority for source-hash binding is the central registry.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from packages.contracts.models import (
    ArtifactRefV1,
    DataRequirementsV1,
    EntryTemplateRequestV1,
    NoTradeV1,
    StrategyConfigV1,
    StrategyContextV1,
    StrategyEvaluationV1,
    StrategyMetadataV1,
)
from packages.strategy_sdk import UNBOUND_PLUGIN_CONTENT_HASH

from .signal import SignalResult, evaluate_signal

FEATURE_CONTRACT_HASH = "sha256:5d3521f1d77ec642ba80c5a8b03eb6d7a0b521b2ea246f1e44828fe5116c8160"
ALLOWED_UNDERLYINGS = ("SPY", "QQQ", "TQQQ", "SMH", "SOXL", "IGV")
LOCAL_FEATURE_KEYS = (
    "adjustment_basis_v1",
    "close_completed_15m_v1",
    "continuation_ratio_v1",
    "corporate_action_continuity_clear_v1",
    "early_close_session_v1",
    "first_hour_return_v1",
    "gap_log_adjusted_v1",
    "gap_z_60_v1",
    "open_0930_adjusted_v1",
    "open_0930_raw_v1",
    "prior_regular_close_adjusted_v1",
    "prior_regular_close_raw_v1",
    "session_iex_vwap_v1",
    "sigma_gap_60_v1",
)
REQUIRED_FEATURE_KEYS = tuple(
    f"{symbol}__{feature}" for symbol in ALLOWED_UNDERLYINGS for feature in LOCAL_FEATURE_KEYS
)
EXPECTED_QUALITY_FLAGS = frozenset(
    {
        "CORPORATE_ACTION_CONTINUITY_CLEAR",
        "FULL_XNYS_SESSION",
        "IEX_COMPLETE",
        "NO_DUPLICATE_BARS",
        "OHLC_VALID",
        "PRIOR_SESSION_FULL",
    }
)
INTENT_TTL_SECONDS = 300
_EASTERN = ZoneInfo("America/New_York")


def _decimal_config(config: StrategyConfigV1, key: str, default: str) -> Decimal:
    value = config.values.get(key, default)
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("CONFIG_DECIMAL_INVALID") from exc
    if not result.is_finite():
        raise ValueError("CONFIG_DECIMAL_INVALID")
    return result


def _int_config(config: StrategyConfigV1, key: str, default: int) -> int:
    value = config.values.get(key, default)
    try:
        return int(str(value))
    except ValueError as exc:
        raise ValueError("CONFIG_INT_INVALID") from exc


def _is_decision_time(context: StrategyContextV1) -> bool:
    value = context.as_of.astimezone(_EASTERN)
    if value.weekday() >= 5 or value.second != 1 or value.microsecond != 0:
        return False
    return value.hour == 10 and value.minute == 30


def _next_state(context: StrategyContextV1, payload: dict[str, str] | None = None):
    return context.prior_state.model_copy(
        update={
            "plugin_id": "gap_continuation",
            "plugin_version": "1.0.0",
            "as_of": context.as_of,
            "sequence": context.prior_state.sequence + 1,
            "payload": context.prior_state.payload if payload is None else payload,
            "state_hash": None,
        }
    )


class Plugin:
    @property
    def metadata(self) -> StrategyMetadataV1:
        return StrategyMetadataV1(
            plugin_id="gap_continuation",
            plugin_version="1.0.0",
            owner="assigned_group_b_research_owner",
            economic_hypothesis_id="STANDARDIZED_GAP_CONTINUATION",
        )

    def data_requirements(self, config: StrategyConfigV1) -> DataRequirementsV1:
        return DataRequirementsV1(
            underlyings=ALLOWED_UNDERLYINGS,
            feature_contract_hash=FEATURE_CONTRACT_HASH,
            required_feature_keys=REQUIRED_FEATURE_KEYS,
            maximum_observation_age_seconds=60,
            needs_logical_positions=False,
        )

    def evaluate(self, context: StrategyContextV1, config: StrategyConfigV1) -> StrategyEvaluationV1:
        decision = self._decision(context, config)
        payload: dict[str, str] | None = None
        if isinstance(decision, EntryTemplateRequestV1):
            payload = dict(context.prior_state.payload)
            payload[f"last_entry_session_{decision.underlying}"] = (
                context.as_of.astimezone(_EASTERN).date().isoformat()
            )
        return StrategyEvaluationV1(
            evaluation_id=context.evaluation_id,
            plugin_id=self.metadata.plugin_id,
            plugin_version=self.metadata.plugin_version,
            plugin_content_hash=UNBOUND_PLUGIN_CONTENT_HASH,
            context_hash=context.context_hash,
            config_hash=context.config_hash,
            decision=decision,
            next_state=_next_state(context, payload),
        )

    def _decision(self, context: StrategyContextV1, config: StrategyConfigV1):
        if context.feature_contract_hash != FEATURE_CONTRACT_HASH:
            return NoTradeV1(primary_reason_code="FEATURE_SCHEMA_MISMATCH")
        age_seconds = (context.as_of - context.feature_available_time).total_seconds()
        if age_seconds < 0 or age_seconds > 60:
            return NoTradeV1(primary_reason_code="DATA_STALE")
        if any(key not in context.universe_features for key in REQUIRED_FEATURE_KEYS):
            return NoTradeV1(primary_reason_code="DATA_MISSING")
        for symbol in ALLOWED_UNDERLYINGS:
            if context.universe_features[f"{symbol}__early_close_session_v1"] != 0:
                return NoTradeV1(primary_reason_code="EARLY_CLOSE_SESSION")
        for symbol in ALLOWED_UNDERLYINGS:
            if (
                context.universe_features[f"{symbol}__corporate_action_continuity_clear_v1"] != 1
                or context.universe_features[f"{symbol}__adjustment_basis_v1"] != 1
            ):
                return NoTradeV1(primary_reason_code="CORPORATE_ACTION_AMBIGUOUS")
        flags = set(context.quality_flags)
        if flags != EXPECTED_QUALITY_FLAGS:
            return NoTradeV1(primary_reason_code="DATA_QUALITY_REJECTED")
        if not _is_decision_time(context):
            return NoTradeV1(primary_reason_code="OUTSIDE_DECISION_WINDOW")
        try:
            gap_z_threshold = _decimal_config(config, "gap_z_threshold", "1.00")
            continuation_ratio_threshold = _decimal_config(
                config, "continuation_ratio_threshold", "0.25"
            )
            gap_floor = _decimal_config(config, "gap_floor", "0.000001")
            intent_ttl = _int_config(config, "intent_ttl_seconds", INTENT_TTL_SECONDS)
        except ValueError:
            return NoTradeV1(primary_reason_code="FEATURE_SCHEMA_MISMATCH")
        if (
            gap_z_threshold <= 0
            or continuation_ratio_threshold <= 0
            or gap_floor <= 0
            or intent_ttl <= 0
        ):
            return NoTradeV1(primary_reason_code="FEATURE_SCHEMA_MISMATCH")
        today = context.as_of.astimezone(_EASTERN).date().isoformat()
        candidates: list[SignalResult] = []
        had_used_candidate = False
        for symbol in ALLOWED_UNDERLYINGS:
            local = {
                feature: context.universe_features[f"{symbol}__{feature}"]
                for feature in LOCAL_FEATURE_KEYS
            }
            result = evaluate_signal(
                underlying=symbol,
                features=local,
                gap_z_threshold=gap_z_threshold,
                continuation_ratio_threshold=continuation_ratio_threshold,
                gap_floor=gap_floor,
            )
            if result.action != "NO_TRADE":
                if context.prior_state.payload.get(f"last_entry_session_{symbol}") == today:
                    had_used_candidate = True
                    continue
                candidates.append(result)
        if not candidates:
            if had_used_candidate:
                return NoTradeV1(primary_reason_code="DAILY_ENTRY_ALREADY_USED")
            return NoTradeV1(primary_reason_code="GAP_CONTINUATION_GATE_NOT_MET")
        # Pair-cell packages must not choose a cross-symbol winner.  The central
        # adapter applies the frozen arbitration module over per-symbol rows.
        if len(candidates) != 1:
            return NoTradeV1(primary_reason_code="DIRECTION_AMBIGUOUS")
        result = candidates[0]
        template_id = "CALL_DEBIT_SPREAD_V1" if result.action == "BUY" else "PUT_DEBIT_SPREAD_V1"
        matching = [
            item
            for item in context.allowed_intent_tuples
            if item.template_id == template_id
            and item.horizon_bucket == "INTRADAY_15_60M"
            and item.risk_tier == "TINY"
            and item.max_intent_ttl_seconds == intent_ttl
        ]
        if not matching:
            has_template = any(item.template_id == template_id for item in context.allowed_intent_tuples)
            return NoTradeV1(
                primary_reason_code="TUPLE_NOT_ALLOWED" if has_template else "TEMPLATE_NOT_ALLOWED"
            )
        score = result.score or Decimal("0")
        bucket = "LOW" if score < Decimal("1.25") else "MEDIUM" if score < Decimal("1.75") else "HIGH"
        return EntryTemplateRequestV1(
            underlying=result.underlying,
            template_id=template_id,
            horizon_bucket=matching[0].horizon_bucket,
            risk_tier=matching[0].risk_tier,
            signal_strength_bucket=bucket,
            intent_expires_at=context.as_of + timedelta(seconds=intent_ttl),
            entry_reason_codes=result.reason_codes,
            evidence_refs=(
                ArtifactRefV1(
                    artifact_type="FEATURE_VECTOR",
                    content_hash=context.feature_vector_hash,
                    record_id=context.feature_vector_id,
                ),
            ),
        )
