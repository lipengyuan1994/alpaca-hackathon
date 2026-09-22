"""Deterministic live ETF runtime for T08, with archived L11 compatibility.

This package is deliberately separate from the paper wheel and research
simulator.  It contains no options code and does not expose a broker client
until the runtime has passed its account and activation gates.
"""

__all__ = ["__version__"]
__version__ = "0.1.0"
