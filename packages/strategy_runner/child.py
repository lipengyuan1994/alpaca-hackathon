"""Child process for one canonical-JSON authorized plug-in evaluation."""

from __future__ import annotations

import builtins
import importlib
import io
import os
import socket
import sys
from pathlib import Path
from typing import Any

# The runner is invoked by absolute filename from an empty environment. Restore
# only the repository import root for this fixed, repository-owned child.
_SOURCE_ROOT = str(Path(__file__).resolve().parents[2])
if _SOURCE_ROOT not in sys.path:
    sys.path.insert(0, _SOURCE_ROOT)
os.environ.clear()

from pydantic import BaseModel

from packages.contracts.canonical import canonical_json
from packages.contracts.models import (
    DataRequirementsV1,
    StrategyEvaluationV1,
    StrategyMetadataV1,
)
from packages.plugin_integrity import calculate_plugin_content_hash
from packages.strategy_runner.models import PluginRequest, PluginResponse


def _deny_network(*_: Any, **__: Any) -> None:
    raise PermissionError("PLUGIN_NETWORK_DENIED")


def _install_network_denial() -> None:
    socket.socket = _deny_network  # type: ignore[assignment]
    socket.create_connection = _deny_network  # type: ignore[assignment]
    socket.getaddrinfo = _deny_network  # type: ignore[assignment]


def _deny_filesystem(*_: Any, **__: Any) -> None:
    raise PermissionError("PLUGIN_FILESYSTEM_DENIED")


def _install_filesystem_denial() -> None:
    # Imports have completed. Metadata, requirements, and evaluation receive only
    # the serialized request and cannot open additional files.
    builtins.open = _deny_filesystem  # type: ignore[assignment]
    io.open = _deny_filesystem  # type: ignore[assignment]


def _load_plugin_class(entrypoint: str) -> type[Any]:
    if entrypoint.count(":") != 1:
        raise ValueError("PLUGIN_ENTRYPOINT_INVALID")
    module_name, class_name = entrypoint.split(":", 1)
    module = importlib.import_module(module_name)
    plugin_class = getattr(module, class_name)
    if not isinstance(plugin_class, type):
        raise TypeError("PLUGIN_ENTRYPOINT_NOT_CLASS")
    return plugin_class


def _result_payload(result: object) -> dict[str, object]:
    if isinstance(result, BaseModel):
        return result.model_dump(mode="json")
    if isinstance(result, dict):
        return dict(result)
    raise TypeError("PLUGIN_RESULT_NOT_MAPPING")


def main() -> None:
    request = PluginRequest.model_validate_json(sys.stdin.buffer.read())
    if request.config.config_hash != request.authorization.config_hash:
        raise ValueError("PLUGIN_CONFIG_BINDING_MISMATCH")

    # Network is denied before import. Filesystem reads remain available only
    # while Python imports the already registry-pinned repository package.
    _install_network_denial()
    current_content_hash = calculate_plugin_content_hash(
        request.authorization.entrypoint,
        repository_root=Path(_SOURCE_ROOT),
    )
    if current_content_hash != request.authorization.content_hash:
        raise ValueError("PLUGIN_SOURCE_CHANGED_AFTER_AUTHORIZATION")
    plugin_class = _load_plugin_class(request.authorization.entrypoint)
    _install_filesystem_denial()
    plugin = plugin_class()

    metadata = StrategyMetadataV1.model_validate(plugin.metadata)
    if metadata != request.authorization.expected_metadata:
        raise ValueError("PLUGIN_METADATA_MISMATCH")
    data_requirements = DataRequirementsV1.model_validate(
        plugin.data_requirements(request.config)
    )
    if data_requirements != request.authorization.expected_data_requirements:
        raise ValueError("PLUGIN_DATA_REQUIREMENTS_MISMATCH")

    result = _result_payload(plugin.evaluate(request.context, request.config))
    # The source digest is host-owned. Ignore any value the plug-in returned,
    # inject the externally computed digest, and derive a fresh evaluation hash.
    result["plugin_content_hash"] = request.authorization.content_hash
    result.pop("evaluation_hash", None)
    evaluation = StrategyEvaluationV1.model_validate(result)
    response = PluginResponse(
        metadata=metadata,
        data_requirements=data_requirements,
        evaluation=evaluation,
    )
    sys.stdout.write(canonical_json(response))


if __name__ == "__main__":
    main()
