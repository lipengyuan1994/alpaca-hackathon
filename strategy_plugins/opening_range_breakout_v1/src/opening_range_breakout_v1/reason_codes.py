"""Closed reason-code namespace for opening-range breakout.

The namespace is exactly the frozen
``research/candidates/opening_range_breakout__all_feasible__o2_v1/reason_codes.yaml``
declaration; the implementation emits no undeclared code.
"""

COMMON_NO_TRADE_CODES = frozenset(
    {
        "DATA_MISSING",
        "DATA_STALE",
        "DATA_QUALITY_REJECTED",
        "FEATURE_SCHEMA_MISMATCH",
        "OUTSIDE_DECISION_WINDOW",
        "EARLY_CLOSE_SESSION",
        "DAILY_ENTRY_ALREADY_USED",
        "NO_SIGNAL",
        "DIRECTION_AMBIGUOUS",
        "UNDERLYING_NOT_ALLOWED",
        "TEMPLATE_NOT_ALLOWED",
        "TUPLE_NOT_ALLOWED",
    }
)

ENTRY_CODES = frozenset(
    {
        "OPENING_RANGE_BREAKOUT_BULLISH",
        "OPENING_RANGE_BREAKOUT_BEARISH",
    }
)

ALL_CODES = COMMON_NO_TRADE_CODES | ENTRY_CODES | {"OPENING_RANGE_BREAKOUT_GATE_NOT_MET"}
