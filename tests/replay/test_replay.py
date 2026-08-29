from __future__ import annotations

from apps.decision_worker.main import run_refusal_fixture


def test_no_trade_replay_is_stable_and_visible() -> None:
    first = run_refusal_fixture()
    second = run_refusal_fixture()
    assert first.status == "NO_TRADE"
    assert first.details == second.details
    assert first.tape == second.tape
    assert [event["event_type"] for event in first.tape] == ["MarketSnapshotRecordedV1", "NoTradeRecordedV1"]
