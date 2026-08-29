"""Deterministic platform-owned position exits and reduce-only authorization."""

from .manager import (
    PositionManagerError,
    authorize_reduce_only,
    build_execute_reduce_command,
    build_reduce_only_plan,
    evaluate_position,
    reduce_only_violations,
)
from .runtime import ExitProductionResult, produce_position_exit

__all__ = [
    "PositionManagerError",
    "ExitProductionResult",
    "authorize_reduce_only",
    "build_execute_reduce_command",
    "build_reduce_only_plan",
    "evaluate_position",
    "reduce_only_violations",
    "produce_position_exit",
]
