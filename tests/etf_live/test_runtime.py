from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from packages.contracts.canonical import canonical_hash
from packages.etf_live.broker import AccountSnapshot, Quote
from packages.etf_live.config import LiveConfig
from packages.etf_live.runtime import LiveRuntime
from packages.etf_live.state import LiveState


class FakeBroker:
    def __init__(self) -> None:
        self.submissions: list[dict[str, object]] = []

    def submit_order(self, payload: dict[str, object]) -> dict[str, object]:
        self.submissions.append(payload)
        return {"id": f"broker-{len(self.submissions)}", "status": "accepted", **payload}


def _config(tmp_path: Path, *, mode: str = "live") -> LiveConfig:
    enabled = tmp_path / "enabled"
    enabled.write_text("1\n", encoding="utf-8")
    return LiveConfig(
        mode=mode,
        account_id="acct",
        state_path=tmp_path / "state.db",
        enabled_file=enabled,
        secrets_root=tmp_path,
        strategy_config_hash="sha256:" + "a" * 64,
        telegram_enabled=False,
    )


def _armed_runtime(tmp_path: Path) -> tuple[LiveRuntime, FakeBroker, datetime]:
    config = _config(tmp_path)
    state = LiveState(config.state_path)
    state.bind_config(config.config_hash)
    state.set_activation(
        account_id_hash=canonical_hash({"account_id": config.account_id}),
        config_hash=config.config_hash,
        operator_reason="fixture activation",
        at=datetime(2026, 9, 21, 14, 31, tzinfo=UTC),
    )
    broker = FakeBroker()
    now = datetime(2026, 9, 21, 14, 31, tzinfo=UTC)
    return LiveRuntime(config=config, broker=broker, state=state), broker, now


def test_buy_is_capped_to_settled_cash_and_sell_is_market(tmp_path: Path) -> None:
    runtime, broker, now = _armed_runtime(tmp_path)
    quote = Quote("TQQQ", Decimal("100.00"), Decimal("100.10"), now)

    buy = runtime.submit_intent(
        intent={"symbol": "TQQQ", "side": "buy", "quantity": "20"},
        decision_id="d-buy",
        quote=quote,
        settled_cash=Decimal("1000"),
        now=now,
    )
    assert buy["status"] == "SUBMITTED"
    assert Decimal(str(buy["requested_qty"])) * Decimal(str(buy["limit_price"])) <= Decimal("1000")
    assert broker.submissions[0]["type"] == "limit"

    wide_quote = Quote("TQQQ", Decimal("99.00"), Decimal("101.00"), now)
    sell = runtime.submit_intent(
        intent={"symbol": "TQQQ", "side": "sell", "quantity": "1"},
        decision_id="d-sell",
        quote=wide_quote,
        settled_cash=Decimal("0"),
        now=now,
    )
    assert sell["status"] == "SUBMITTED"
    assert broker.submissions[1]["type"] == "market"
    assert "limit_price" not in broker.submissions[1]


def test_observe_mode_cannot_submit_even_with_an_activation_record(tmp_path: Path) -> None:
    config = _config(tmp_path, mode="observe")
    state = LiveState(config.state_path)
    state.bind_config(config.config_hash)
    state.set_activation(
        account_id_hash=canonical_hash({"account_id": config.account_id}),
        config_hash=config.config_hash,
        operator_reason="fixture activation",
        at=datetime(2026, 9, 21, 14, 31, tzinfo=UTC),
    )
    runtime = LiveRuntime(config=config, broker=FakeBroker(), state=state)
    with pytest.raises(RuntimeError, match="OBSERVE_ONLY"):
        runtime.submit_intent(
            intent={"symbol": "TQQQ", "side": "sell", "quantity": "1"},
            decision_id="d-observe",
            quote=Quote("TQQQ", Decimal("100"), Decimal("100.01"), datetime(2026, 9, 21, 14, 31, tzinfo=UTC)),
            settled_cash=Decimal("0"),
            now=datetime(2026, 9, 21, 14, 31, tzinfo=UTC),
        )


def test_preflight_blocks_non_one_multiplier_and_unmanaged_position(tmp_path: Path) -> None:
    config = _config(tmp_path)

    class PreflightBroker(FakeBroker):
        def __init__(self, multiplier: str, positions: list[dict[str, str]], orders: list[dict[str, str]] | None = None) -> None:
            super().__init__()
            self.multiplier = multiplier
            self._positions = positions
            self._orders = orders or []

        def account(self) -> AccountSnapshot:
            return AccountSnapshot("acct", "ACTIVE", Decimal("1000"), Decimal("1000"), Decimal("1000"), Decimal("1000"), Decimal(self.multiplier), False, False, False)

        def positions(self) -> list[dict[str, str]]:
            return self._positions

        def open_orders(self) -> list[dict[str, str]]:
            return self._orders

        def asset(self, symbol: str) -> dict[str, object]:
            return {"symbol": symbol, "tradable": True, "fractionable": True}

    with pytest.raises(RuntimeError, match="MULTIPLIER_NOT_ONE"):
        LiveRuntime(config=config, broker=PreflightBroker("2", [])).preflight(now=datetime.now(UTC))

    with pytest.raises(RuntimeError, match="UNMANAGED_ACCOUNT_ACTIVITY"):
        LiveRuntime(config=config, broker=PreflightBroker("1", [{"symbol": "SPY", "qty": "1"}])).preflight(now=datetime.now(UTC))

    with pytest.raises(RuntimeError, match="UNMANAGED_ACCOUNT_ACTIVITY"):
        LiveRuntime(config=config, broker=PreflightBroker("1", [], [{"symbol": "TQQQ", "client_order_id": "manual-order"}])).preflight(now=datetime.now(UTC))

    with pytest.raises(RuntimeError, match="ACCOUNT_NOT_FLAT_AT_ACTIVATION"):
        LiveRuntime(config=config, broker=PreflightBroker("1", [{"symbol": "TQQQ", "qty": "1"}])).preflight(now=datetime.now(UTC))


def test_reconcile_records_incremental_fill_and_broker_settled_cash(tmp_path: Path) -> None:
    config = _config(tmp_path)
    state = LiveState(config.state_path)
    state.bind_config(config.config_hash)
    state.save_order(
        {
            "client_order_id": "l11-d1-tqqq-buy",
            "decision_id": "d1",
            "symbol": "TQQQ",
            "side": "buy",
            "order_type": "limit",
            "requested_qty": "2",
            "limit_price": "50",
            "status": "accepted",
            "broker_order_id": "broker-1",
            "reserved_cash": "100",
            "updated_at": datetime.now(UTC).isoformat(),
        }
    )
    state.set_settled_cash("1000")

    class FillBroker(FakeBroker):
        def account(self) -> AccountSnapshot:
            return AccountSnapshot("acct", "ACTIVE", Decimal("900"), Decimal("900"), Decimal("900"), Decimal("1000"), Decimal("1"), False, False, False)

        def positions(self) -> list[dict[str, str]]:
            return [{"symbol": "TQQQ", "qty": "2"}]

        def open_orders(self) -> list[dict[str, object]]:
            return []

        def order_by_id(self, order_id: str) -> dict[str, object] | None:
            assert order_id == "broker-1"
            return {"id": "broker-1", "client_order_id": "l11-d1-tqqq-buy", "status": "filled", "filled_qty": "2", "filled_avg_price": "50", "filled_at": "2026-09-21T14:31:00Z"}

        def order_by_client_id(self, client_order_id: str) -> dict[str, object] | None:
            raise AssertionError("client-id fallback should not be needed")

    result = LiveRuntime(config=config, broker=FillBroker(), state=state).reconcile(now=datetime(2026, 9, 21, 14, 31, tzinfo=UTC))
    assert result["reconciled_fills"] == 1
    assert state.order("l11-d1-tqqq-buy")["status"] == "filled"
    assert state.filled_quantity("l11-d1-tqqq-buy") == "2"
    assert state.settled_cash() == "900"
