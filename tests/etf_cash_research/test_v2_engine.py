from __future__ import annotations

import pandas as pd

from packages.etf_cash_research.audit_v2 import run_engine_audit
from packages.etf_cash_research.protocol_v2 import (
    DEFAULT_STUDY_PROTOCOL,
    TRACK_A_UNIVERSE,
    CandidateSpec,
)
from packages.etf_cash_research.simulator_v2 import (
    _align_signal_frames,
    _intersection_sessions,
    _settlement_date_v2,
    run_generic_backtest,
)


class _EntryStrategy:
    def evaluate(self, frames, index, **kwargs):
        return type("Signal", (), {"asof": frames["QQQM"].iloc[index]["date"], "target_weights": {"QQQM": 0.99}, "reason_code": "ENTRY", "entries": ("QQQM",), "exits": ()})()


class _ExitOnLastStrategy(_EntryStrategy):
    def evaluate(self, frames, index, **kwargs):
        if index >= 2:
            return type("Signal", (), {"asof": frames["QQQM"].iloc[index]["date"], "target_weights": {}, "reason_code": "EXIT", "entries": (), "exits": ("QQQM",)})()
        return super().evaluate(frames, index, **kwargs)


class _CalendarRecordingStrategy:
    def __init__(self) -> None:
        self.observations: list[dict[str, pd.Timestamp]] = []

    def evaluate(self, frames, index, **kwargs):
        self.observations.append({symbol: pd.Timestamp(frame.iloc[index]["date"]) for symbol, frame in frames.items()})
        asof = next(iter(self.observations[-1].values()))
        return type("Signal", (), {"asof": asof, "target_weights": {}, "reason_code": "NOOP", "entries": (), "exits": ()})()


def _bars() -> pd.DataFrame:
    dates = pd.to_datetime(["2024-05-24", "2024-05-28", "2024-05-29", "2024-05-30"], utc=True)
    rows = []
    for symbol, base in (("QQQM", 100.0), ("SMH", 200.0)):
        for index, date in enumerate(dates):
            price = base + index
            rows.append({"date": date, "symbol": symbol, "open": price, "high": price + 1, "low": price - 1, "close": price, "dividend": 0.0, "dividend_payable_date": pd.NaT, "split_factor": 1.0})
    return pd.DataFrame(rows)


def _bars_with_different_listing_starts() -> pd.DataFrame:
    dates = pd.date_range("2020-01-02", periods=6, freq="B", tz="UTC")
    rows = []
    for symbol, first_index, base in (("QQQM", 2, 100.0), ("SMH", 0, 200.0)):
        for index, date in enumerate(dates[first_index:], start=first_index):
            price = base + index
            rows.append({"date": date, "symbol": symbol, "open": price, "high": price + 1, "low": price - 1, "close": price, "dividend": 0.0, "dividend_payable_date": pd.NaT, "split_factor": 1.0})
    return pd.DataFrame(rows)


def test_signal_frames_align_positional_histories_to_common_dates() -> None:
    bars = _bars_with_different_listing_starts()
    common = _intersection_sessions(bars, ("QQQM", "SMH"))
    aligned, indices = _align_signal_frames(bars, ("QQQM", "SMH"), common)
    index = indices[common[0]]

    # The old independent-history construction would compare QQQM's first
    # common session with SMH's earlier listing-date row at this index.
    qqqm_independent = bars[bars["symbol"] == "QQQM"].sort_values("date").reset_index(drop=True)
    smh_independent = bars[bars["symbol"] == "SMH"].sort_values("date").reset_index(drop=True)
    assert qqqm_independent.iloc[0]["date"] != smh_independent.iloc[0]["date"]
    assert aligned["QQQM"].iloc[index]["date"] == common[0]
    assert aligned["SMH"].iloc[index]["date"] == common[0]


def test_simulator_passes_one_calendar_date_to_every_symbol_history() -> None:
    strategy = _CalendarRecordingStrategy()
    candidate = CandidateSpec("TEST", "TEST", TRACK_A_UNIVERSE)
    run_generic_backtest(
        _bars_with_different_listing_starts(),
        candidate=candidate,
        strategy=strategy,
        cost=DEFAULT_STUDY_PROTOCOL.costs_track_a[0],
        protocol=DEFAULT_STUDY_PROTOCOL,
        start="2020-01-06",
        end="2020-01-09",
    )
    assert strategy.observations
    assert all(len(set(observation.values())) == 1 for observation in strategy.observations)


def test_engine_audit_passes() -> None:
    assert run_engine_audit()["status"] == "OK"


def test_v2_starting_equity_is_explicitly_one_thousand() -> None:
    result = run_generic_backtest(_bars(), candidate=CandidateSpec("TEST", "TEST", TRACK_A_UNIVERSE), strategy=_EntryStrategy(), cost=DEFAULT_STUDY_PROTOCOL.costs_track_a[0], protocol=DEFAULT_STUDY_PROTOCOL, start="2024-05-28", end="2024-05-30")
    assert result.metrics["starting_equity"] == 1000.0
    assert result.metrics["net_pnl"] == result.metrics["ending_equity"] - 1000.0


def test_final_session_sale_keeps_unsettled_proceeds_pending() -> None:
    result = run_generic_backtest(_bars(), candidate=CandidateSpec("TEST", "TEST", TRACK_A_UNIVERSE), strategy=_ExitOnLastStrategy(), cost=DEFAULT_STUDY_PROTOCOL.costs_track_a[0], protocol=DEFAULT_STUDY_PROTOCOL, start="2024-05-28", end="2024-05-30")
    sells = result.fills[result.fills["side"] == "sell"]
    assert not sells.empty and pd.isna(sells.iloc[-1]["settlement_date"])


def test_settlement_skips_a_federal_holiday_even_if_fixture_has_a_session() -> None:
    sessions = list(pd.to_datetime(["2024-06-14", "2024-06-17", "2024-06-19", "2024-06-20"], utc=True))
    # A T+1 sale on June 18 (omitted from this compact fixture) would not
    # settle on Juneteenth; the next available session is Jun 20.
    trade_date = pd.Timestamp("2024-06-18", tz="UTC")
    sessions.append(trade_date)
    sessions.sort()
    assert _settlement_date_v2(trade_date, DEFAULT_STUDY_PROTOCOL.settlement_change, sessions).date().isoformat() == "2024-06-20"
