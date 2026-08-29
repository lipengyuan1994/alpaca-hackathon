"""Versioned, hash-addressed feature contracts shared by research and runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from packages.contracts.canonical import canonical_hash


class FeatureContractError(ValueError):
    """A feature definition or vector did not match its pinned contract."""


class FeatureDefinitionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(pattern=r"^[A-Z]{1,8}__[a-z][a-z0-9_]{1,63}$")
    dtype: Literal["decimal"]
    unit: str = Field(min_length=1, max_length=64)
    source: str = Field(min_length=1, max_length=128)
    feed: str = Field(min_length=1, max_length=64)
    timeframe: str = Field(pattern=r"^[1-9][0-9]*[smhd]$")
    lookback_bars: int = Field(ge=1, le=100_000)
    formula_id: str = Field(min_length=1, max_length=128)
    availability_delay_seconds: int = Field(ge=0, le=86_400)
    maximum_age_seconds: int = Field(ge=1, le=86_400)
    missing_behavior: Literal["NO_TRADE"]
    allowed_quality_flags: tuple[str, ...] = ()
    worked_example_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class FeatureContractV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["feature-contract/v1"]
    contract_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,95}$")
    feature_schema_version: Literal["feature-vector/v1"]
    features: tuple[FeatureDefinitionV1, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _ordered_unique_keys(self) -> "FeatureContractV1":
        keys = tuple(item.key for item in self.features)
        if tuple(dict.fromkeys(keys)) != keys:
            raise ValueError("feature keys must be unique and ordered")
        return self

    @property
    def feature_keys(self) -> tuple[str, ...]:
        return tuple(item.key for item in self.features)

    @property
    def content_hash(self) -> str:
        return canonical_hash(self)


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FEATURE_CONTRACT_PATH = _REPOSITORY_ROOT / "configs" / "feature_contract_fixture_v1.yaml"


def load_feature_contract(
    path: Path = DEFAULT_FEATURE_CONTRACT_PATH,
) -> FeatureContractV1:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return FeatureContractV1.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError, ValueError) as exc:
        raise FeatureContractError("FEATURE_CONTRACT_INVALID") from exc
