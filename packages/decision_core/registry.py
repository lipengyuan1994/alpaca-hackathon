"""Central plug-in authority; strategy manifests never self-authorize."""

from __future__ import annotations

from dataclasses import dataclass

from packages.contracts.canonical import canonical_hash
from packages.contracts.models import IntentTupleV1, OperatingModeV1, StrategyMetadataV1


class RegistryError(ValueError):
    pass


@dataclass(frozen=True)
class RegistryEntry:
    plugin_id: str
    plugin_version: str
    entrypoint: str
    content_hash: str
    lifecycle: str
    allowed_underlyings: tuple[str, ...]
    allowed_intent_tuples: tuple[IntentTupleV1, ...]


class StrategyRegistry:
    def __init__(self, entries: tuple[RegistryEntry, ...]) -> None:
        self._entries = {(entry.plugin_id, entry.plugin_version): entry for entry in entries}
        self.registry_hash = canonical_hash(
            [
                {
                    "plugin_id": item.plugin_id,
                    "plugin_version": item.plugin_version,
                    "entrypoint": item.entrypoint,
                    "content_hash": item.content_hash,
                    "lifecycle": item.lifecycle,
                }
                for item in sorted(entries, key=lambda value: (value.plugin_id, value.plugin_version))
            ]
        )

    def validate(self, metadata: StrategyMetadataV1, content_hash: str, mode: OperatingModeV1) -> RegistryEntry:
        entry = self._entries.get((metadata.plugin_id, metadata.plugin_version))
        if entry is None:
            raise RegistryError("REGISTRY_PLUGIN_UNREGISTERED")
        if entry.content_hash != content_hash:
            raise RegistryError("REGISTRY_CONTENT_HASH_MISMATCH")
        allowed = entry.lifecycle == "paper_enabled" and mode == OperatingModeV1.PAPER_ARMED
        demo_allowed = entry.lifecycle == "paper_demo_only" and mode == OperatingModeV1.PAPER_DEMO_ARMED
        replay_allowed = mode in {OperatingModeV1.REPLAY, OperatingModeV1.SHADOW} and entry.lifecycle in {
            "paper_enabled",
            "paper_demo_only",
            "research_only",
        }
        if not (allowed or demo_allowed or replay_allowed):
            raise RegistryError("REGISTRY_LIFECYCLE_MODE_REJECTED")
        return entry

    def entry(self, plugin_id: str, plugin_version: str) -> RegistryEntry:
        try:
            return self._entries[(plugin_id, plugin_version)]
        except KeyError as exc:
            raise RegistryError("REGISTRY_PLUGIN_UNREGISTERED") from exc


def default_registry() -> StrategyRegistry:
    no_trade = RegistryEntry(
        plugin_id="always_no_trade",
        plugin_version="1.0.0",
        entrypoint="strategy_plugins.always_no_trade_v1.plugin:Plugin",
        content_hash="sha256:" + "0" * 64,
        lifecycle="paper_demo_only",
        allowed_underlyings=("SPY",),
        allowed_intent_tuples=(
            IntentTupleV1(
                template_id="CALL_DEBIT_SPREAD_V1",
                horizon_bucket="INTRADAY_15_60M",
                risk_tier="TINY",
                max_intent_ttl_seconds=300,
            ),
        ),
    )
    momentum = RegistryEntry(
        plugin_id="regime_momentum",
        plugin_version="1.0.0",
        entrypoint="strategy_plugins.regime_momentum_v1.plugin:Plugin",
        content_hash="sha256:" + "1" * 64,
        lifecycle="paper_demo_only",
        allowed_underlyings=("SPY", "QQQ"),
        allowed_intent_tuples=no_trade.allowed_intent_tuples,
    )
    return StrategyRegistry((no_trade, momentum))
