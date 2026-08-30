import pandas as pd

from packages.research_data.group_a_wheel_v12_backtest import _ActiveOption, _resolve


def _active(*, kind: str = "CSP", credit: float = 2.0) -> _ActiveOption:
    return _ActiveOption(
        record={"underlying": "SPY", "take_profit_fraction": 0.15, "request_id": "000001"},
        kind=kind,
        symbol="SPY250117P00600000" if kind == "CSP" else "SPY250117C00600000",
        strike=600.0,
        credit=credit,
        entry_time=pd.Timestamp("2025-01-02T15:31:00Z"),
        expiry_time=pd.Timestamp("2025-01-03T21:05:00Z"),
    )


def test_short_option_closes_only_after_profit_strictly_exceeds_15_percent() -> None:
    active = _active()
    bars = pd.DataFrame(
        [
            {"symbol": active.symbol, "event_time": "2025-01-02T15:32:00Z", "open": 1.80, "high": 1.80, "low": 1.80, "close": 1.80},
            {"symbol": active.symbol, "event_time": "2025-01-02T15:33:00Z", "open": 1.30, "high": 1.30, "low": 1.30, "close": 1.30},
        ]
    )
    bars["event_time"] = pd.to_datetime(bars["event_time"], utc=True)
    underlying = pd.DataFrame([{"symbol": "SPY", "event_time": "2025-01-03T21:00:00Z", "close": 610.0}])
    underlying["event_time"] = pd.to_datetime(underlying["event_time"], utc=True)

    resolved = _resolve(active, option_bars=bars, underlying_bars=underlying, cutoff=active.expiry_time)

    assert resolved is not None
    assert resolved["reason"] == "TAKE_PROFIT_15_PERCENT"
    assert resolved["time"] == pd.Timestamp("2025-01-02T15:33:00Z")


def test_expiring_itm_cash_secured_put_assigns_one_hundred_shares() -> None:
    active = _active()
    option_bars = pd.DataFrame(columns=["symbol", "event_time", "open", "high", "low", "close"])
    underlying = pd.DataFrame([{"symbol": "SPY", "event_time": "2025-01-03T21:00:00Z", "close": 590.0}])
    underlying["event_time"] = pd.to_datetime(underlying["event_time"], utc=True)

    resolved = _resolve(active, option_bars=option_bars, underlying_bars=underlying, cutoff=active.expiry_time)

    assert resolved is not None
    assert resolved["reason"] == "ASSIGNED"
    assert resolved["assigned"] is True
    assert resolved["called_away"] is False


def test_expiring_itm_covered_call_is_called_away() -> None:
    active = _active(kind="CC")
    option_bars = pd.DataFrame(columns=["symbol", "event_time", "open", "high", "low", "close"])
    underlying = pd.DataFrame([{"symbol": "SPY", "event_time": "2025-01-03T21:00:00Z", "close": 610.0}])
    underlying["event_time"] = pd.to_datetime(underlying["event_time"], utc=True)

    resolved = _resolve(active, option_bars=option_bars, underlying_bars=underlying, cutoff=active.expiry_time)

    assert resolved is not None
    assert resolved["reason"] == "CALLED_AWAY"
    assert resolved["called_away"] is True
    assert resolved["assigned"] is False
