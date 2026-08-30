"""Closed reason-code namespace for normalized intraday continuation."""

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
        "INTRADAY_CONTINUATION_BULLISH",
        "INTRADAY_CONTINUATION_BEARISH",
    }
)

ALL_CODES = COMMON_NO_TRADE_CODES | ENTRY_CODES | {"INTRADAY_CONTINUATION_GATE_NOT_MET"}
