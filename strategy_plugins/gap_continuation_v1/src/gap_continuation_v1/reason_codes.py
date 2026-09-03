"""Closed reason-code namespace for standardized gap continuation.

The namespace is exactly the frozen
``research/candidates/gap_continuation__all_feasible__o2_v1/reason_codes.yaml``
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
        "GAP_CONTINUATION_BULLISH",
        "GAP_CONTINUATION_BEARISH",
    }
)

ALL_CODES = (
    COMMON_NO_TRADE_CODES
    | ENTRY_CODES
    | {"GAP_CONTINUATION_GATE_NOT_MET", "CORPORATE_ACTION_AMBIGUOUS"}
)
