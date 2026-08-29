"""Pure normalized feature construction; strategies never see raw provider responses."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from packages.contracts.models import FeatureVectorV1, MarketSnapshotV1


def compute_feature_vector(
    snapshot: MarketSnapshotV1,
    *,
    feature_id: str,
    calculated_at: datetime,
    values: dict[str, Decimal],
) -> FeatureVectorV1:
    return FeatureVectorV1(
        feature_id=feature_id,
        calculated_at=calculated_at,
        available_time=calculated_at,
        values=values,
        source_market_hash=snapshot.content_hash,
    )
