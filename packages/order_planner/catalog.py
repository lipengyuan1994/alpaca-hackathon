"""Single schema-validated template catalog shared by research and runtime."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from packages.contracts.canonical import canonical_hash


class CatalogError(ValueError):
    """Stable fail-closed catalog loading error."""


class RiskTierDefinitionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_loss_dollars: Decimal = Field(gt=0, le=10_000)
    max_equity_fraction: Decimal = Field(gt=0, le=Decimal("0.05"))


class TemplateDefinitionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
    direction: Literal["BULLISH", "BEARISH"]
    right: Literal["CALL", "PUT"]
    atomic_option_only: Literal[True]
    max_legs: Literal[2]
    min_dte: int = Field(ge=1, le=60)
    max_dte: int = Field(ge=1, le=60)
    target_short_offset_fraction: Decimal = Field(gt=0, le=Decimal("0.10"))
    standard_deliverable_only: Literal[True]

    @model_validator(mode="after")
    def _coherent(self) -> "TemplateDefinitionV1":
        if self.min_dte > self.max_dte:
            raise ValueError("min_dte must not exceed max_dte")
        if (self.right == "CALL") != (self.direction == "BULLISH"):
            raise ValueError("template right/direction mismatch")
        return self


class TemplateCatalogV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["template-catalog/v1"]
    selector_version: Literal["nearest-spot-one-percent-width/v1"]
    quote_ttl_seconds: int = Field(ge=1, le=300)
    risk_tiers: dict[Literal["TINY", "STANDARD"], RiskTierDefinitionV1]
    templates: dict[
        Literal["CALL_DEBIT_SPREAD_V1", "PUT_DEBIT_SPREAD_V1"],
        TemplateDefinitionV1,
    ]

    @model_validator(mode="after")
    def _complete(self) -> "TemplateCatalogV1":
        if set(self.risk_tiers) != {"TINY", "STANDARD"}:
            raise ValueError("catalog must define both risk tiers")
        if set(self.templates) != {"CALL_DEBIT_SPREAD_V1", "PUT_DEBIT_SPREAD_V1"}:
            raise ValueError("catalog must define both debit verticals")
        return self

    @property
    def content_hash(self) -> str:
        return canonical_hash(self)


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG_PATH = _REPOSITORY_ROOT / "configs" / "template_catalog.yaml"


def load_template_catalog(path: Path = DEFAULT_CATALOG_PATH) -> TemplateCatalogV1:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return TemplateCatalogV1.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError, ValueError) as exc:
        raise CatalogError("TEMPLATE_CATALOG_INVALID") from exc
