"""Small deterministic fixtures used by the v2 ``audit-engine`` command."""

from __future__ import annotations

import pandas as pd

from .protocol_v2 import DEFAULT_STUDY_PROTOCOL, TRACK_A_UNIVERSE, CandidateSpec
from .simulator_v2 import _settlement_date_v2, run_generic_backtest


class _StaticStrategy:
    def evaluate(self, frames, index):
        return type("Signal", (), {"asof": frames["QQQM"].iloc[index]["date"], "target_weights": {"QQQM": 0.99, "SMH": 0.0}, "reason_code": "FIXTURE_ENTRY", "entries": ("QQQM",), "exits": ()})()


def _fixture_bars() -> pd.DataFrame:
    dates = pd.to_datetime(["2024-05-24", "2024-05-28", "2024-05-29", "2024-05-30"], utc=True)
    rows = []
    for symbol, base in (("QQQM", 100.0), ("SMH", 200.0)):
        for index, value in enumerate(dates):
            close = base + index
            rows.append({"date": value, "symbol": symbol, "open": close, "high": close + 1.0, "low": close - 1.0, "close": close, "volume": 1000, "dividend": 0.0, "dividend_payable_date": pd.NaT, "split_factor": 1.0})
    return pd.DataFrame(rows)


def run_engine_audit() -> dict[str, object]:
    sessions = list(pd.to_datetime(["2024-05-24", "2024-05-28", "2024-05-29"], utc=True))
    t2 = _settlement_date_v2(sessions[0], DEFAULT_STUDY_PROTOCOL.settlement_change, sessions)
    t1 = _settlement_date_v2(sessions[1], DEFAULT_STUDY_PROTOCOL.settlement_change, sessions)
    if t2.date().isoformat() != "2024-05-29" or t1.date().isoformat() != "2024-05-29":
        return {"status": "FAILED", "reason": "SETTLEMENT_FIXTURE"}
    candidate = CandidateSpec("AUDIT__QQQM_SMH__primary", "AUDIT", TRACK_A_UNIVERSE)
    result = run_generic_backtest(_fixture_bars(), candidate=candidate, strategy=_StaticStrategy(), cost=DEFAULT_STUDY_PROTOCOL.costs_track_a[0], protocol=DEFAULT_STUDY_PROTOCOL, start="2024-05-28", end="2024-05-30")
    if result.equity.empty or result.equity["equity"].isna().any():
        return {"status": "FAILED", "reason": "EQUITY_RECONCILIATION"}
    return {"status": "OK", "settlement_t2_fixture": t2.date().isoformat(), "settlement_t1_fixture": t1.date().isoformat(), "equity_rows": len(result.equity)}
