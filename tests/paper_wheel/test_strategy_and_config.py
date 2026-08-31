from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from packages.paper_wheel.broker import PaperQuote
from packages.paper_wheel.config import load_config
from packages.paper_wheel.risk import quote_violations
from packages.paper_wheel.strategy import should_take_profit, target_strike_fraction, trend_is_up


def test_checked_in_v13_5_qqq_config_is_strict_and_paper_only() -> None:
    loaded = load_config(Path("configs/paper/v13_5_qqq.yaml"))

    assert loaded.config.runtime.mode == "paper"
    assert loaded.config.runtime.paper_base_url == "https://paper-api.alpaca.markets"
    assert loaded.config.strategy.strategy_id == "v13.5"
    assert loaded.config.strategy.symbols == ("QQQ",)
    assert loaded.config.activation.start_date == date(2026, 8, 31)
    assert loaded.config_hash.startswith("sha256:")


def test_v13_5_trend_and_strike_asymmetry_match_frozen_research() -> None:
    loaded = load_config(Path("configs/paper/v13_5_qqq.yaml"))
    config = loaded.config.strategy

    assert trend_is_up(tuple(Decimal(index) for index in range(1, 51)), sessions=50) is True
    assert trend_is_up(tuple(Decimal(51 - index) for index in range(1, 51)), sessions=50) is False
    assert trend_is_up((Decimal("1"),), sessions=50) is None
    assert target_strike_fraction(right="PUT", trend_up=True, config=config) == Decimal("0.99")
    assert target_strike_fraction(right="CALL", trend_up=True, config=config) == Decimal("1.03")
    assert target_strike_fraction(right="PUT", trend_up=False, config=config) == Decimal("0.97")
    assert target_strike_fraction(right="CALL", trend_up=False, config=config) == Decimal("1.01")


def test_take_profit_boundary_is_strict() -> None:
    assert not should_take_profit(
        entry_credit=Decimal("2.00"),
        close_debit=Decimal("1.70"),
        target_fraction=Decimal("0.15"),
    )
    assert should_take_profit(
        entry_credit=Decimal("2.00"),
        close_debit=Decimal("1.69"),
        target_fraction=Decimal("0.15"),
    )


def test_config_rejects_live_origin_and_unbounded_activation(tmp_path: Path) -> None:
    source = Path("configs/paper/v13_5_qqq.yaml").read_text(encoding="utf-8")
    live = tmp_path / "live.yaml"
    live.write_text(source.replace("https://paper-api.alpaca.markets", "https://api.alpaca.markets"), encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(live)

    long = tmp_path / "long.yaml"
    long.write_text(source.replace("end_date: 2026-09-04", "end_date: 2026-09-30"), encoding="utf-8")
    with pytest.raises(ValueError, match="WHEEL_ACTIVATION_WINDOW_TOO_LONG"):
        load_config(long)


def test_quote_future_skew_tolerance_has_strict_boundary() -> None:
    now = datetime(2026, 8, 31, 13, 30, tzinfo=UTC)
    common = {
        "now": now,
        "maximum_age_seconds": 15,
        "maximum_future_skew_seconds": 2,
        "maximum_relative_spread": Decimal("0.25"),
        "minimum_bid": Decimal("0.05"),
    }

    accepted = quote_violations(
        PaperQuote(bid=Decimal("1.00"), ask=Decimal("1.10"), timestamp=now + timedelta(seconds=2)),
        **common,
    )
    rejected = quote_violations(
        PaperQuote(
            bid=Decimal("1.00"),
            ask=Decimal("1.10"),
            timestamp=now + timedelta(seconds=2, microseconds=1),
        ),
        **common,
    )

    assert accepted == ()
    assert rejected == ("WHEEL_QUOTE_STALE",)
