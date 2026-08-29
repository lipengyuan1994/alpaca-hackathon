"""Frozen fixture risk policy; deployment policy is versioned and hash-bound."""

from __future__ import annotations

from packages.contracts.canonical import canonical_hash
from packages.contracts.models import RiskPolicyV1


def default_policy() -> RiskPolicyV1:
    payload = {
        "max_per_trade_loss": "250",
        "max_daily_loss": "500",
        "max_total_reserved_loss": "250",
        "quote_ttl_seconds": 30,
        "approval_ttl_seconds": 60,
    }
    return RiskPolicyV1(policy_hash=canonical_hash(payload), **payload)
