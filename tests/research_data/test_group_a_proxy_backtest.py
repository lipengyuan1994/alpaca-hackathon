from pathlib import Path

import pandas as pd

from packages.research_data import group_a_proxy_backtest as replay


def test_cumulative_pnl_svg_is_deterministic_and_contains_all_series() -> None:
    curves = {
        "alpha": [{"date": "2025-01-02", "daily_pnl": 10.0}, {"date": "2025-01-03", "daily_pnl": -2.0}],
        "beta": [{"date": "2025-01-02", "daily_pnl": -4.0}],
    }

    first = replay._cumulative_pnl_svg(curves)

    assert first == replay._cumulative_pnl_svg(curves)
    assert b"Cumulative option-proxy P&amp;L" in first
    assert b"alpha" in first
    assert b"beta" in first


def test_cumulative_pnl_svg_handles_no_filled_trades() -> None:
    assert b"No option-proxy fills" in replay._cumulative_pnl_svg({})


def test_complete_market_dates_includes_inactive_sessions() -> None:
    bars = pd.DataFrame(
        [
            {"event_time": "2025-01-02T15:00:00Z"},
            {"event_time": "2025-01-03T15:00:00Z"},
            {"event_time": "2025-01-06T15:00:00Z"},
        ]
    )

    assert replay._complete_market_dates(bars, start="2025-01-02", end="2025-01-06") == [
        "2025-01-02", "2025-01-03", "2025-01-06",
    ]


def test_session_exit_uses_future_trading_sessions_not_calendar_days() -> None:
    empty = pd.DataFrame(columns=["bucket", "close", "session_vwap"])
    intervals = {
        ("QQQ", pd.Timestamp("2025-01-02").date()): empty,
        ("QQQ", pd.Timestamp("2025-01-03").date()): empty,
        ("QQQ", pd.Timestamp("2025-01-06").date()): empty,
        ("QQQ", pd.Timestamp("2025-01-07").date()): empty,
    }
    record = {"underlying": "QQQ", "holding_sessions": 3, "session_exit_clock": "14:00"}

    exit_time = replay._session_exit_time(
        record,
        underlying_bars=intervals,
        fill_time=pd.Timestamp("2025-01-02T15:01:00Z"),
    )

    assert exit_time == pd.Timestamp("2025-01-07T19:00:00Z")


def test_missing_exit_is_no_fill_except_in_named_severe_stress(monkeypatch) -> None:
    event_time = pd.Timestamp("2025-01-02T15:31:00Z")
    bars = pd.DataFrame(
        [
            {"symbol": "SPY250117C00600000", "event_time": event_time, "open": 2.0, "high": 2.1, "low": 1.9, "close": 2.0},
            {"symbol": "SPY250117C00610000", "event_time": event_time, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0},
        ]
    )
    monkeypatch.setattr(replay, "_bar", lambda *_: bars)
    record = {
        "request_id": "000001", "strategy": "intraday_continuation_v1", "underlying": "SPY",
        "side": "CALL", "symbols": ["SPY250117C00600000", "SPY250117C00610000"],
        "decision_time": "2025-01-02T15:30:00Z",
    }
    manifest = {"datasets": [{"dataset_id": "option_bars_000001", "artifact": {}}]}
    assert replay._trade(record, root=Path("."), option_manifest=manifest, fee=0.10, severe=False) is None
    severe = replay._trade(record, root=Path("."), option_manifest=manifest, fee=0.10, severe=True)
    assert severe is not None
    assert severe["missing_exit"] is True


def test_predefined_45_minute_exit_uses_45_minute_window(monkeypatch) -> None:
    entry_time = pd.Timestamp("2025-01-02T15:31:00Z")
    exit_time = pd.Timestamp("2025-01-02T16:17:00Z")
    bars = pd.DataFrame(
        [
            {"symbol": "SPY250117C00600000", "event_time": entry_time, "open": 2.0, "high": 2.1, "low": 1.9, "close": 2.0},
            {"symbol": "SPY250117C00610000", "event_time": entry_time, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0},
            {"symbol": "SPY250117C00600000", "event_time": exit_time, "open": 2.5, "high": 2.6, "low": 2.4, "close": 2.5},
            {"symbol": "SPY250117C00610000", "event_time": exit_time, "open": 1.2, "high": 1.3, "low": 1.1, "close": 1.2},
        ]
    )
    monkeypatch.setattr(replay, "_bar", lambda *_: bars)
    record = {
        "request_id": "000001", "strategy": "intraday_continuation_v1", "underlying": "SPY",
        "side": "CALL", "symbols": ["SPY250117C00600000", "SPY250117C00610000"],
        "decision_time": "2025-01-02T15:30:00Z",
    }
    manifest = {"datasets": [{"dataset_id": "option_bars_000001", "artifact": {}}]}

    trade = replay._trade(
        record,
        root=Path("."),
        option_manifest=manifest,
        fee=0.10,
        severe=False,
        exit_minutes=45,
    )

    assert trade is not None
    assert trade["exit_time"] == "2025-01-02T16:17:00Z"
    assert trade["exit_policy_time"] == "2025-01-02T16:16:00Z"
    assert trade["exposure_minutes"] == 46.0
    single_long = replay._trade(
        record,
        root=Path("."),
        option_manifest=manifest,
        fee=0.10,
        severe=False,
        structure="single_long",
        exit_minutes=45,
    )
    assert single_long is not None
    assert round(single_long["pnl"], 8) == 4.8
    assert replay._trade(
        record,
        root=Path("."),
        option_manifest=manifest,
        fee=0.10,
        severe=False,
        exit_minutes=60,
    ) is None
    assert replay._trade(
        record,
        root=Path("."),
        option_manifest=manifest,
        fee=0.10,
        severe=False,
        exit_minutes=90,
    ) is None


def test_record_time_exit_override_is_deterministic(monkeypatch) -> None:
    entry_time = pd.Timestamp("2025-01-02T15:31:00Z")
    exit_time = pd.Timestamp("2025-01-02T16:17:00Z")
    bars = pd.DataFrame(
        [
            {"symbol": "SPY250117C00600000", "event_time": entry_time, "open": 2.0, "high": 2.1, "low": 1.9, "close": 2.0},
            {"symbol": "SPY250117C00610000", "event_time": entry_time, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0},
            {"symbol": "SPY250117C00600000", "event_time": exit_time, "open": 2.5, "high": 2.6, "low": 2.4, "close": 2.5},
            {"symbol": "SPY250117C00610000", "event_time": exit_time, "open": 1.2, "high": 1.3, "low": 1.1, "close": 1.2},
        ]
    )
    monkeypatch.setattr(replay, "_bar", lambda *_: bars)
    record = {
        "request_id": "000001", "strategy": "late_momentum_v2", "underlying": "SPY",
        "side": "CALL", "symbols": ["SPY250117C00600000", "SPY250117C00610000"],
        "decision_time": "2025-01-02T15:30:00Z", "time_exit_minutes": 45,
    }
    manifest = {"datasets": [{"dataset_id": "option_bars_000001", "artifact": {}}]}

    trade = replay._trade(
        record, root=Path("."), option_manifest=manifest, fee=0.10, severe=False,
        exit_minutes=90,
    )

    assert trade is not None
    assert trade["exit_policy_time"] == "2025-01-02T16:16:00Z"
    assert replay._trade(
        record, root=Path("."), option_manifest=manifest, fee=0.10, severe=False,
        exit_minutes=45, override_exit_minutes=60,
    ) is None


def test_continuation_policy_exits_on_adverse_completed_vwap_cross() -> None:
    bars = pd.DataFrame(
        [
            {"symbol": "SPY", "event_time": "2025-01-02T15:30:00Z", "close": 100.0, "volume": 10.0, "vwap": 100.0},
            {"symbol": "SPY", "event_time": "2025-01-02T15:44:00Z", "close": 99.0, "volume": 10.0, "vwap": 99.0},
        ]
    )
    record = {
        "strategy": "intraday_continuation_v1",
        "underlying": "SPY",
        "side": "CALL",
        "decision_time": "2025-01-02T15:30:00Z",
    }

    exit_time, reason = replay._policy_exit(record, underlying_bars=bars, exit_minutes=60)

    assert exit_time == pd.Timestamp("2025-01-02T15:45:00Z")
    assert reason == "ADVERSE_VWAP_CROSS"


def test_sensitivity_variant_uses_its_declared_continuation_policy_family() -> None:
    bars = pd.DataFrame(
        [
            {"symbol": "SPY", "event_time": "2025-01-02T15:30:00Z", "close": 100.0, "volume": 10.0, "vwap": 100.0},
            {"symbol": "SPY", "event_time": "2025-01-02T15:44:00Z", "close": 99.0, "volume": 10.0, "vwap": 99.0},
        ]
    )
    record = {
        "strategy": "intraday_continuation_v1_momentum_1_25",
        "strategy_family": "intraday_continuation_v1",
        "underlying": "SPY",
        "side": "CALL",
        "decision_time": "2025-01-02T15:30:00Z",
    }

    exit_time, reason = replay._policy_exit(record, underlying_bars=bars, exit_minutes=60)

    assert exit_time == pd.Timestamp("2025-01-02T15:45:00Z")
    assert reason == "ADVERSE_VWAP_CROSS"


def test_bar_open_credit_is_the_debit_economic_complement_before_equal_fees(monkeypatch) -> None:
    entry_time = pd.Timestamp("2025-01-02T15:31:00Z")
    exit_time = pd.Timestamp("2025-01-02T16:17:00Z")
    bars = pd.DataFrame(
        [
            {"symbol": "SPY250117C00600000", "event_time": entry_time, "open": 2.0, "high": 2.1, "low": 1.9, "close": 2.0},
            {"symbol": "SPY250117C00610000", "event_time": entry_time, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0},
            {"symbol": "SPY250117C00600000", "event_time": exit_time, "open": 2.5, "high": 2.6, "low": 2.4, "close": 2.5},
            {"symbol": "SPY250117C00610000", "event_time": exit_time, "open": 1.2, "high": 1.3, "low": 1.1, "close": 1.2},
        ]
    )
    monkeypatch.setattr(replay, "_bar", lambda *_: bars)
    record = {
        "request_id": "000001", "strategy": "intraday_continuation_v1", "underlying": "SPY",
        "side": "CALL", "symbols": ["SPY250117C00600000", "SPY250117C00610000"],
        "decision_time": "2025-01-02T15:30:00Z",
    }
    manifest = {"datasets": [{"dataset_id": "option_bars_000001", "artifact": {}}]}

    debit = replay._trade(
        record, root=Path("."), option_manifest=manifest, fee=0.0, severe=False,
        structure="debit", execution_model="bar_open", exit_minutes=45,
    )
    credit = replay._trade(
        record, root=Path("."), option_manifest=manifest, fee=0.0, severe=False,
        structure="credit", execution_model="bar_open", exit_minutes=45,
    )

    assert debit is not None and credit is not None
    assert debit["pnl"] == -credit["pnl"]


def test_long_straddle_buys_and_sells_both_legs(monkeypatch) -> None:
    entry_time = pd.Timestamp("2025-01-02T15:31:00Z")
    exit_time = pd.Timestamp("2025-01-02T16:17:00Z")
    call_symbol = "SPY250117C00600000"
    put_symbol = "SPY250117P00600000"
    bars = pd.DataFrame(
        [
            {"symbol": call_symbol, "event_time": entry_time, "open": 2.0, "high": 2.1, "low": 1.9, "close": 2.0},
            {"symbol": put_symbol, "event_time": entry_time, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0},
            {"symbol": call_symbol, "event_time": exit_time, "open": 2.5, "high": 2.6, "low": 2.4, "close": 2.5},
            {"symbol": put_symbol, "event_time": exit_time, "open": 1.2, "high": 1.3, "low": 1.1, "close": 1.2},
        ]
    )
    monkeypatch.setattr(replay, "_bar", lambda *_: bars)
    record = {
        "request_id": "000001", "strategy": "long_vol_v3", "underlying": "SPY",
        "side": "LONG_VOL", "symbols": [call_symbol, put_symbol],
        "decision_time": "2025-01-02T15:30:00Z",
    }
    manifest = {"datasets": [{"dataset_id": "option_bars_000001", "artifact": {}}]}

    trade = replay._trade(
        record, root=Path("."), option_manifest=manifest, fee=0.10, severe=False,
        structure="long_straddle", execution_model="bar_open", exit_minutes=45,
    )

    assert trade is not None
    assert trade["debit"] == 3.0
    assert trade["exit_value"] == 3.7
    assert round(trade["pnl"], 8) == 69.6


def test_calendar_uses_explicit_far_and_near_leg_order(monkeypatch) -> None:
    entry_time = pd.Timestamp("2025-01-02T15:31:00Z")
    exit_time = pd.Timestamp("2025-01-02T16:17:00Z")
    near_symbol = "SPY250117C00600000"
    far_symbol = "SPY250124C00600000"
    bars = pd.DataFrame(
        [
            {"symbol": far_symbol, "event_time": entry_time, "open": 2.0, "high": 2.1, "low": 1.9, "close": 2.0},
            {"symbol": near_symbol, "event_time": entry_time, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0},
            {"symbol": far_symbol, "event_time": exit_time, "open": 2.5, "high": 2.6, "low": 2.4, "close": 2.5},
            {"symbol": near_symbol, "event_time": exit_time, "open": 1.2, "high": 1.3, "low": 1.1, "close": 1.2},
        ]
    )
    monkeypatch.setattr(replay, "_bar", lambda *_: bars)
    record = {
        "request_id": "000001", "strategy": "calendar_v10", "underlying": "SPY", "side": "CALL",
        "symbols": [far_symbol, near_symbol], "long_symbol": far_symbol, "short_symbol": near_symbol,
        "decision_time": "2025-01-02T15:30:00Z",
    }
    manifest = {"datasets": [{"dataset_id": "option_bars_000001", "artifact": {}}]}

    trade = replay._trade(
        record, root=Path("."), option_manifest=manifest, fee=0.10, severe=False,
        structure="calendar", execution_model="bar_open", exit_minutes=45,
    )

    assert trade is not None
    assert trade["long_symbol"] == far_symbol
    assert trade["short_symbol"] == near_symbol
    assert round(trade["pnl"], 8) == 29.6


def test_missing_credit_exit_is_never_a_severe_stress_gain(monkeypatch) -> None:
    event_time = pd.Timestamp("2025-01-02T15:31:00Z")
    bars = pd.DataFrame(
        [
            {"symbol": "SPY250117C00600000", "event_time": event_time, "open": 2.0, "high": 2.1, "low": 1.9, "close": 2.0},
            {"symbol": "SPY250117C00610000", "event_time": event_time, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0},
        ]
    )
    monkeypatch.setattr(replay, "_bar", lambda *_: bars)
    record = {
        "request_id": "000001", "strategy": "intraday_continuation_v1", "underlying": "SPY",
        "side": "CALL", "symbols": ["SPY250117C00600000", "SPY250117C00610000"],
        "decision_time": "2025-01-02T15:30:00Z",
    }
    manifest = {"datasets": [{"dataset_id": "option_bars_000001", "artifact": {}}]}

    assert replay._trade(
        record, root=Path("."), option_manifest=manifest, fee=0.10, severe=True,
        structure="credit", execution_model="buffered", exit_minutes=60,
    ) is None
