from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from packages.paper_wheel.broker import AlpacaPaperWheelBroker


class _Trading:
    def __init__(self, contracts: list[Any]) -> None:
        self.contracts = contracts

    def get_option_contracts(self, request: Any) -> Any:
        assert request.limit == 1000
        return SimpleNamespace(option_contracts=self.contracts, next_page_token=None)


class _Options:
    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    def get_option_latest_quote(self, request: Any) -> dict[str, Any]:
        symbols = list(request.symbol_or_symbols)
        self.batch_sizes.append(len(symbols))
        return {
            symbol: SimpleNamespace(
                bid_price=Decimal("1.00"),
                ask_price=Decimal("1.10"),
                timestamp=datetime(2026, 8, 31, 14, 0, tzinfo=UTC),
            )
            for symbol in symbols
        }


def test_option_quotes_are_batched_at_alpacas_one_hundred_symbol_limit() -> None:
    expiration = date(2026, 9, 11)
    contracts = [
        SimpleNamespace(
            symbol=f"QQQ260911P{index:08d}",
            tradable=True,
            strike_price=Decimal(index),
            expiration_date=expiration,
        )
        for index in range(1, 206)
    ]
    option_data = _Options()
    broker = AlpacaPaperWheelBroker(
        trading_client=_Trading(contracts),
        stock_data_client=object(),
        option_data_client=option_data,
        expected_account_id="paper-fixture",
    )

    rows = broker.option_candidates(
        "QQQ",
        right="PUT",
        minimum_expiration=expiration - timedelta(days=1),
        maximum_expiration=expiration + timedelta(days=1),
        minimum_strike=Decimal("1"),
        maximum_strike=Decimal("1000"),
    )

    assert len(rows) == 205
    assert option_data.batch_sizes == [100, 100, 5]
