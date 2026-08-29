"""Strict V1 payloads.  These models deliberately contain no I/O or adapters."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal, Union
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .canonical import hash_without


class ContractError(ValueError):
    """Stable local validation error for contract-invariant violations."""


_OCC_SYMBOL = re.compile(
    r"^(?P<root>[A-Z]{1,6})(?P<expiry>[0-9]{6})(?P<right>[CP])(?P<strike>[0-9]{8})$"
)


def validate_occ_identity(
    *,
    symbol: str,
    underlying: str,
    right: str,
    strike: Decimal,
    expiration: datetime,
) -> None:
    match = _OCC_SYMBOL.fullmatch(symbol)
    if match is None:
        raise ContractError("option symbol is not a standard OCC identity")
    encoded_expiration = datetime.strptime(match.group("expiry"), "%y%m%d").date()
    encoded_strike = Decimal(match.group("strike")) / Decimal("1000")
    encoded_right = "CALL" if match.group("right") == "C" else "PUT"
    if (
        match.group("root") != underlying
        or encoded_right != right
        or encoded_strike != strike
        or encoded_expiration != expiration.date()
    ):
        raise ContractError("option symbol metadata mismatch")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ContractError("timestamp must include a UTC offset")
    normalized = value.astimezone(UTC)
    return normalized


class TimestampedModel(StrictModel):
    @field_validator("*", mode="after", check_fields=False)
    @classmethod
    def _validate_datetimes(cls, value: Any) -> Any:
        return _require_utc(value) if isinstance(value, datetime) else value


class FeedIdentityV1(StrictModel):
    equity: Literal["iex"] = "iex"
    options: Literal["indicative"] = "indicative"
    entitlement: str = Field(min_length=1, max_length=128)


class MarketClockV1(TimestampedModel):
    """Broker-observed market clock; local wall-clock guesses are not authority."""

    is_open: bool
    as_of: datetime
    next_open: datetime
    next_close: datetime

    @model_validator(mode="after")
    def _ordered(self) -> "MarketClockV1":
        if self.next_open <= self.as_of and not self.is_open:
            raise ContractError("closed market clock next_open must be in the future")
        if self.next_close <= self.as_of and self.is_open:
            raise ContractError("open market clock next_close must be in the future")
        return self


class QuoteV1(TimestampedModel):
    bid: Decimal = Field(ge=0)
    ask: Decimal = Field(ge=0)
    event_time: datetime
    available_time: datetime

    @model_validator(mode="after")
    def _not_crossed(self) -> "QuoteV1":
        if self.ask < self.bid:
            raise ContractError("quote is crossed")
        if self.available_time < self.event_time:
            raise ContractError("quote available_time precedes event_time")
        return self


class OptionContractV1(StrictModel):
    symbol: str = Field(pattern=r"^[A-Z0-9]{1,32}$")
    underlying: str = Field(pattern=r"^[A-Z]{1,8}$")
    right: Literal["CALL", "PUT"]
    strike: Decimal = Field(gt=0)
    expiration: datetime
    multiplier: Literal[100] = 100
    quote: QuoteV1
    deliverable: Literal["STANDARD"] = "STANDARD"

    @model_validator(mode="after")
    def _occ_identity_matches(self) -> "OptionContractV1":
        validate_occ_identity(
            symbol=self.symbol,
            underlying=self.underlying,
            right=self.right,
            strike=self.strike,
            expiration=self.expiration,
        )
        return self


class MarketSnapshotV1(TimestampedModel):
    schema_version: Literal["market-snapshot/v1"] = "market-snapshot/v1"
    snapshot_id: str = Field(min_length=1, max_length=128)
    as_of: datetime
    feed_identity: FeedIdentityV1
    clock: MarketClockV1 | None = None
    underlying_quotes: dict[str, QuoteV1] = Field(min_length=1)
    option_contracts: tuple[OptionContractV1, ...] = ()
    quality_flags: tuple[str, ...] = ()
    content_hash: str | None = None

    @model_validator(mode="after")
    def _hash_matches(self) -> "MarketSnapshotV1":
        expected = hash_without(self, "content_hash")
        if self.content_hash is not None and self.content_hash != expected:
            raise ContractError("market snapshot content_hash mismatch")
        object.__setattr__(self, "content_hash", expected)
        return self

    def quote_age(self, now: datetime, symbol: str) -> timedelta:
        return _require_utc(now) - self.underlying_quotes[symbol].event_time


class AccountSnapshotV1(TimestampedModel):
    schema_version: Literal["account-snapshot/v1"] = "account-snapshot/v1"
    snapshot_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=0)
    as_of: datetime
    equity: Decimal = Field(ge=0)
    day_start_equity: Decimal | None = Field(default=None, ge=0)
    cash: Decimal = Field(ge=0)
    buying_power: Decimal = Field(ge=0)
    content_hash: str | None = None

    @model_validator(mode="after")
    def _hash_matches(self) -> "AccountSnapshotV1":
        expected = hash_without(self, "content_hash")
        if self.content_hash is not None and self.content_hash != expected:
            raise ContractError("account snapshot content_hash mismatch")
        object.__setattr__(self, "content_hash", expected)
        return self


class PositionLegV1(StrictModel):
    symbol: str = Field(pattern=r"^[A-Z0-9]{1,32}$")
    quantity: int


class PositionSnapshotV1(TimestampedModel):
    schema_version: Literal["position-snapshot/v1"] = "position-snapshot/v1"
    snapshot_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    version: int = Field(ge=0)
    as_of: datetime
    legs: tuple[PositionLegV1, ...] = ()
    content_hash: str | None = None

    @model_validator(mode="after")
    def _hash_matches(self) -> "PositionSnapshotV1":
        expected = hash_without(self, "content_hash")
        if self.content_hash is not None and self.content_hash != expected:
            raise ContractError("position snapshot content_hash mismatch")
        object.__setattr__(self, "content_hash", expected)
        return self


class RiskReservationV1(TimestampedModel):
    reservation_id: str = Field(min_length=1)
    plan_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    maximum_loss: Decimal = Field(gt=0)
    remaining_quantity: int = Field(gt=0)
    expires_at: datetime
    status: Literal["APPROVED", "ACCEPTED", "PARTIAL", "UNKNOWN"]


class OrderRiskSnapshotV1(TimestampedModel):
    schema_version: Literal["order-risk-snapshot/v1"] = "order-risk-snapshot/v1"
    snapshot_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    version: int = Field(ge=0)
    as_of: datetime
    reservations: tuple[RiskReservationV1, ...] = ()
    working_client_order_ids: tuple[str, ...] = ()
    content_hash: str | None = None

    @model_validator(mode="after")
    def _hash_matches(self) -> "OrderRiskSnapshotV1":
        expected = hash_without(self, "content_hash")
        if self.content_hash is not None and self.content_hash != expected:
            raise ContractError("order risk snapshot content_hash mismatch")
        object.__setattr__(self, "content_hash", expected)
        return self

    @property
    def reserved_maximum_loss(self) -> Decimal:
        return sum((item.maximum_loss for item in self.reservations), Decimal("0"))


class FeatureVectorV1(TimestampedModel):
    schema_version: Literal["feature-vector/v1"] = "feature-vector/v1"
    feature_id: str = Field(min_length=1)
    feature_contract_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    calculated_at: datetime
    available_time: datetime
    values: dict[str, Decimal]
    source_market_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    content_hash: str | None = None

    @model_validator(mode="after")
    def _hash_matches(self) -> "FeatureVectorV1":
        if self.available_time < self.calculated_at:
            raise ContractError("feature availability precedes calculation")
        if any(not value.is_finite() for value in self.values.values()):
            raise ContractError("feature vector values must be finite")
        expected = hash_without(self, "content_hash")
        if self.content_hash is not None and self.content_hash != expected:
            raise ContractError("feature vector content_hash mismatch")
        object.__setattr__(self, "content_hash", expected)
        return self


class ArtifactRefV1(StrictModel):
    artifact_type: Literal["FEATURE_VECTOR", "RESEARCH_REPORT", "DATASET", "MODEL_OUTPUT"]
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    record_id: str | None = Field(default=None, max_length=128)


class IntentTupleV1(StrictModel):
    template_id: Literal["CALL_DEBIT_SPREAD_V1", "PUT_DEBIT_SPREAD_V1", "LONG_CALL_V1", "LONG_PUT_V1"]
    horizon_bucket: Literal["INTRADAY_15_60M"]
    risk_tier: Literal["TINY", "STANDARD"]
    max_intent_ttl_seconds: int = Field(ge=1, le=300)


class StrategyMetadataV1(StrictModel):
    api_version: Literal["strategy-plugin/v1"] = "strategy-plugin/v1"
    plugin_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    plugin_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    decision_schema_version: Literal["strategy-evaluation/v1"] = "strategy-evaluation/v1"
    owner: str = Field(min_length=1, max_length=128)
    economic_hypothesis_id: str = Field(min_length=1, max_length=128)
    deterministic: Literal[True] = True


class DataRequirementsV1(StrictModel):
    underlyings: tuple[str, ...] = Field(min_length=1)
    feature_schema_version: Literal["feature-vector/v1"] = "feature-vector/v1"
    feature_contract_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    required_feature_keys: tuple[str, ...] = ()
    maximum_observation_age_seconds: int = Field(ge=1, le=900)
    needs_logical_positions: bool = False

    @model_validator(mode="after")
    def _feature_keys_unique(self) -> "DataRequirementsV1":
        if tuple(dict.fromkeys(self.required_feature_keys)) != self.required_feature_keys:
            raise ContractError("required feature keys must be unique and ordered")
        return self


class StrategyStateV1(TimestampedModel):
    state_schema_version: Literal["strategy-state/v1"] = "strategy-state/v1"
    plugin_id: str
    plugin_version: str
    as_of: datetime
    sequence: int = Field(ge=0)
    payload: dict[str, str] = Field(default_factory=dict)
    state_hash: str | None = None

    @model_validator(mode="after")
    def _hash_matches(self) -> "StrategyStateV1":
        expected = hash_without(self, "state_hash")
        if self.state_hash is not None and self.state_hash != expected:
            raise ContractError("strategy state hash mismatch")
        object.__setattr__(self, "state_hash", expected)
        return self


class NoTradeV1(TimestampedModel):
    kind: Literal["NO_TRADE"] = "NO_TRADE"
    primary_reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,95}$")
    retry_after: datetime | None = None


class EntryTemplateRequestV1(TimestampedModel):
    kind: Literal["ENTRY_TEMPLATE_REQUEST"] = "ENTRY_TEMPLATE_REQUEST"
    underlying: str = Field(pattern=r"^[A-Z]{1,8}$")
    template_id: Literal["CALL_DEBIT_SPREAD_V1", "PUT_DEBIT_SPREAD_V1", "LONG_CALL_V1", "LONG_PUT_V1"]
    horizon_bucket: Literal["INTRADAY_15_60M"]
    risk_tier: Literal["TINY", "STANDARD"]
    signal_strength_bucket: Literal["LOW", "MEDIUM", "HIGH"]
    intent_expires_at: datetime
    entry_reason_codes: tuple[str, ...] = Field(min_length=1)
    evidence_refs: tuple[ArtifactRefV1, ...] = ()


class PositionDirectiveV1(TimestampedModel):
    kind: Literal["POSITION_DIRECTIVE"] = "POSITION_DIRECTIVE"
    strategy_position_id: str = Field(min_length=1, max_length=128)
    action: Literal["HOLD", "REDUCE", "CLOSE"]
    urgency: Literal["NORMAL", "RISK_EXIT"]
    reason_codes: tuple[str, ...] = Field(min_length=1)
    directive_expires_at: datetime


StrategyDecisionV1 = Annotated[
    Union[NoTradeV1, EntryTemplateRequestV1, PositionDirectiveV1], Field(discriminator="kind")
]


class StrategyContextV1(TimestampedModel):
    schema_version: Literal["strategy-context/v1"] = "strategy-context/v1"
    evaluation_id: str = Field(min_length=1)
    as_of: datetime
    market_snapshot_id: str
    market_snapshot_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    feature_vector_id: str
    feature_vector_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    feature_contract_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    feature_available_time: datetime
    feed_identity: FeedIdentityV1
    quality_flags: tuple[str, ...] = ()
    universe_features: dict[str, Decimal]
    option_surface_summaries: dict[str, Decimal] = Field(default_factory=dict)
    logical_positions: tuple[str, ...] = ()
    allowed_intent_tuples: tuple[IntentTupleV1, ...] = Field(min_length=1)
    prior_state: StrategyStateV1
    config_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    context_hash: str | None = None

    @model_validator(mode="after")
    def _hash_matches(self) -> "StrategyContextV1":
        if self.feature_available_time > self.as_of:
            raise ContractError("feature vector is not yet available")
        if any(not value.is_finite() for value in self.universe_features.values()):
            raise ContractError("strategy context features must be finite")
        expected = hash_without(self, "context_hash")
        if self.context_hash is not None and self.context_hash != expected:
            raise ContractError("strategy context hash mismatch")
        object.__setattr__(self, "context_hash", expected)
        return self


class StrategyConfigV1(StrictModel):
    schema_version: Literal["strategy-config/v1"] = "strategy-config/v1"
    values: dict[str, Decimal | str | int | bool]
    config_hash: str | None = None

    @model_validator(mode="after")
    def _hash_matches(self) -> "StrategyConfigV1":
        expected = hash_without(self, "config_hash")
        if self.config_hash is not None and self.config_hash != expected:
            raise ContractError("strategy config hash mismatch")
        object.__setattr__(self, "config_hash", expected)
        return self


class StrategyEvaluationV1(TimestampedModel):
    schema_version: Literal["strategy-evaluation/v1"] = "strategy-evaluation/v1"
    evaluation_id: str
    plugin_id: str
    plugin_version: str
    plugin_content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    context_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    config_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    decision: StrategyDecisionV1
    next_state: StrategyStateV1
    evaluation_hash: str | None = None

    @model_validator(mode="after")
    def _consistency_and_hash(self) -> "StrategyEvaluationV1":
        if self.next_state.plugin_id != self.plugin_id or self.next_state.plugin_version != self.plugin_version:
            raise ContractError("next state does not belong to evaluation plugin")
        expected = hash_without(self, "evaluation_hash")
        if self.evaluation_hash is not None and self.evaluation_hash != expected:
            raise ContractError("strategy evaluation hash mismatch")
        object.__setattr__(self, "evaluation_hash", expected)
        return self


class AgentNarrativeV1(StrictModel):
    """Display-only explanation from the advisory model.

    The resolver never reads these fields.  They are retained in the frozen
    thesis artifact so a public replay can explain an allow or veto without
    allowing prose to alter any executable semantic field.
    """

    market_thesis: str = Field(min_length=1, max_length=2_000)
    counter_thesis: str = Field(min_length=1, max_length=2_000)
    explanation: str = Field(min_length=1, max_length=4_000)


class AgentThesisV1(TimestampedModel):
    schema_version: Literal["agent-thesis/v1"] = "agent-thesis/v1"
    thesis_id: str = Field(min_length=1)
    context_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    strategy_evaluation_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    model_input_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    model_version: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    raw_output_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    recommendation: Literal["ALLOW_UNCHANGED", "VETO"]
    diagnostic_confidence: Decimal = Field(ge=0, le=1)
    expires_at: datetime
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,95}$")
    # Optional only for compatibility with previously frozen replay artifacts.
    # New model adapters must populate it before publishing a public tape.
    narrative: AgentNarrativeV1 | None = None
    content_hash: str | None = None

    @model_validator(mode="after")
    def _hash_and_ttl(self) -> "AgentThesisV1":
        expected = hash_without(self, "content_hash")
        if self.content_hash is not None and self.content_hash != expected:
            raise ContractError("agent thesis content_hash mismatch")
        object.__setattr__(self, "content_hash", expected)
        return self


class PositionPolicyIdV1(StrEnum):
    TREND_VWAP_OR_60M_V1 = "TREND_VWAP_OR_60M_V1"
    REVERSION_VWAP_TOUCH_OR_60M_V1 = "REVERSION_VWAP_TOUCH_OR_60M_V1"


class TradeIntentV1(TimestampedModel):
    schema_version: Literal["trade-intent/v1"] = "trade-intent/v1"
    intent_id: str = Field(min_length=1)
    as_of: datetime
    underlying: str = Field(pattern=r"^[A-Z]{1,8}$")
    template_id: Literal["CALL_DEBIT_SPREAD_V1", "PUT_DEBIT_SPREAD_V1", "LONG_CALL_V1", "LONG_PUT_V1"]
    direction: Literal["BULLISH", "BEARISH"]
    horizon_bucket: Literal["INTRADAY_15_60M"]
    risk_tier: Literal["TINY", "STANDARD"]
    position_policy_id: PositionPolicyIdV1
    expires_at: datetime
    strategy_evaluation_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    thesis_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    content_hash: str | None = None

    @model_validator(mode="after")
    def _hash_and_ttl(self) -> "TradeIntentV1":
        if self.expires_at <= self.as_of:
            raise ContractError("intent expiry must be after intent time")
        expected = hash_without(self, "content_hash")
        if self.content_hash is not None and self.content_hash != expected:
            raise ContractError("trade intent content_hash mismatch")
        object.__setattr__(self, "content_hash", expected)
        return self


class OrderLegV1(StrictModel):
    symbol: str = Field(pattern=r"^[A-Z0-9]{1,32}$")
    side: Literal["BUY", "SELL"]
    quantity: int = Field(gt=0)
    right: Literal["CALL", "PUT"]
    strike: Decimal = Field(gt=0)
    expiration: datetime
    multiplier: Literal[100] = 100

    @model_validator(mode="after")
    def _occ_identity_matches(self) -> "OrderLegV1":
        match = _OCC_SYMBOL.fullmatch(self.symbol)
        if match is None:
            raise ContractError("order leg symbol is not a standard OCC identity")
        encoded_expiration = datetime.strptime(match.group("expiry"), "%y%m%d").date()
        encoded_strike = Decimal(match.group("strike")) / Decimal("1000")
        encoded_right = "CALL" if match.group("right") == "C" else "PUT"
        if (
            encoded_right != self.right
            or encoded_strike != self.strike
            or encoded_expiration != self.expiration.date()
        ):
            raise ContractError("order leg symbol metadata mismatch")
        return self


class OrderPlanV1(TimestampedModel):
    schema_version: Literal["order-plan/v1"] = "order-plan/v1"
    plan_id: str = Field(min_length=1)
    intent_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    underlying: str = Field(pattern=r"^[A-Z]{1,8}$")
    template_id: Literal["CALL_DEBIT_SPREAD_V1", "PUT_DEBIT_SPREAD_V1", "LONG_CALL_V1", "LONG_PUT_V1"]
    position_policy_id: PositionPolicyIdV1
    legs: tuple[OrderLegV1, ...] = Field(min_length=1, max_length=4)
    quantity: int = Field(gt=0)
    limit_debit: Decimal = Field(gt=0)
    time_in_force: Literal["DAY"] = "DAY"
    client_order_id: str = Field(pattern=r"^[a-z0-9-]{8,48}$")
    market_snapshot_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    account_snapshot_version: int = Field(ge=0)
    position_snapshot_version: int = Field(ge=0)
    order_risk_snapshot_version: int = Field(ge=0)
    maximum_loss: Decimal = Field(gt=0)
    plan_hash: str | None = None

    def recompute_maximum_loss(self) -> Decimal:
        """Return the full debit at risk for the supported defined-risk plans."""
        if not self.legs:
            raise ContractError("order plan must contain at least one leg")
        multipliers = {leg.multiplier for leg in self.legs}
        if len(multipliers) != 1:
            raise ContractError("order plan legs must share one multiplier")
        return self.limit_debit * Decimal(multipliers.pop()) * self.quantity

    def vertical_width(self) -> Decimal | None:
        """Return per-share vertical width, or ``None`` for a long option."""
        if not self.template_id.endswith("SPREAD_V1"):
            return None
        if len(self.legs) != 2:
            raise ContractError("vertical plan must contain exactly two legs")
        return abs(self.legs[0].strike - self.legs[1].strike)

    @model_validator(mode="after")
    def _defined_risk_and_hash(self) -> "OrderPlanV1":
        if self.limit_debit != self.limit_debit.quantize(Decimal("0.01")):
            raise ContractError("order limit debit must use cent precision")
        if any(
            (match := _OCC_SYMBOL.fullmatch(leg.symbol)) is None
            or match.group("root") != self.underlying
            for leg in self.legs
        ):
            raise ContractError("order leg OCC root does not match plan underlying")
        buy_legs = [leg for leg in self.legs if leg.side == "BUY"]
        sell_legs = [leg for leg in self.legs if leg.side == "SELL"]
        if self.template_id.endswith("SPREAD_V1"):
            if len(self.legs) != 2 or len(buy_legs) != 1 or len(sell_legs) != 1:
                raise ContractError("vertical plan must contain one buy and one sell leg")
            if buy_legs[0].right != sell_legs[0].right or buy_legs[0].expiration != sell_legs[0].expiration:
                raise ContractError("vertical legs must share right and expiration")
            if buy_legs[0].quantity != self.quantity or sell_legs[0].quantity != self.quantity:
                raise ContractError("leg quantities must equal plan quantity")
            if self.template_id == "CALL_DEBIT_SPREAD_V1" and buy_legs[0].strike >= sell_legs[0].strike:
                raise ContractError("call debit spread must buy lower strike")
            if self.template_id == "PUT_DEBIT_SPREAD_V1" and buy_legs[0].strike <= sell_legs[0].strike:
                raise ContractError("put debit spread must buy higher strike")
            expected_right = "CALL" if self.template_id == "CALL_DEBIT_SPREAD_V1" else "PUT"
            if buy_legs[0].right != expected_right:
                raise ContractError("vertical leg right does not match template")
            width = self.vertical_width()
            if width is None or self.limit_debit >= width:
                raise ContractError("vertical debit must be strictly below spread width")
        else:
            if len(self.legs) != 1 or len(buy_legs) != 1 or sell_legs:
                raise ContractError("long option plan must contain exactly one buy leg")
            if buy_legs[0].quantity != self.quantity:
                raise ContractError("leg quantity must equal plan quantity")
            expected_right = "CALL" if self.template_id == "LONG_CALL_V1" else "PUT"
            if buy_legs[0].right != expected_right:
                raise ContractError("long option leg right does not match template")
        recomputed_maximum_loss = self.recompute_maximum_loss()
        if self.maximum_loss != recomputed_maximum_loss:
            raise ContractError("maximum loss does not equal full order debit")
        expected = hash_without(self, "plan_hash")
        if self.plan_hash is not None and self.plan_hash != expected:
            raise ContractError("order plan hash mismatch")
        object.__setattr__(self, "plan_hash", expected)
        return self


class OperatingModeV1(StrEnum):
    DISARMED = "DISARMED"
    REPLAY = "REPLAY"
    SHADOW = "SHADOW"
    PAPER_DEMO_ARMED = "PAPER_DEMO_ARMED"
    PAPER_ARMED = "PAPER_ARMED"
    FLATTENING = "FLATTENING"
    HALTED = "HALTED"


class RiskPolicyV1(StrictModel):
    policy_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    max_per_trade_loss: Decimal = Field(gt=0)
    max_daily_loss: Decimal = Field(gt=0)
    max_total_reserved_loss: Decimal = Field(gt=0)
    quote_ttl_seconds: int = Field(ge=1, le=300)
    approval_ttl_seconds: int = Field(ge=1, le=300)

    @model_validator(mode="after")
    def _hash_matches(self) -> "RiskPolicyV1":
        expected = hash_without(self, "policy_hash")
        if self.policy_hash != expected:
            raise ContractError("risk policy hash mismatch")
        return self


class RiskInputV1(TimestampedModel):
    schema_version: Literal["risk-input/v1"] = "risk-input/v1"
    plan: OrderPlanV1
    market_snapshot_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    account_snapshot_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    position_snapshot_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    order_risk_snapshot_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    risk_policy: RiskPolicyV1
    template_catalog_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    strategy_registry_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    strategy_config_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    strategy_content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    mode: OperatingModeV1
    control_state_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    control_state_version: int = Field(ge=0)
    account_allowlist_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    release_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    entry_cutoff_at: datetime
    flatten_at: datetime
    risk_input_hash: str | None = None

    @model_validator(mode="after")
    def _plan_binding_and_hash(self) -> "RiskInputV1":
        if self.plan.market_snapshot_hash != self.market_snapshot_hash:
            raise ContractError("risk input market hash does not bind plan")
        if self.entry_cutoff_at >= self.flatten_at:
            raise ContractError("entry cutoff must precede flatten time")
        expected = hash_without(self, "risk_input_hash")
        if self.risk_input_hash is not None and self.risk_input_hash != expected:
            raise ContractError("risk input hash mismatch")
        object.__setattr__(self, "risk_input_hash", expected)
        return self


class RiskDecisionV1(TimestampedModel):
    schema_version: Literal["risk-decision/v1"] = "risk-decision/v1"
    decision_id: str = Field(min_length=1)
    plan_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    risk_input_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    approved: bool
    reason_codes: tuple[str, ...] = Field(min_length=1)
    maximum_loss: Decimal = Field(ge=0)
    expires_at: datetime
    reservation_id: str | None = None
    decision_hash: str | None = None

    @model_validator(mode="after")
    def _approval_binding_and_hash(self) -> "RiskDecisionV1":
        if self.approved and self.reservation_id is None:
            raise ContractError("approved risk decision requires reservation")
        expected = hash_without(self, "decision_hash")
        if self.decision_hash is not None and self.decision_hash != expected:
            raise ContractError("risk decision hash mismatch")
        object.__setattr__(self, "decision_hash", expected)
        return self


class ExecuteApprovedPlanV1(TimestampedModel):
    schema_version: Literal["execute-approved-plan/v1"] = "execute-approved-plan/v1"
    command_id: str = Field(min_length=1)
    plan: OrderPlanV1
    approval: RiskDecisionV1
    risk_input_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    market_snapshot_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    account_snapshot_version: int = Field(ge=0)
    position_snapshot_version: int = Field(ge=0)
    order_risk_snapshot_version: int = Field(ge=0)
    control_state_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    control_state_version: int = Field(ge=0)
    command_hash: str | None = None

    @model_validator(mode="after")
    def _bindings_and_hash(self) -> "ExecuteApprovedPlanV1":
        if not self.approval.approved:
            raise ContractError("rejected risk decision cannot be executed")
        if self.approval.plan_hash != self.plan.plan_hash or self.approval.risk_input_hash != self.risk_input_hash:
            raise ContractError("execution command approval binding mismatch")
        if self.plan.market_snapshot_hash != self.market_snapshot_hash:
            raise ContractError("execution command market binding mismatch")
        expected = hash_without(self, "command_hash")
        if self.command_hash is not None and self.command_hash != expected:
            raise ContractError("execution command hash mismatch")
        object.__setattr__(self, "command_hash", expected)
        return self


class ManagedPositionV1(TimestampedModel):
    """Logical position owned by the platform, never by a broker-facing plug-in."""

    schema_version: Literal["managed-position/v1"] = "managed-position/v1"
    strategy_position_id: str = Field(min_length=1, max_length=128)
    account_id: str = Field(min_length=1, max_length=128)
    underlying: str = Field(pattern=r"^[A-Z]{1,8}$")
    direction: Literal["BULLISH", "BEARISH"]
    opened_at: datetime
    current_quantity: int = Field(gt=0)
    position_policy_id: PositionPolicyIdV1
    entry_plan: OrderPlanV1
    status: Literal["OPEN", "CLOSING"] = "OPEN"
    content_hash: str | None = None

    @model_validator(mode="after")
    def _bindings_and_hash(self) -> "ManagedPositionV1":
        if self.entry_plan.account_id != self.account_id:
            raise ContractError("managed position account does not match entry plan")
        if self.entry_plan.underlying != self.underlying:
            raise ContractError("managed position underlying does not match entry plan")
        if self.entry_plan.position_policy_id != self.position_policy_id:
            raise ContractError("managed position policy does not match entry plan")
        if self.current_quantity > self.entry_plan.quantity:
            raise ContractError("managed position quantity exceeds entry quantity")
        expected = hash_without(self, "content_hash")
        if self.content_hash is not None and self.content_hash != expected:
            raise ContractError("managed position content_hash mismatch")
        object.__setattr__(self, "content_hash", expected)
        return self


class PositionMarketStateV1(TimestampedModel):
    """Completed-bar state used by deterministic central exit policies."""

    schema_version: Literal["position-market-state/v1"] = "position-market-state/v1"
    as_of: datetime
    underlying_price: Decimal = Field(gt=0)
    session_vwap: Decimal = Field(gt=0)
    completed_bar: Literal[True] = True
    competition_flatten_at: datetime


class ReduceOnlyOrderPlanV1(TimestampedModel):
    """Atomic exact-leg close plan that can only reduce a reconciled position."""

    schema_version: Literal["reduce-only-order-plan/v1"] = "reduce-only-order-plan/v1"
    plan_id: str = Field(min_length=1)
    strategy_position_id: str = Field(min_length=1, max_length=128)
    account_id: str = Field(min_length=1, max_length=128)
    underlying: str = Field(pattern=r"^[A-Z]{1,8}$")
    template_id: Literal["CALL_DEBIT_SPREAD_V1", "PUT_DEBIT_SPREAD_V1", "LONG_CALL_V1", "LONG_PUT_V1"]
    legs: tuple[OrderLegV1, ...] = Field(min_length=1, max_length=4)
    quantity: int = Field(gt=0)
    limit_price: Decimal = Field(gt=0)
    price_effect: Literal["CREDIT", "DEBIT"]
    reduce_only: Literal[True] = True
    time_in_force: Literal["DAY"] = "DAY"
    client_order_id: str = Field(pattern=r"^[a-z0-9-]{8,48}$")
    market_snapshot_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    account_snapshot_version: int = Field(ge=0)
    position_snapshot_version: int = Field(ge=0)
    control_state_version: int = Field(ge=0)
    position_policy_id: PositionPolicyIdV1
    close_reason_codes: tuple[str, ...] = Field(min_length=1)
    plan_hash: str | None = None

    @model_validator(mode="after")
    def _shape_and_hash(self) -> "ReduceOnlyOrderPlanV1":
        if self.limit_price != self.limit_price.quantize(Decimal("0.01")):
            raise ContractError("reduce-only limit price must use cent precision")
        if any(
            (match := _OCC_SYMBOL.fullmatch(leg.symbol)) is None
            or match.group("root") != self.underlying
            for leg in self.legs
        ):
            raise ContractError("reduce-only leg OCC root does not match plan underlying")
        if any(leg.quantity != self.quantity for leg in self.legs):
            raise ContractError("reduce-only leg quantities must equal plan quantity")
        if len({leg.symbol for leg in self.legs}) != len(self.legs):
            raise ContractError("reduce-only plan contains duplicate leg symbols")
        expected = hash_without(self, "plan_hash")
        if self.plan_hash is not None and self.plan_hash != expected:
            raise ContractError("reduce-only plan hash mismatch")
        object.__setattr__(self, "plan_hash", expected)
        return self


class ReduceOnlyDecisionV1(TimestampedModel):
    schema_version: Literal["reduce-only-decision/v1"] = "reduce-only-decision/v1"
    decision_id: str = Field(min_length=1)
    plan_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    position_snapshot_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    managed_position_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    control_state_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    allowed: bool
    reason_codes: tuple[str, ...] = Field(min_length=1)
    expires_at: datetime
    decision_hash: str | None = None

    @model_validator(mode="after")
    def _hash_matches(self) -> "ReduceOnlyDecisionV1":
        expected = hash_without(self, "decision_hash")
        if self.decision_hash is not None and self.decision_hash != expected:
            raise ContractError("reduce-only decision hash mismatch")
        object.__setattr__(self, "decision_hash", expected)
        return self


class ExecuteReduceOnlyPlanV1(TimestampedModel):
    schema_version: Literal["execute-reduce-only-plan/v1"] = "execute-reduce-only-plan/v1"
    command_id: str = Field(min_length=1)
    plan: ReduceOnlyOrderPlanV1
    approval: ReduceOnlyDecisionV1
    market_snapshot_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    account_snapshot_version: int = Field(ge=0)
    position_snapshot_version: int = Field(ge=0)
    control_state_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    control_state_version: int = Field(ge=0)
    command_hash: str | None = None

    @model_validator(mode="after")
    def _bindings_and_hash(self) -> "ExecuteReduceOnlyPlanV1":
        if not self.approval.allowed:
            raise ContractError("rejected reduce-only decision cannot be executed")
        if self.approval.plan_hash != self.plan.plan_hash:
            raise ContractError("reduce-only command approval binding mismatch")
        if self.plan.market_snapshot_hash != self.market_snapshot_hash:
            raise ContractError("reduce-only command market binding mismatch")
        if self.approval.control_state_hash != self.control_state_hash:
            raise ContractError("reduce-only command control binding mismatch")
        expected = hash_without(self, "command_hash")
        if self.command_hash is not None and self.command_hash != expected:
            raise ContractError("reduce-only command hash mismatch")
        object.__setattr__(self, "command_hash", expected)
        return self


class ExecutionPreflightDecisionV1(TimestampedModel):
    schema_version: Literal["execution-preflight/v1"] = "execution-preflight/v1"
    command_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    allowed: bool
    reason_codes: tuple[str, ...] = Field(min_length=1)
    checked_at: datetime
    content_hash: str | None = None

    @model_validator(mode="after")
    def _hash_matches(self) -> "ExecutionPreflightDecisionV1":
        expected = hash_without(self, "content_hash")
        if self.content_hash is not None and self.content_hash != expected:
            raise ContractError("preflight content_hash mismatch")
        object.__setattr__(self, "content_hash", expected)
        return self


class BrokerEventV1(TimestampedModel):
    schema_version: Literal["broker-event/v1"] = "broker-event/v1"
    client_order_id: str
    status: Literal["ACCEPTED", "REJECTED", "UNKNOWN", "PARTIAL", "FILLED", "CANCELLED", "EXPIRED"]
    occurred_at: datetime
    broker_order_id: str | None = None
    filled_quantity: int = Field(ge=0, default=0)
    reason_code: str | None = None
    content_hash: str | None = None

    @model_validator(mode="after")
    def _hash_matches(self) -> "BrokerEventV1":
        expected = hash_without(self, "content_hash")
        if self.content_hash is not None and self.content_hash != expected:
            raise ContractError("broker event content_hash mismatch")
        object.__setattr__(self, "content_hash", expected)
        return self


class EventEnvelopeV1(TimestampedModel):
    schema_version: Literal["event-envelope/v1"] = "event-envelope/v1"
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    event_type: str = Field(pattern=r"^[A-Z][A-Za-z0-9]+V1$")
    aggregate_id: str = Field(min_length=1)
    aggregate_version: int = Field(ge=1)
    occurred_at: datetime
    received_at: datetime = Field(default_factory=utc_now)
    producer: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    causation_id: str | None = None
    payload: dict[str, Any]
    content_hash: str | None = None

    @model_validator(mode="after")
    def _hash_matches(self) -> "EventEnvelopeV1":
        expected = hash_without(self, "content_hash")
        if self.content_hash is not None and self.content_hash != expected:
            raise ContractError("event envelope content_hash mismatch")
        object.__setattr__(self, "content_hash", expected)
        return self


class ControlStateV1(TimestampedModel):
    schema_version: Literal["control-state/v1"] = "control-state/v1"
    account_id: str
    version: int = Field(ge=0)
    mode: OperatingModeV1 = OperatingModeV1.DISARMED
    release_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    config_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    account_allowlist_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    reconciliation_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    reconciled_at: datetime
    content_hash: str | None = None

    @model_validator(mode="after")
    def _hash_matches(self) -> "ControlStateV1":
        expected = hash_without(self, "content_hash")
        if self.content_hash is not None and self.content_hash != expected:
            raise ContractError("control state content_hash mismatch")
        object.__setattr__(self, "content_hash", expected)
        return self


class DecisionJobV1(TimestampedModel):
    """Credential-free, sanitized input consumed by the durable decision worker."""

    schema_version: Literal["decision-job/v1"] = "decision-job/v1"
    job_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    as_of: datetime
    plugin_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    plugin_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    market: MarketSnapshotV1
    feature_vector: FeatureVectorV1
    account: AccountSnapshotV1
    positions: PositionSnapshotV1
    order_risk: OrderRiskSnapshotV1
    control_state: ControlStateV1
    prior_state: StrategyStateV1
    config: StrategyConfigV1
    entry_cutoff_at: datetime
    flatten_at: datetime
    job_hash: str | None = None

    @model_validator(mode="after")
    def _bindings_and_hash(self) -> "DecisionJobV1":
        if self.positions.account_id != self.account.account_id:
            raise ContractError("decision job position account mismatch")
        if self.order_risk.account_id != self.account.account_id:
            raise ContractError("decision job order-risk account mismatch")
        if self.control_state.account_id != self.account.account_id:
            raise ContractError("decision job control account mismatch")
        if self.feature_vector.source_market_hash != self.market.content_hash:
            raise ContractError("decision job feature source mismatch")
        if self.feature_vector.available_time > self.as_of:
            raise ContractError("decision job feature is not available")
        if self.entry_cutoff_at >= self.flatten_at:
            raise ContractError("decision job cutoff must precede flatten")
        if self.prior_state.plugin_id != self.plugin_id or self.prior_state.plugin_version != self.plugin_version:
            raise ContractError("decision job prior state identity mismatch")
        expected = hash_without(self, "job_hash")
        if self.job_hash is not None and self.job_hash != expected:
            raise ContractError("decision job hash mismatch")
        object.__setattr__(self, "job_hash", expected)
        return self


class ExecutionBundleV1(TimestampedModel):
    """Durable outbox payload containing immutable preflight inputs, never credentials."""

    schema_version: Literal["execution-bundle/v1"] = "execution-bundle/v1"
    bundle_id: str = Field(min_length=1, max_length=128)
    command: ExecuteApprovedPlanV1 | ExecuteReduceOnlyPlanV1
    risk_input: RiskInputV1 | None = None
    market: MarketSnapshotV1
    account: AccountSnapshotV1
    positions: PositionSnapshotV1
    order_risk: OrderRiskSnapshotV1
    control_state: ControlStateV1
    managed_position: ManagedPositionV1 | None = None
    content_hash: str | None = None

    @model_validator(mode="after")
    def _variant_and_hash(self) -> "ExecutionBundleV1":
        if isinstance(self.command, ExecuteApprovedPlanV1):
            if self.risk_input is None or self.managed_position is not None:
                raise ContractError("entry execution bundle requires risk input only")
        elif self.risk_input is not None or self.managed_position is None:
            raise ContractError("reduce-only execution bundle requires managed position only")
        if self.command.plan.account_id != self.account.account_id:
            raise ContractError("execution bundle account mismatch")
        if self.control_state.account_id != self.account.account_id:
            raise ContractError("execution bundle control account mismatch")
        if self.positions.account_id != self.account.account_id:
            raise ContractError("execution bundle position account mismatch")
        if self.order_risk.account_id != self.account.account_id:
            raise ContractError("execution bundle order-risk account mismatch")
        expected = hash_without(self, "content_hash")
        if self.content_hash is not None and self.content_hash != expected:
            raise ContractError("execution bundle content_hash mismatch")
        object.__setattr__(self, "content_hash", expected)
        return self


class ExecutionDeploymentV1(StrictModel):
    """Immutable credential-free deployment authority used by execution preflight."""

    schema_version: Literal["execution-deployment/v1"] = "execution-deployment/v1"
    expected_account_id: str = Field(min_length=1, max_length=128)
    paper_base_url: str = Field(min_length=1, max_length=256)
    risk_policy_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    template_catalog_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    strategy_registry_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    strategy_config_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    strategy_content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    account_allowlist_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    release_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    entry_cutoff_at: datetime
    flatten_at: datetime
    flat_deadline_at: datetime
    content_hash: str | None = None

    @model_validator(mode="after")
    def _hash_matches(self) -> "ExecutionDeploymentV1":
        entry_cutoff = _require_utc(self.entry_cutoff_at)
        flatten = _require_utc(self.flatten_at)
        deadline = _require_utc(self.flat_deadline_at)
        if not entry_cutoff < flatten < deadline:
            raise ContractError("deployment cutoff, flatten, and deadline must be ordered")
        expected = hash_without(self, "content_hash")
        if self.content_hash is not None and self.content_hash != expected:
            raise ContractError("execution deployment content_hash mismatch")
        object.__setattr__(self, "content_hash", expected)
        return self


class ControlCommandV1(TimestampedModel):
    schema_version: Literal["control-command/v1"] = "control-command/v1"
    command_id: str = Field(default_factory=lambda: str(uuid4()))
    nonce: UUID
    issued_at: datetime
    expires_at: datetime
    operator_id: str = Field(min_length=1)
    expected_mode: OperatingModeV1
    expected_version: int = Field(ge=0)
    target_mode: OperatingModeV1
    account_id: str
    account_allowlist_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    release_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    config_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    reconciliation_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,95}$")
    command_hash: str | None = None

    @model_validator(mode="after")
    def _hash_and_expiry(self) -> "ControlCommandV1":
        if self.expires_at <= self.issued_at:
            raise ContractError("control command expiry must be after issue time")
        expected = hash_without(self, "command_hash")
        if self.command_hash is not None and self.command_hash != expected:
            raise ContractError("control command hash mismatch")
        object.__setattr__(self, "command_hash", expected)
        return self


class ArmCommandV1(ControlCommandV1):
    """Typed private control command for the only two entry-capable modes."""

    schema_version: Literal["arm-command/v1"] = "arm-command/v1"
    target_mode: Literal[OperatingModeV1.PAPER_DEMO_ARMED, OperatingModeV1.PAPER_ARMED]


class HaltCommandV1(ControlCommandV1):
    """Typed private control command that can only halt or begin flattening."""

    schema_version: Literal["halt-command/v1"] = "halt-command/v1"
    target_mode: Literal[OperatingModeV1.FLATTENING, OperatingModeV1.HALTED]


class RunManifestV1(TimestampedModel):
    """Evidence record that binds a replay result to all immutable release inputs."""

    schema_version: Literal["run-manifest/v1"] = "run-manifest/v1"
    run_id: str = Field(min_length=1)
    status: Literal["NO_TRADE", "RISK_REJECTED", "APPROVED_AND_ENQUEUED", "EXECUTION_FAILED"]
    git_revision: str = Field(min_length=7, max_length=128)
    config_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    schema_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    plugin_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    market_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    thesis_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    content_hash: str | None = None

    @model_validator(mode="after")
    def _hash_matches(self) -> "RunManifestV1":
        expected = hash_without(self, "content_hash")
        if self.content_hash is not None and self.content_hash != expected:
            raise ContractError("run manifest content_hash mismatch")
        object.__setattr__(self, "content_hash", expected)
        return self
