"""Deterministic identifiers used for idempotent broker effects."""

from __future__ import annotations

from packages.contracts.canonical import canonical_hash


def deterministic_client_order_id(intent_id: str, plan_material: object) -> str:
    """Make a broker-safe, deterministic identifier from immutable plan material."""
    digest = canonical_hash({"intent_id": intent_id, "plan": plan_material}).removeprefix("sha256:")
    return f"paper-{digest[:48]}"
