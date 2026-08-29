"""Schema-validated central plug-in authority.

The registry is loaded without importing strategy code. Each configured source
hash is recomputed from the registered package before an entry can be returned,
so a plug-in can neither self-register nor self-assert its executable identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from packages.contracts.canonical import canonical_hash
from packages.contracts.models import (
    DataRequirementsV1,
    IntentTupleV1,
    OperatingModeV1,
    PositionPolicyIdV1,
    StrategyMetadataV1,
)
from packages.plugin_integrity import PluginIntegrityError
from packages.plugin_integrity import (
    calculate_plugin_content_hash as _calculate_plugin_content_hash,
)


class RegistryError(ValueError):
    """Stable fail-closed error raised by registry loading or authorization."""


_HASH_PATTERN = r"^sha256:[0-9a-f]{64}$"
_PLUGIN_ID_PATTERN = r"^[a-z][a-z0-9_]{1,63}$"
_PLUGIN_VERSION_PATTERN = r"^\d+\.\d+\.\d+$"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_REGISTRY_PATH = _REPOSITORY_ROOT / "configs" / "strategy_registry.yaml"


class _RegistryEntryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plugin_id: str = Field(pattern=_PLUGIN_ID_PATTERN)
    plugin_version: str = Field(pattern=_PLUGIN_VERSION_PATTERN)
    entrypoint: str = Field(pattern=r"^strategy_plugins\.[a-zA-Z0-9_.]+:[A-Za-z_][A-Za-z0-9_]*$")
    content_hash: str = Field(pattern=_HASH_PATTERN)
    lifecycle: Literal["research_only", "paper_demo_only", "paper_enabled", "retired"]
    authority: Literal["central-registry-only"]
    owner: str = Field(min_length=1, max_length=128)
    reviewer: str = Field(min_length=1, max_length=128)
    economic_hypothesis_id: str = Field(min_length=1, max_length=128)
    config_hash: str = Field(pattern=_HASH_PATTERN)
    promotion_evidence_hash: str = Field(pattern=_HASH_PATTERN)
    position_policy_ref: PositionPolicyIdV1
    data_requirements: DataRequirementsV1
    allowed_underlyings: tuple[str, ...] = Field(min_length=1)
    allowed_intent_tuples: tuple[IntentTupleV1, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _coherent_authority(self) -> "_RegistryEntryDocument":
        if tuple(dict.fromkeys(self.allowed_underlyings)) != self.allowed_underlyings:
            raise ValueError("allowed_underlyings must be unique and ordered")
        if any(
            not symbol.isalpha() or not symbol.isupper() or len(symbol) > 8
            for symbol in self.allowed_underlyings
        ):
            raise ValueError("allowed_underlyings must be uppercase symbols")
        if self.data_requirements.underlyings != self.allowed_underlyings:
            raise ValueError("data_requirements underlyings must equal allowed_underlyings")
        tuple_keys = [
            (item.template_id, item.horizon_bucket, item.risk_tier, item.max_intent_ttl_seconds)
            for item in self.allowed_intent_tuples
        ]
        if len(tuple_keys) != len(set(tuple_keys)):
            raise ValueError("allowed_intent_tuples must be unique")
        return self


class _RegistryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["strategy-registry/v1"]
    entries: tuple[_RegistryEntryDocument, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_entries(self) -> "_RegistryDocument":
        keys = [(entry.plugin_id, entry.plugin_version) for entry in self.entries]
        if len(keys) != len(set(keys)):
            raise ValueError("registry entries must have unique plugin identity/version")
        return self


@dataclass(frozen=True)
class RegistryEntry:
    plugin_id: str
    plugin_version: str
    entrypoint: str
    content_hash: str
    lifecycle: str
    authority: str
    owner: str
    reviewer: str
    economic_hypothesis_id: str
    config_hash: str
    promotion_evidence_hash: str
    position_policy_ref: PositionPolicyIdV1
    data_requirements: DataRequirementsV1
    allowed_underlyings: tuple[str, ...]
    allowed_intent_tuples: tuple[IntentTupleV1, ...]

    @property
    def expected_metadata(self) -> StrategyMetadataV1:
        return StrategyMetadataV1(
            plugin_id=self.plugin_id,
            plugin_version=self.plugin_version,
            owner=self.owner,
            economic_hypothesis_id=self.economic_hypothesis_id,
        )

    def authority_payload(self) -> dict[str, object]:
        """Return every field whose change alters registry authority."""
        return {
            "plugin_id": self.plugin_id,
            "plugin_version": self.plugin_version,
            "entrypoint": self.entrypoint,
            "content_hash": self.content_hash,
            "lifecycle": self.lifecycle,
            "authority": self.authority,
            "owner": self.owner,
            "reviewer": self.reviewer,
            "economic_hypothesis_id": self.economic_hypothesis_id,
            "config_hash": self.config_hash,
            "promotion_evidence_hash": self.promotion_evidence_hash,
            "position_policy_ref": self.position_policy_ref,
            "data_requirements": self.data_requirements,
            "allowed_underlyings": self.allowed_underlyings,
            "allowed_intent_tuples": self.allowed_intent_tuples,
        }


def calculate_plugin_content_hash(
    entrypoint: str,
    *,
    repository_root: Path = _REPOSITORY_ROOT,
) -> str:
    """Preserve the registry API while sharing its digest with the runner."""
    try:
        return _calculate_plugin_content_hash(
            entrypoint,
            repository_root=repository_root,
        )
    except PluginIntegrityError as exc:
        raise RegistryError(str(exc)) from exc


def _entry_from_document(document: _RegistryEntryDocument) -> RegistryEntry:
    scalar_fields = document.model_dump(
        exclude={"data_requirements", "allowed_intent_tuples"}
    )
    return RegistryEntry(
        **scalar_fields,
        data_requirements=document.data_requirements,
        allowed_intent_tuples=document.allowed_intent_tuples,
    )


class StrategyRegistry:
    def __init__(
        self,
        entries: tuple[RegistryEntry, ...],
        *,
        version: str = "strategy-registry/v1",
    ) -> None:
        keys = [(entry.plugin_id, entry.plugin_version) for entry in entries]
        if len(keys) != len(set(keys)):
            raise RegistryError("REGISTRY_DUPLICATE_PLUGIN")
        self._entries = {(entry.plugin_id, entry.plugin_version): entry for entry in entries}
        self.registry_hash = canonical_hash(
            {
                "version": version,
                "entries": [
                    item.authority_payload()
                    for item in sorted(entries, key=lambda value: (value.plugin_id, value.plugin_version))
                ],
            }
        )

    def entry(self, plugin_id: str, plugin_version: str) -> RegistryEntry:
        try:
            return self._entries[(plugin_id, plugin_version)]
        except KeyError as exc:
            raise RegistryError("REGISTRY_PLUGIN_UNREGISTERED") from exc

    def authorize(
        self,
        plugin_id: str,
        plugin_version: str,
        *,
        config_hash: str,
        mode: OperatingModeV1,
    ) -> RegistryEntry:
        """Authorize a pinned source/config/lifecycle before child-process import."""
        entry = self.entry(plugin_id, plugin_version)
        if entry.config_hash != config_hash:
            raise RegistryError("REGISTRY_CONFIG_HASH_MISMATCH")
        self._validate_lifecycle(entry, mode)
        return entry

    def validate(
        self,
        metadata: StrategyMetadataV1,
        content_hash: str,
        mode: OperatingModeV1,
    ) -> RegistryEntry:
        """Validate imported metadata for callers outside the standard runner."""
        entry = self.entry(metadata.plugin_id, metadata.plugin_version)
        if metadata != entry.expected_metadata:
            raise RegistryError("REGISTRY_METADATA_MISMATCH")
        if entry.content_hash != content_hash:
            raise RegistryError("REGISTRY_CONTENT_HASH_MISMATCH")
        self._validate_lifecycle(entry, mode)
        return entry

    @staticmethod
    def _validate_lifecycle(entry: RegistryEntry, mode: OperatingModeV1) -> None:
        allowed = entry.lifecycle == "paper_enabled" and mode == OperatingModeV1.PAPER_ARMED
        demo_allowed = (
            entry.lifecycle == "paper_demo_only" and mode == OperatingModeV1.PAPER_DEMO_ARMED
        )
        replay_allowed = (
            mode in {OperatingModeV1.REPLAY, OperatingModeV1.SHADOW}
            and entry.lifecycle in {"paper_enabled", "paper_demo_only", "research_only"}
        )
        if not (allowed or demo_allowed or replay_allowed):
            raise RegistryError("REGISTRY_LIFECYCLE_MODE_REJECTED")


def load_registry(
    path: Path = _DEFAULT_REGISTRY_PATH,
    *,
    repository_root: Path = _REPOSITORY_ROOT,
) -> StrategyRegistry:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RegistryError("REGISTRY_FILE_UNAVAILABLE") from exc
    except yaml.YAMLError as exc:
        raise RegistryError("REGISTRY_YAML_INVALID") from exc
    try:
        document = _RegistryDocument.model_validate(raw)
    except ValidationError as exc:
        raise RegistryError("REGISTRY_SCHEMA_INVALID") from exc

    entries: list[RegistryEntry] = []
    for configured in document.entries:
        computed_hash = calculate_plugin_content_hash(
            configured.entrypoint,
            repository_root=repository_root,
        )
        if computed_hash != configured.content_hash:
            raise RegistryError(
                f"REGISTRY_CONTENT_HASH_MISMATCH:{configured.plugin_id}@{configured.plugin_version}"
            )
        entries.append(_entry_from_document(configured))
    return StrategyRegistry(tuple(entries), version=document.version)


def default_registry() -> StrategyRegistry:
    return load_registry()
