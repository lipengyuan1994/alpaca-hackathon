"""Pinned, provider-neutral advisory-model configuration.

The catalog contains endpoints and bounded runtime settings only.  API keys
are always supplied separately through the file-secret boundary.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, model_validator

from packages.contracts.canonical import canonical_hash
from packages.contracts.models import StrictModel


class AdvisoryProviderV1(StrEnum):
    GEMINI = "GEMINI"
    OPENAI_COMPATIBLE = "OPENAI_COMPATIBLE"


class AdvisoryRequestProtocolV1(StrEnum):
    """Pinned provider wire protocols; model names never select an endpoint implicitly."""

    GEMINI_GENERATE_CONTENT = "GEMINI_GENERATE_CONTENT"
    GEMINI_INTERACTIONS = "GEMINI_INTERACTIONS"
    OPENAI_CHAT_COMPLETIONS = "OPENAI_CHAT_COMPLETIONS"


_ALLOWED_BASE_URLS = {
    AdvisoryProviderV1.GEMINI: {"https://generativelanguage.googleapis.com"},
    AdvisoryProviderV1.OPENAI_COMPATIBLE: {"https://api.deepseek.com"},
}

_ALLOWED_PROTOCOLS = {
    AdvisoryProviderV1.GEMINI: {
        AdvisoryRequestProtocolV1.GEMINI_GENERATE_CONTENT,
        AdvisoryRequestProtocolV1.GEMINI_INTERACTIONS,
    },
    AdvisoryProviderV1.OPENAI_COMPATIBLE: {
        AdvisoryRequestProtocolV1.OPENAI_CHAT_COMPLETIONS,
    },
}


class AdvisoryModelProfileV1(StrictModel):
    model_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    provider: AdvisoryProviderV1
    request_protocol: AdvisoryRequestProtocolV1
    model_name: str = Field(pattern=r"^[A-Za-z0-9._-]{2,127}$")
    api_base_url: str = Field(pattern=r"^https://[A-Za-z0-9./-]+$")
    enabled: bool = True
    prompt_version: str = Field(pattern=r"^[a-z][a-z0-9._-]{2,63}$")
    max_output_tokens: int = Field(ge=64, le=600)
    timeout_seconds: int = Field(ge=1, le=30)
    thesis_ttl_seconds: int = Field(ge=1, le=300)

    @model_validator(mode="after")
    def _provider_endpoint_is_allowlisted(self) -> "AdvisoryModelProfileV1":
        if self.api_base_url not in _ALLOWED_BASE_URLS[self.provider]:
            raise ValueError("advisory provider endpoint is not allowlisted")
        if self.request_protocol not in _ALLOWED_PROTOCOLS[self.provider]:
            raise ValueError("advisory request protocol is not allowed for provider")
        return self

    @property
    def profile_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))


class AdvisoryModelCatalogV1(StrictModel):
    schema_version: Literal["advisory-model-catalog/v1"] = "advisory-model-catalog/v1"
    default_model_id: str
    models: dict[str, AdvisoryModelProfileV1]

    @model_validator(mode="after")
    def _model_ids_and_default_are_valid(self) -> "AdvisoryModelCatalogV1":
        if not self.models:
            raise ValueError("advisory model catalog is empty")
        if any(model_id != profile.model_id for model_id, profile in self.models.items()):
            raise ValueError("advisory model catalog key does not match model_id")
        if self.default_model_id not in self.models:
            raise ValueError("advisory model default is not in catalog")
        if not self.models[self.default_model_id].enabled:
            raise ValueError("advisory model default is disabled")
        return self

    def select(self, model_id: str | None = None) -> AdvisoryModelProfileV1:
        selected = model_id or self.default_model_id
        profile = self.models.get(selected)
        if profile is None:
            raise ValueError("ADVISORY_MODEL_ID_NOT_ALLOWLISTED")
        if not profile.enabled:
            raise ValueError("ADVISORY_MODEL_DISABLED")
        return profile


def load_advisory_model_catalog(path: Path) -> AdvisoryModelCatalogV1:
    """Load the release-owned YAML catalog without accepting arbitrary endpoints."""

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError("ADVISORY_MODEL_CATALOG_UNAVAILABLE") from exc
    if not isinstance(raw, dict):
        raise RuntimeError("ADVISORY_MODEL_CATALOG_INVALID")
    return AdvisoryModelCatalogV1.model_validate(raw)
