"""Pure normalized feature construction; strategies never see raw provider responses."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from packages.contracts.models import FeatureVectorV1, MarketSnapshotV1

from .feature_registry import FeatureContractError, FeatureContractV1


def compute_feature_vector(
    snapshot: MarketSnapshotV1,
    *,
    feature_id: str,
    calculated_at: datetime,
    values: dict[str, Decimal],
    contract: FeatureContractV1,
) -> FeatureVectorV1:
    if tuple(values) != contract.feature_keys:
        raise FeatureContractError("FEATURE_VECTOR_KEYS_MISMATCH")
    if any(not value.is_finite() for value in values.values()):
        raise FeatureContractError("FEATURE_VECTOR_NONFINITE")
    return FeatureVectorV1(
        feature_id=feature_id,
        feature_contract_hash=contract.content_hash,
        calculated_at=calculated_at,
        available_time=calculated_at
        + timedelta(
            seconds=max(item.availability_delay_seconds for item in contract.features)
        ),
        values=values,
        source_market_hash=snapshot.content_hash,
    )
