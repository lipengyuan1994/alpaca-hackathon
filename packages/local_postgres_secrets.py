"""Safe generation of the three local Compose PostgreSQL secret files."""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

POSTGRES_ADMIN_USER = "regimeswitch_postgres_admin"
POSTGRES_DATABASE = "regimeswitch"
POSTGRES_EXECUTION_ROLE = "regimeswitch_execution"
POSTGRES_SERVICE_HOST = "postgres"
POSTGRES_SERVICE_PORT = 5432

_DIRECTORY_MODE = 0o700
_FILE_MODE = 0o600
_PASSWORD_BYTES = 32


class LocalPostgresSecretProvisioningError(RuntimeError):
    """Raised when a local database-secret directory is unsafe to modify."""


def execution_database_url(execution_password: str) -> str:
    """Return the internal-only DSN consumed by the execution container."""
    return (
        f"postgresql://{POSTGRES_EXECUTION_ROLE}:{execution_password}"
        f"@{POSTGRES_SERVICE_HOST}:{POSTGRES_SERVICE_PORT}/{POSTGRES_DATABASE}?sslmode=disable"
    )


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=_DIRECTORY_MODE)
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise LocalPostgresSecretProvisioningError("LOCAL_POSTGRES_SECRET_DIRECTORY_INVALID")
    os.chmod(path, _DIRECTORY_MODE)


def _path_exists(path: Path) -> bool:
    return os.path.lexists(path)


def _write_new_secret(path: Path, value: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, _FILE_MODE)
    handle = None
    try:
        os.fchmod(descriptor, _FILE_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as opened_handle:
            handle = opened_handle
            opened_handle.write(f"{value}\n")
            opened_handle.flush()
            os.fsync(opened_handle.fileno())
    except BaseException:
        if handle is None:
            os.close(descriptor)
        raise


def provision_local_postgres_secrets(secrets_directory: Path) -> tuple[Path, ...]:
    """Create non-overwritable local database secrets outside the repository.

    The returned paths are relative to ``secrets_directory``. Secret values are
    deliberately never returned or logged.
    """
    root = secrets_directory.expanduser()
    _ensure_private_directory(root)
    postgres_directory = root / "postgres"
    _ensure_private_directory(postgres_directory)
    targets = (
        postgres_directory / "bootstrap_password",
        postgres_directory / "execution_password",
        root / "execution_database_url",
    )
    if any(_path_exists(path) for path in targets):
        raise LocalPostgresSecretProvisioningError("LOCAL_POSTGRES_SECRET_ALREADY_EXISTS")

    bootstrap_password = secrets.token_hex(_PASSWORD_BYTES)
    execution_password = secrets.token_hex(_PASSWORD_BYTES)
    values = (
        bootstrap_password,
        execution_password,
        execution_database_url(execution_password),
    )
    created: list[Path] = []
    try:
        for path, value in zip(targets, values, strict=True):
            _write_new_secret(path, value)
            created.append(path)
    except BaseException:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise
    return tuple(path.relative_to(root) for path in created)
