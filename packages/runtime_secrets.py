"""Read deployment secrets from explicitly mounted files only.

Runtime code never accepts a secret value through a normal environment
variable.  Compose mounts each allowed secret under ``/run/secrets`` and the
configuration passes only the corresponding ``*_FILE`` path.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

import yaml

DEFAULT_SECRET_ROOT: Final = Path("/run/secrets")
_MAX_SECRET_FILE_BYTES: Final = 65_536


class SecretConfigurationError(RuntimeError):
    """A required file-mounted secret is missing, unsafe, or empty."""


def _read_file_secret_text(
    name: str,
    *,
    environ: Mapping[str, str] | None = None,
    allowed_roots: Sequence[Path] = (DEFAULT_SECRET_ROOT,),
) -> str:
    """Return an unparsed text payload from an allowlisted mounted secret file."""

    values = os.environ if environ is None else environ
    configured_path = values.get(f"{name}_FILE")
    if not configured_path:
        raise SecretConfigurationError(f"{name}_FILE_REQUIRED")
    candidate = Path(configured_path)
    if not candidate.is_absolute():
        raise SecretConfigurationError(f"{name}_FILE_NOT_ABSOLUTE")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise SecretConfigurationError(f"{name}_FILE_UNAVAILABLE") from exc
    resolved_roots = tuple(root.resolve(strict=False) for root in allowed_roots)
    if not any(resolved.is_relative_to(root) for root in resolved_roots):
        raise SecretConfigurationError(f"{name}_FILE_OUTSIDE_ALLOWED_ROOT")
    if not resolved.is_file():
        raise SecretConfigurationError(f"{name}_FILE_NOT_REGULAR")
    try:
        payload = resolved.read_bytes()
    except OSError as exc:
        raise SecretConfigurationError(f"{name}_FILE_UNREADABLE") from exc
    if len(payload) > _MAX_SECRET_FILE_BYTES:
        raise SecretConfigurationError(f"{name}_FILE_TOO_LARGE")
    try:
        value = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SecretConfigurationError(f"{name}_FILE_NOT_UTF8") from exc
    if "\x00" in value:
        raise SecretConfigurationError(f"{name}_FILE_INVALID")
    return value


def require_file_secret(
    name: str,
    *,
    environ: Mapping[str, str] | None = None,
    allowed_roots: Sequence[Path] = (DEFAULT_SECRET_ROOT,),
) -> str:
    """Return one nonempty raw secret from ``<name>_FILE`` without exposing it.

    The default accepts only paths mounted by Compose. Tests may pass a
    temporary allowed root explicitly; production callers should retain the
    default rather than permitting arbitrary host paths.
    """

    value = _read_file_secret_text(name, environ=environ, allowed_roots=allowed_roots).strip()
    if not value or "\x00" in value:
        raise SecretConfigurationError(f"{name}_FILE_INVALID")
    return value


def require_yaml_file_secret(
    name: str,
    *,
    key_path: tuple[str, ...],
    environ: Mapping[str, str] | None = None,
    allowed_roots: Sequence[Path] = (DEFAULT_SECRET_ROOT,),
) -> str:
    """Read one fixed scalar from an allowlisted YAML file mounted by Compose.

    ``key_path`` is owned by code, never supplied through the environment, so
    a deployment cannot redirect a role to a different value in its bundle.
    """

    if not key_path or any(not key for key in key_path):
        raise ValueError("YAML_SECRET_KEY_PATH_INVALID")
    payload = _read_file_secret_text(name, environ=environ, allowed_roots=allowed_roots)
    try:
        document = yaml.safe_load(payload)
    except yaml.YAMLError as exc:
        raise SecretConfigurationError(f"{name}_FILE_YAML_INVALID") from exc

    value: Any = document
    for key in key_path:
        if not isinstance(value, dict) or key not in value:
            raise SecretConfigurationError(f"{name}_FILE_YAML_KEY_MISSING")
        value = value[key]
    if not isinstance(value, str):
        raise SecretConfigurationError(f"{name}_FILE_YAML_VALUE_INVALID")
    secret = value.strip()
    if not secret or "\x00" in secret:
        raise SecretConfigurationError(f"{name}_FILE_INVALID")
    return secret
