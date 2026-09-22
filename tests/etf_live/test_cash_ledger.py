from __future__ import annotations

from pathlib import Path

import pytest

from packages.etf_live.state import LiveState


def test_cash_ledger_tracks_funding_reservations_and_unsettled_sales(tmp_path: Path) -> None:
    state = LiveState(tmp_path / "cash.db")
    assert state.record_cash_event(
        event_id="funding-1",
        category="cleared_funding",
        activity_type="CSD",
        amount="2000",
        effective_date="2026-09-01",
        spendable_date="2026-09-01",
        payload={"source": "broker_activity"},
    )
    state.initialize_cash_ledger(evidence_id="funding-1", account_id_hash="sha256:account", as_of="2026-09-01")
    state.record_cash_event(
        event_id="buy-1",
        category="buy_fill",
        activity_type="FILL",
        amount="-500",
        effective_date="2026-09-02",
        spendable_date="2026-09-02",
        payload={"quantity": "5", "price": "100"},
    )
    state.record_cash_event(
        event_id="sell-1",
        category="sale_proceeds",
        activity_type="FILL",
        amount="300",
        effective_date="2026-09-03",
        spendable_date="2026-09-04",
        payload={"quantity": "3", "price": "100", "settlement_date": "2026-09-04"},
    )

    trade_date = state.cash_ledger_snapshot(as_of="2026-09-03", reserved_cash="100")
    settlement_date = state.cash_ledger_snapshot(as_of="2026-09-04", reserved_cash="100")

    assert trade_date["cleared_funding"] == "2000"
    assert trade_date["settled_cash"] == "1500"
    assert trade_date["reserved_cash"] == "100"
    assert trade_date["spendable_cash"] == "1400"
    assert trade_date["unsettled_sale_proceeds"] == "300"
    assert trade_date["cash_equity"] == "1800"
    assert settlement_date["settled_cash"] == "1800"
    assert settlement_date["unsettled_sale_proceeds"] == "0"
    assert settlement_date["spendable_cash"] == "1700"


def test_distribution_entitlement_is_equity_but_not_cash_until_confirmed_payment(tmp_path: Path) -> None:
    state = LiveState(tmp_path / "cash.db")
    state.record_cash_event(
        event_id="funding-1",
        category="cleared_funding",
        activity_type="CSD",
        amount="2000",
        effective_date="2026-09-01",
        spendable_date="2026-09-01",
        payload={},
    )
    state.initialize_cash_ledger(evidence_id="funding-1", account_id_hash="sha256:account", as_of="2026-09-01")
    state.record_distribution_entitlement(
        distribution_id="tecl-distribution-1",
        symbol="TECL",
        ex_date="2026-09-02",
        payable_date="2026-09-05",
        amount="20",
        payload={"shares_entitled": "2", "per_share": "10"},
    )

    ex_date = state.cash_ledger_snapshot(as_of="2026-09-02")
    unpaid = state.cash_ledger_snapshot(as_of="2026-09-05")
    assert ex_date["settled_cash"] == "2000"
    assert ex_date["distribution_receivables"] == "20"
    assert ex_date["cash_equity"] == "2020"
    assert "DISTRIBUTION_RECONCILIATION_REQUIRED" in unpaid["blockers"]

    state.record_cash_event(
        event_id="div-payment-activity-1",
        category="distribution_payment",
        activity_type="DIV",
        amount="20",
        effective_date="2026-09-05",
        spendable_date="2026-09-05",
        distribution_id="tecl-distribution-1",
        payload={"broker_activity_id": "div-payment-activity-1"},
    )
    paid = state.cash_ledger_snapshot(as_of="2026-09-05")
    assert paid["settled_cash"] == "2020"
    assert paid["distribution_receivables"] == "0"
    assert paid["broker_confirmed_distribution_payments"] == "20"
    assert "DISTRIBUTION_RECONCILIATION_REQUIRED" not in paid["blockers"]


def test_cash_event_dedup_is_idempotent_and_changed_activity_fails_closed(tmp_path: Path) -> None:
    state = LiveState(tmp_path / "cash.db")
    event = {
        "event_id": "activity-1",
        "category": "fee",
        "activity_type": "FEE",
        "amount": "-1.25",
        "effective_date": "2026-09-01",
        "spendable_date": "2026-09-01",
        "payload": {"id": "activity-1"},
    }
    assert state.record_cash_event(**event)
    assert not state.record_cash_event(**event)
    changed = {**event, "amount": "-1.26"}
    with pytest.raises(RuntimeError, match="CASH_EVENT_ID_CONFLICT"):
        state.record_cash_event(**changed)


def test_cash_ledger_requires_two_thousand_of_verified_cleared_funding(tmp_path: Path) -> None:
    state = LiveState(tmp_path / "cash.db")
    state.record_cash_event(
        event_id="funding-1",
        category="cleared_funding",
        activity_type="CSD",
        amount="1999.99",
        effective_date="2026-09-01",
        spendable_date="2026-09-01",
        payload={},
    )
    with pytest.raises(RuntimeError, match="CLEARED_FUNDING_BELOW_REQUIREMENT"):
        state.initialize_cash_ledger(evidence_id="funding-1", account_id_hash="sha256:account", as_of="2026-09-01")
