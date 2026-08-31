"""Hash-bound contracts for the paper-wheel lifecycle."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from packages.contracts.canonical import hash_without
from packages.contracts.models import StrictModel, TimestampedModel


class WheelAction(StrEnum):
    SELL_CASH_SECURED_PUT = "SELL_CASH_SECURED_PUT"
    SELL_COVERED_CALL = "SELL_COVERED_CALL"
    BUY_TO_CLOSE = "BUY_TO_CLOSE"


class WheelOrderPlanV1(TimestampedModel):
    schema_version: Literal["wheel-order-plan/v1"] = "wheel-order-plan/v1"
    strategy_id: Literal["v13.5"]
    underlying: str = Field(pattern=r"^[A-Z]{1,6}$")
    action: WheelAction
    option_symbol: str = Field(pattern=r"^[A-Z0-9]{15,32}$")
    right: Literal["CALL", "PUT"]
    strike: Decimal = Field(gt=0)
    expiration: date
    quantity: Literal[1] = 1
    limit_price: Decimal = Field(gt=0)
    quote_bid: Decimal = Field(ge=0)
    quote_ask: Decimal = Field(gt=0)
    quote_time: datetime
    underlying_price: Decimal = Field(gt=0)
    trend_up: bool
    collateral_required: Decimal = Field(ge=0)
    maximum_loss: Decimal = Field(gt=0)
    client_order_id: str = Field(min_length=8, max_length=48, pattern=r"^[a-zA-Z0-9_-]+$")
    config_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    created_at: datetime
    plan_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _derived_hash_and_shape(self) -> "WheelOrderPlanV1":
        if self.quote_ask < self.quote_bid:
            raise ValueError("WHEEL_PLAN_QUOTE_CROSSED")
        expected_right = "PUT" if self.action == WheelAction.SELL_CASH_SECURED_PUT else "CALL"
        if self.action != WheelAction.BUY_TO_CLOSE and self.right != expected_right:
            raise ValueError("WHEEL_PLAN_ACTION_RIGHT_MISMATCH")
        computed = hash_without(self, "plan_hash")
        if self.plan_hash is not None and self.plan_hash != computed:
            raise ValueError("WHEEL_PLAN_HASH_MISMATCH")
        object.__setattr__(self, "plan_hash", computed)
        return self


class ManagedOptionV1(StrictModel):
    option_symbol: str
    underlying: str
    right: Literal["CALL", "PUT"]
    strike: Decimal
    expiration: date
    quantity: Literal[1] = 1
    entry_credit: Decimal = Field(gt=0)
    entry_client_order_id: str
    entry_order_id: str
    opened_at: datetime


class WheelRuntimeStateV1(TimestampedModel):
    schema_version: Literal["wheel-runtime-state/v1"] = "wheel-runtime-state/v1"
    config_hash: str
    sequence: int = Field(default=0, ge=0)
    status: Literal["READY", "RECONCILE_ONLY", "HALTED"] = "READY"
    last_run_at: datetime
    last_entry_week_by_symbol: dict[str, str] = Field(default_factory=dict)
    managed_options: dict[str, ManagedOptionV1] = Field(default_factory=dict)
    lifecycle_missing_since_by_symbol: dict[str, datetime] = Field(default_factory=dict)
    halt_reason: str | None = None
    state_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _derived_hash(self) -> "WheelRuntimeStateV1":
        computed = hash_without(self, "state_hash")
        if self.state_hash is not None and self.state_hash != computed:
            raise ValueError("WHEEL_STATE_HASH_MISMATCH")
        object.__setattr__(self, "state_hash", computed)
        return self


class WheelArmTokenV1(TimestampedModel):
    schema_version: Literal["wheel-arm-token/v1"] = "wheel-arm-token/v1"
    mode: Literal["paper"] = "paper"
    config_hash: str
    account_id_hash: str
    valid_from: datetime
    expires_at: datetime
    operator_reason: str = Field(min_length=8, max_length=256)
    token_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _derived_hash(self) -> "WheelArmTokenV1":
        if self.expires_at <= self.valid_from:
            raise ValueError("WHEEL_ARM_WINDOW_INVALID")
        computed = hash_without(self, "token_hash")
        if self.token_hash is not None and self.token_hash != computed:
            raise ValueError("WHEEL_ARM_HASH_MISMATCH")
        object.__setattr__(self, "token_hash", computed)
        return self


class WheelJournalEventV1(TimestampedModel):
    schema_version: Literal["wheel-journal-event/v1"] = "wheel-journal-event/v1"
    sequence: int = Field(ge=1)
    occurred_at: datetime
    event_type: str = Field(min_length=3, max_length=64)
    client_order_id: str | None = None
    plan_hash: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    previous_hash: str | None = None
    event_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _derived_hash(self) -> "WheelJournalEventV1":
        computed = hash_without(self, "event_hash")
        if self.event_hash is not None and self.event_hash != computed:
            raise ValueError("WHEEL_JOURNAL_HASH_MISMATCH")
        object.__setattr__(self, "event_hash", computed)
        return self
