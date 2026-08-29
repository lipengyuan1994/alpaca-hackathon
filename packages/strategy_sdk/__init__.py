"""Public, dependency-light strategy plug-in protocol."""

from .arbitration import (
    FROZEN_SYMBOL_ORDER,
    ArbitrationError,
    RankedCandidate,
    select_ranked_symbol,
)
from .protocol import StrategyPluginV1

# StrategyEvaluationV1 currently carries the source hash on the public wire
# contract. Plug-ins must use this non-authoritative placeholder; the isolated
# host discards it and injects the registry-verified package digest.
UNBOUND_PLUGIN_CONTENT_HASH = "sha256:" + "0" * 64

__all__ = [
    "ArbitrationError",
    "FROZEN_SYMBOL_ORDER",
    "RankedCandidate",
    "StrategyPluginV1",
    "UNBOUND_PLUGIN_CONTENT_HASH",
    "select_ranked_symbol",
]
