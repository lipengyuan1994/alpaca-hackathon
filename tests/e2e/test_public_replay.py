from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.decision_worker.main import run_refusal_fixture


def test_public_replay_has_no_credentials_and_renders_no_trade_tape() -> None:
    result = run_refusal_fixture()
    client = TestClient(create_app(result.ledger))
    response = client.get(f"/v1/replay/{result.run_id}")
    assert response.status_code == 200
    assert response.json()["events"][-1]["event_type"] == "NoTradeRecordedV1"
    assert "secret" not in response.text.lower()
