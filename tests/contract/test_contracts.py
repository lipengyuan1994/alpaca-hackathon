from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from apps.decision_worker.main import fixture_inputs
from packages.contracts.models import MarketSnapshotV1


def test_contracts_reject_unknown_fields() -> None:
    market, *_ = fixture_inputs()
    payload = market.model_dump(mode="json")
    payload["broker_secret"] = "never-accepted"
    with pytest.raises(ValidationError):
        MarketSnapshotV1.model_validate(payload)


def test_content_hash_is_stable_and_tampering_is_rejected() -> None:
    market, *_ = fixture_inputs()
    assert market.content_hash == MarketSnapshotV1.model_validate(market.model_dump(mode="json")).content_hash
    payload = market.model_dump(mode="json")
    payload["as_of"] = datetime(2026, 8, 31, 14, 16, tzinfo=UTC).isoformat()
    with pytest.raises(ValidationError, match="content_hash mismatch"):
        MarketSnapshotV1.model_validate(payload)
