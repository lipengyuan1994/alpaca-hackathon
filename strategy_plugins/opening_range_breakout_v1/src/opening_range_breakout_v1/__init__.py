"""Group B opening-range-breakout deterministic research package."""

from __future__ import annotations

from .plugin import ALLOWED_UNDERLYINGS, FEATURE_CONTRACT_HASH, Plugin
from .signal import SignalResult, evaluate_signal

__all__ = [
    "ALLOWED_UNDERLYINGS",
    "FEATURE_CONTRACT_HASH",
    "Plugin",
    "SignalResult",
    "evaluate_signal",
]
