"""Release-owned configuration for one pre-market economic context capture."""

from __future__ import annotations

from datetime import time
from pathlib import Path

import yaml
from pydantic import Field, model_validator

from packages.contracts.canonical import hash_without
from packages.contracts.models import StrictModel


class EconomicContextConfigV1(StrictModel):
    schema_version: str = "economic-context-config/v1"
    collection_window_start_et: time
    collection_window_end_et: time
    lookback_calendar_days: int = Field(ge=3, le=31)
    news_lookback_hours: int = Field(ge=1, le=168)
    maximum_news_headlines: int = Field(ge=0, le=16)
    macro_proxy_symbols: tuple[str, ...] = Field(min_length=1, max_length=16)
    micro_context_symbols: tuple[str, ...] = Field(min_length=1, max_length=32)
    config_hash: str | None = None

    @model_validator(mode="after")
    def _safe_pre_market_window_and_hash(self) -> "EconomicContextConfigV1":
        if self.collection_window_start_et >= self.collection_window_end_et:
            raise ValueError("economic collection window is invalid")
        if self.collection_window_end_et >= time(9, 30):
            raise ValueError("economic collection must finish before the regular session")
        symbols = self.macro_proxy_symbols + self.micro_context_symbols
        if any(not symbol.isalpha() or symbol != symbol.upper() or len(symbol) > 8 for symbol in symbols):
            raise ValueError("economic context symbol is invalid")
        if len(set(self.macro_proxy_symbols)) != len(self.macro_proxy_symbols):
            raise ValueError("economic macro proxy symbols must be unique")
        if len(set(self.micro_context_symbols)) != len(self.micro_context_symbols):
            raise ValueError("economic micro context symbols must be unique")
        expected = hash_without(self, "config_hash")
        if self.config_hash is not None and self.config_hash != expected:
            raise ValueError("economic context config hash mismatch")
        object.__setattr__(self, "config_hash", expected)
        return self

    @property
    def all_symbols(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self.macro_proxy_symbols + self.micro_context_symbols))


def load_economic_context_config(path: Path) -> EconomicContextConfigV1:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError("ECONOMIC_CONTEXT_CONFIG_UNAVAILABLE") from exc
    if not isinstance(raw, dict):
        raise RuntimeError("ECONOMIC_CONTEXT_CONFIG_INVALID")
    return EconomicContextConfigV1.model_validate(raw)
