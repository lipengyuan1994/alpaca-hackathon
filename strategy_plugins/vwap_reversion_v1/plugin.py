"""Framework adapter for the frozen Group A VWAP-reversion signal."""

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

FEATURE_CONTRACT_HASH = "sha256:bed7fb8429134110c1c6f9f2b1020d306b50910fd8d58b9900096995b2b566a3"
ALLOWED_UNDERLYINGS = ("SPY", "QQQ", "SMH", "IGV")
LOCAL_FEATURE_KEYS = ("deviation_z_same_time_v1", "momentum_z_60m_same_time_v1")
REQUIRED_FEATURE_KEYS = tuple(
    f"{symbol}__{feature}" for symbol in ALLOWED_UNDERLYINGS for feature in LOCAL_FEATURE_KEYS
)
_EASTERN = ZoneInfo("America/New_York")


def _decimal_config(config: StrategyConfigV1, key: str, default: str) -> Decimal:
    try:
        value = Decimal(str(config.values.get(key, default)))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("CONFIG_DECIMAL_INVALID") from exc
    if not value.is_finite():
        raise ValueError("CONFIG_DECIMAL_INVALID")
    return value


def _is_decision_time(context: StrategyContextV1) -> bool:
    value = context.as_of.astimezone(_EASTERN)
    if value.weekday() >= 5 or value.second != 1 or value.microsecond != 0:
        return False
    if value.hour == 10:
        return value.minute == 30
    if value.hour in (11, 12, 13):
        return value.minute in (0, 30)
    return value.hour == 14 and value.minute in (0, 30)


def _next_state(context: StrategyContextV1, payload: dict[str, str] | None = None):
    return context.prior_state.model_copy(
        update={
            "plugin_id": "vwap_reversion",
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
            plugin_id="vwap_reversion",
            plugin_version="1.0.0",
            owner="assigned_group_a_research_owner",
            economic_hypothesis_id="NORMALIZED_VWAP_REVERSION",
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
        flags = set(context.quality_flags)
        if "EARLY_CLOSE_SESSION" in flags:
            return NoTradeV1(primary_reason_code="EARLY_CLOSE_SESSION")
        if flags:
            return NoTradeV1(primary_reason_code="DATA_QUALITY_REJECTED")
        if not _is_decision_time(context):
            return NoTradeV1(primary_reason_code="OUTSIDE_DECISION_WINDOW")
        if any(key not in context.universe_features for key in REQUIRED_FEATURE_KEYS):
            return NoTradeV1(primary_reason_code="DATA_MISSING")
        try:
            threshold = _decimal_config(config, "deviation_threshold", "1.50")
            neutral = _decimal_config(config, "momentum_neutral_abs_max", "0.50")
        except ValueError:
            return NoTradeV1(primary_reason_code="FEATURE_SCHEMA_MISMATCH")
        if threshold <= 0 or neutral < 0:
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
                deviation_threshold=threshold,
                momentum_neutral_abs_max=neutral,
            )
            if result.action != "NO_TRADE":
                if context.prior_state.payload.get(f"last_entry_session_{symbol}") == today:
                    had_used_candidate = True
                    continue
                candidates.append(result)
        if not candidates:
            if had_used_candidate:
                return NoTradeV1(primary_reason_code="DAILY_ENTRY_ALREADY_USED")
            return NoTradeV1(primary_reason_code="VWAP_REVERSION_GATE_NOT_MET")
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
            intent_expires_at=context.as_of + timedelta(seconds=matching[0].max_intent_ttl_seconds),
            entry_reason_codes=result.reason_codes,
            evidence_refs=(
                ArtifactRefV1(
                    artifact_type="FEATURE_VECTOR",
                    content_hash=context.feature_vector_hash,
                    record_id=context.feature_vector_id,
                ),
            ),
        )
