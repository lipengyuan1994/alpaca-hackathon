"""Host-side resource-limited subprocess runner."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packages.contracts.canonical import canonical_json
from packages.contracts.models import StrategyConfigV1, StrategyContextV1, StrategyEvaluationV1


class PluginIsolationError(RuntimeError):
    """The runner refused or could not safely execute a plug-in."""


@dataclass(frozen=True)
class RunnerLimits:
    wall_time_seconds: float = 2.0
    cpu_seconds: int = 1
    max_output_bytes: int = 128_000
    memory_bytes: int = 256 * 1024 * 1024


def _limit_process(limits: RunnerLimits) -> None:
    """Apply POSIX limits in the child. Linux/container enforcement is mandatory."""
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds))
        resource.setrlimit(resource.RLIMIT_FSIZE, (limits.max_output_bytes, limits.max_output_bytes))
        if hasattr(resource, "RLIMIT_AS"):
            resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))
    except (ImportError, OSError, ValueError):
        # Process launch still remains fail-closed if output/time guarantees fail.
        pass


def run_plugin(
    *,
    entrypoint: str,
    context: StrategyContextV1,
    config: StrategyConfigV1,
    limits: RunnerLimits = RunnerLimits(),
) -> StrategyEvaluationV1:
    """Evaluate a registered plug-in once through canonical JSON stdin/stdout."""
    payload: dict[str, Any] = {
        "entrypoint": entrypoint,
        "context": context,
        "config": config,
    }
    source_root = Path.cwd()
    child = source_root / "packages" / "strategy_runner" / "child.py"
    with tempfile.TemporaryDirectory(prefix="strategy-runner-") as workdir:
        try:
            completed = subprocess.run(
                [sys.executable, str(child)],
                input=canonical_json(payload).encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=workdir,
                env={},
                close_fds=True,
                timeout=limits.wall_time_seconds,
                check=False,
                preexec_fn=(lambda: _limit_process(limits)) if os.name == "posix" else None,
            )
        except subprocess.TimeoutExpired as exc:
            raise PluginIsolationError("PLUGIN_TIMEOUT") from exc
    if len(completed.stdout) > limits.max_output_bytes or len(completed.stderr) > limits.max_output_bytes:
        raise PluginIsolationError("PLUGIN_OUTPUT_LIMIT_EXCEEDED")
    if completed.returncode != 0:
        diagnostic = completed.stderr.decode("utf-8", errors="replace").strip()[:4_000]
        raise PluginIsolationError(f"PLUGIN_ISOLATION_FAILURE: {diagnostic or 'runner exited non-zero'}")
    try:
        parsed = json.loads(completed.stdout)
        return StrategyEvaluationV1.model_validate(parsed)
    except (ValueError, TypeError) as exc:
        raise PluginIsolationError("PLUGIN_INVALID_OUTPUT") from exc
