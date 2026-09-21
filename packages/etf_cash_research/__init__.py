"""Deterministic, long-only ETF cash-account research.

The package is deliberately separate from the paper execution and options
research packages.  It has no broker order client and its collector exposes
only GET requests through the existing read-only Alpaca client.
"""

from .metrics import compute_metrics
from .protocol import DEFAULT_PROTOCOL, ResearchProtocol
from .simulator import BacktestResult, run_backtest

__all__ = (
    "BacktestResult",
    "DEFAULT_PROTOCOL",
    "ResearchProtocol",
    "compute_metrics",
    "run_backtest",
)
