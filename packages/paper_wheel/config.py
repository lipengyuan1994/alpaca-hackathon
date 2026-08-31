"""Strict YAML configuration for the V13.5 paper-wheel runtime."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import Field, field_validator, model_validator

from packages.contracts.canonical import canonical_hash
from packages.contracts.models import StrictModel


class WheelScheduleConfig(StrictModel):
    timezone: Literal["America/New_York"] = "America/New_York"
    entry_time: str = Field(default="10:00", pattern=r"^[0-2][0-9]:[0-5][0-9]$")
    entry_window_minutes: int = Field(default=5, ge=1, le=15)
    no_new_entries_after: str = Field(default="15:15", pattern=r"^[0-2][0-9]:[0-5][0-9]$")
    first_eligible_session_per_iso_week: bool = True
    poll_seconds: int = Field(default=60, ge=30, le=300)
    order_cancel_after_seconds: int = Field(default=180, ge=60, le=900)
    cancel_confirmation_grace_seconds: int = Field(default=120, ge=30, le=600)
    lifecycle_settlement_grace_minutes: int = Field(default=30, ge=5, le=240)

    @model_validator(mode="after")
    def _ordered(self) -> "WheelScheduleConfig":
        if self.entry_time >= self.no_new_entries_after:
            raise ValueError("WHEEL_SCHEDULE_ENTRY_CUTOFF_INVALID")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:  # pragma: no cover - host packaging defect
            raise ValueError("WHEEL_SCHEDULE_TIMEZONE_UNAVAILABLE") from exc
        return self


class WheelStrategyConfig(StrictModel):
    strategy_id: Literal["v13.5"] = "v13.5"
    symbols: tuple[str, ...] = ("QQQ",)
    minimum_dte: int = Field(default=7, ge=1, le=30)
    maximum_dte: int = Field(default=14, ge=1, le=45)
    trend_sessions: int = Field(default=50, ge=20, le=252)
    uptrend_put_otm_fraction: Decimal = Field(default=Decimal("0.01"), gt=0, lt=Decimal("0.20"))
    uptrend_call_otm_fraction: Decimal = Field(default=Decimal("0.03"), gt=0, lt=Decimal("0.20"))
    downtrend_put_otm_fraction: Decimal = Field(default=Decimal("0.03"), gt=0, lt=Decimal("0.20"))
    downtrend_call_otm_fraction: Decimal = Field(default=Decimal("0.01"), gt=0, lt=Decimal("0.20"))
    take_profit_fraction: Decimal = Field(default=Decimal("0.15"), gt=0, lt=Decimal("1"))

    @field_validator("symbols")
    @classmethod
    def _symbols(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().upper() for item in value)
        if not normalized or len(set(normalized)) != len(normalized):
            raise ValueError("WHEEL_SYMBOLS_INVALID")
        if any(not item.isalpha() or len(item) > 6 for item in normalized):
            raise ValueError("WHEEL_SYMBOL_INVALID")
        return normalized

    @model_validator(mode="after")
    def _dte_ordered(self) -> "WheelStrategyConfig":
        if self.minimum_dte > self.maximum_dte:
            raise ValueError("WHEEL_DTE_RANGE_INVALID")
        return self


class WheelRiskConfig(StrictModel):
    max_contracts_per_symbol: Literal[1] = 1
    max_assignment_notional_usd: Decimal = Field(default=Decimal("60000"), gt=0)
    minimum_unreserved_cash_usd: Decimal = Field(default=Decimal("25000"), ge=0)
    maximum_daily_drawdown_fraction: Decimal = Field(default=Decimal("0.02"), gt=0, le=Decimal("0.10"))
    maximum_quote_age_seconds: int = Field(default=15, ge=1, le=60)
    maximum_clock_skew_seconds: int = Field(default=2, ge=0, le=5)
    maximum_relative_spread: Decimal = Field(default=Decimal("0.25"), gt=0, le=Decimal("1"))
    minimum_option_bid: Decimal = Field(default=Decimal("0.05"), gt=0)
    allowed_symbols: tuple[str, ...] = ("QQQ",)
    allow_unmanaged_positions: bool = False
    allow_unmanaged_open_orders: bool = False

    @field_validator("allowed_symbols")
    @classmethod
    def _allowed_symbols(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().upper() for item in value)
        if not normalized or any(not item.isalpha() for item in normalized):
            raise ValueError("WHEEL_RISK_SYMBOLS_INVALID")
        return normalized


class WheelActivationConfig(StrictModel):
    start_date: date
    end_date: date
    require_operator_arm: bool = True

    @model_validator(mode="after")
    def _ordered(self) -> "WheelActivationConfig":
        if self.end_date < self.start_date:
            raise ValueError("WHEEL_ACTIVATION_RANGE_INVALID")
        if (self.end_date - self.start_date).days > 10:
            raise ValueError("WHEEL_ACTIVATION_WINDOW_TOO_LONG")
        return self


class WheelRuntimeConfig(StrictModel):
    mode: Literal["paper"] = "paper"
    paper_base_url: Literal["https://paper-api.alpaca.markets"] = "https://paper-api.alpaca.markets"
    submission_enabled: bool = False
    runtime_root: Path = Path("artifacts/paper_wheel/v13_5_qqq")
    client_order_prefix: str = Field(default="rs-v135", pattern=r"^[a-z0-9-]{3,12}$")

    @field_validator("runtime_root")
    @classmethod
    def _runtime_root(cls, value: Path) -> Path:
        if value.is_absolute() or ".." in value.parts:
            raise ValueError("WHEEL_RUNTIME_ROOT_MUST_BE_PROJECT_RELATIVE")
        return value


class WheelPaperConfig(StrictModel):
    schema_version: Literal["paper-wheel-config/v1"] = "paper-wheel-config/v1"
    runtime: WheelRuntimeConfig
    strategy: WheelStrategyConfig
    schedule: WheelScheduleConfig
    risk: WheelRiskConfig
    activation: WheelActivationConfig

    @model_validator(mode="after")
    def _authority_consistent(self) -> "WheelPaperConfig":
        if set(self.strategy.symbols) - set(self.risk.allowed_symbols):
            raise ValueError("WHEEL_STRATEGY_SYMBOL_NOT_RISK_ALLOWED")
        return self


class LoadedWheelConfig(StrictModel):
    path: Path
    config: WheelPaperConfig
    config_hash: str


def load_config(path: Path) -> LoadedWheelConfig:
    """Load one strict YAML file and bind its canonical semantic hash."""
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError("WHEEL_CONFIG_UNAVAILABLE") from exc
    except yaml.YAMLError as exc:
        raise ValueError("WHEEL_CONFIG_YAML_INVALID") from exc
    config = WheelPaperConfig.model_validate(payload)
    return LoadedWheelConfig(
        path=path.resolve(),
        config=config,
        config_hash=canonical_hash(config.model_dump(mode="json")),
    )
