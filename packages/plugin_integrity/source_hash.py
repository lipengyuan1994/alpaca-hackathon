"""Canonical hashing for repository-owned strategy plug-in packages."""

from __future__ import annotations

import hashlib
from pathlib import Path

from packages.contracts.canonical import canonical_hash


class PluginIntegrityError(ValueError):
    """The configured plug-in source cannot be safely identified and hashed."""


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def calculate_plugin_content_hash(
    entrypoint: str,
    *,
    repository_root: Path = _REPOSITORY_ROOT,
) -> str:
    """Hash one registered Python package from disk without importing it."""
    if entrypoint.count(":") != 1:
        raise PluginIntegrityError("REGISTRY_ENTRYPOINT_INVALID")
    module_name, class_name = entrypoint.split(":", 1)
    module_parts = module_name.split(".")
    if (
        len(module_parts) < 3
        or module_parts[0] != "strategy_plugins"
        or not class_name.isidentifier()
    ):
        raise PluginIntegrityError("REGISTRY_ENTRYPOINT_INVALID")

    root = repository_root.resolve()
    strategy_root = (root / "strategy_plugins").resolve()
    package_root = (strategy_root / module_parts[1]).resolve()
    module_path = (root.joinpath(*module_parts).with_suffix(".py")).resolve()
    try:
        package_root.relative_to(strategy_root)
        module_path.relative_to(package_root)
    except ValueError as exc:
        raise PluginIntegrityError("REGISTRY_ENTRYPOINT_OUTSIDE_STRATEGY_ROOT") from exc
    if not module_path.is_file() or not package_root.is_dir():
        raise PluginIntegrityError("REGISTRY_ENTRYPOINT_NOT_FOUND")

    files = sorted(
        path for path in package_root.rglob("*.py") if "__pycache__" not in path.parts
    )
    if not files:
        raise PluginIntegrityError("REGISTRY_PACKAGE_EMPTY")
    material: list[dict[str, str]] = []
    for path in files:
        if path.is_symlink():
            raise PluginIntegrityError("REGISTRY_PACKAGE_SYMLINK_FORBIDDEN")
        material.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return canonical_hash({"entrypoint": entrypoint, "source_files": material})
