"""Strict configuration for the isolated deterministic ETF live service."""

from __future__ import annotations

from datetime import time
from decimal import Decimal
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from packages.contracts.canonical import canonical_hash


class LiveConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["etf-live-config/v1"] = "etf-live-config/v1"
    mode: Literal["fixture", "observe", "live"] = "observe"
    account_id: str = Field(min_length=3, max_length=128)
    timezone: Literal["America/New_York"] = "America/New_York"
    trading_base_url: Literal["https://api.alpaca.markets"] = "https://api.alpaca.markets"
    data_base_url: Literal["https://data.alpaca.markets"] = "https://data.alpaca.markets"
    state_path: Path = Path("/var/lib/alpaca-etf-live/l11_tqqq_soxl/state.db")
    enabled_file: Path = Path("/etc/etf-live/enabled")
    target_investment: Decimal = Field(default=Decimal("0.99"), gt=0, le=Decimal("0.99"))
    initial_cash: Decimal = Field(default=Decimal("1000"), gt=0)
    minimum_order_notional: Decimal = Field(default=Decimal("5"), gt=0)
    quantity_decimals: int = Field(default=6, ge=0, le=9)
    buy_spread_limit: Decimal = Field(default=Decimal("0.005"), gt=0, le=Decimal("0.05"))
    buy_limit_adverse_bps: Decimal = Field(default=Decimal("25"), ge=0, le=Decimal("100"))
    quote_max_age_seconds: int = Field(default=2, ge=1, le=10)
    buy_cutoff: str = Field(default="09:35", pattern=r"^[0-2][0-9]:[0-5][0-9]$")
    exit_start: str = Field(default="09:30", pattern=r"^[0-2][0-9]:[0-5][0-9]$")
    polling_seconds: int = Field(default=15, ge=5, le=300)
    heartbeat_seconds: int = Field(default=60, ge=15, le=600)
    symbols: tuple[str, ...] = ("TQQQ", "SOXL")
    signal_symbols: tuple[str, ...] = ("QQQ", "SOXX")
    strategy_id: Literal["L11", "T08"] = "L11"
    strategy_config_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    secrets_root: Path = Path("/run/etf-live-secrets")
    telegram_enabled: bool = True
    gemini_observer_enabled: bool = False

    @model_validator(mode="before")
    @classmethod
    def _strategy_defaults(cls, data: object) -> object:
        if not isinstance(data, dict) or data.get("strategy_id", "L11") != "T08":
            return data
        payload = dict(data)
        payload.setdefault("symbols", ("TECL",))
        payload.setdefault("signal_symbols", ("XLK",))
        payload.setdefault("state_path", Path("/var/lib/alpaca-etf-live/t08_tecl/state.db"))
        payload.setdefault("initial_cash", Decimal("2000"))
        return payload

    @field_validator("symbols", "signal_symbols")
    @classmethod
    def _symbols(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().upper() for item in value)
        if len(set(normalized)) != len(normalized) or any(not x.isalpha() for x in normalized):
            raise ValueError("ETF_LIVE_SYMBOLS_INVALID")
        return normalized

    @model_validator(mode="after")
    def _validate(self) -> "LiveConfig":
        expected = {
            "L11": ({"TQQQ", "SOXL"}, {"QQQ", "SOXX"}),
            "T08": ({"TECL"}, {"XLK"}),
        }[self.strategy_id]
        if set(self.symbols) != expected[0] or set(self.signal_symbols) != expected[1]:
            raise ValueError(f"ETF_LIVE_{self.strategy_id}_UNIVERSE_INVALID")
        if self.mode == "live" and self.account_id in {"pending", "replace-me"}:
            raise ValueError("ETF_LIVE_ACCOUNT_ID_REQUIRED")
        if ZoneInfo(self.timezone).key != self.timezone:
            raise ValueError("ETF_LIVE_TIMEZONE_INVALID")
        for text in (self.buy_cutoff, self.exit_start):
            time.fromisoformat(text)
        return self

    @property
    def config_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))


def load_config(path: Path) -> LiveConfig:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError("ETF_LIVE_CONFIG_UNAVAILABLE") from exc
    return LiveConfig.model_validate(payload)
