"""Child process for one canonical-JSON plug-in evaluation.

It accepts no secrets, starts from a cleared environment, blocks socket use after
imports, and prints exactly one validated response to stdout.
"""

from __future__ import annotations

import builtins
import importlib
import io
import os
import socket
import sys
from pathlib import Path
from typing import Any

# The runner is invoked by absolute filename from an empty environment.  Restore
# only the repository import root for this fixed, repository-owned child.
_SOURCE_ROOT = str(Path(__file__).resolve().parents[2])
if _SOURCE_ROOT not in sys.path:
    sys.path.insert(0, _SOURCE_ROOT)
os.environ.clear()

from pydantic import BaseModel, ConfigDict

from packages.contracts.canonical import canonical_json
from packages.contracts.models import StrategyConfigV1, StrategyContextV1, StrategyEvaluationV1


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entrypoint: str
    context: StrategyContextV1
    config: StrategyConfigV1


def _deny_network(*_: Any, **__: Any) -> None:
    raise PermissionError("PLUGIN_NETWORK_DENIED")


def _install_network_denial() -> None:
    socket.socket = _deny_network  # type: ignore[assignment]
    socket.create_connection = _deny_network  # type: ignore[assignment]
    socket.getaddrinfo = _deny_network  # type: ignore[assignment]


def _deny_filesystem(*_: Any, **__: Any) -> None:
    raise PermissionError("PLUGIN_FILESYSTEM_DENIED")


def _install_filesystem_denial() -> None:
    # Imports finish before this point. Evaluation gets only serialized inputs.
    builtins.open = _deny_filesystem  # type: ignore[assignment]
    io.open = _deny_filesystem  # type: ignore[assignment]


def _load_plugin(entrypoint: str) -> object:
    if entrypoint.count(":") != 1:
        raise ValueError("invalid registered entrypoint")
    module_name, class_name = entrypoint.split(":", 1)
    module = importlib.import_module(module_name)
    return getattr(module, class_name)()


def main() -> None:
    raw = sys.stdin.buffer.read()
    request = Request.model_validate_json(raw)
    plugin = _load_plugin(request.entrypoint)
    _install_network_denial()
    _install_filesystem_denial()
    result = plugin.evaluate(request.context, request.config)  # type: ignore[attr-defined]
    validated = StrategyEvaluationV1.model_validate(result)
    sys.stdout.write(canonical_json(validated))


if __name__ == "__main__":
    main()
