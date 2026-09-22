from datetime import UTC, datetime
from pathlib import Path

from packages.etf_live.state import LiveState


def test_state_binds_config_and_preserves_order_and_hash_chain(tmp_path: Path) -> None:
    state = LiveState(tmp_path / "state.db")
    state.bind_config("sha256:" + "a" * 64)
    decision = {"decision_id": "d1", "signal_cutoff": "2026-09-18T20:00:00Z", "execution_session": "2026-09-19", "review_kind": "weekly", "intents": []}
    state.save_decision(decision)
    state.save_order({"client_order_id": "l11-d1-tqqq-buy", "decision_id": "d1", "symbol": "TQQQ", "side": "buy", "order_type": "limit", "requested_qty": "1.2", "limit_price": "100", "status": "submit_pending", "reserved_cash": "120", "updated_at": datetime.now(UTC).isoformat()})
    assert len(state.open_orders()) == 1
    state.update_order("l11-d1-tqqq-buy", status="accepted", broker_order_id="o1")
    assert state.order("l11-d1-tqqq-buy")["broker_order_id"] == "o1"
    state.update_order("l11-d1-tqqq-buy", status="filled")
    assert state.open_orders() == []
    assert state.get_meta("config_hash") == "sha256:" + "a" * 64


def test_activation_requires_reason_and_is_hash_bound(tmp_path: Path) -> None:
    state = LiveState(tmp_path / "state.db")
    token = state.set_activation(account_id_hash="sha256:" + "a" * 64, config_hash="sha256:" + "b" * 64, operator_reason="explicit live activation", at=datetime.now(UTC))
    assert token.startswith("sha256:")
    assert state.activation()["token_hash"] == token
