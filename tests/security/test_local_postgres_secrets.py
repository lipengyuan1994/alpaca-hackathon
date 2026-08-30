from __future__ import annotations

import stat
from pathlib import Path

import pytest

from packages.local_postgres_secrets import (
    LocalPostgresSecretProvisioningError,
    provision_local_postgres_secrets,
)


def test_local_postgres_secret_provisioning_creates_private_non_overwritable_files(
    tmp_path: Path,
) -> None:
    secrets_directory = tmp_path / "external-secrets"

    created = provision_local_postgres_secrets(secrets_directory)

    assert created == (
        Path("postgres/bootstrap_password"),
        Path("postgres/execution_password"),
        Path("execution_database_url"),
    )
    assert stat.S_IMODE(secrets_directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((secrets_directory / "postgres").stat().st_mode) == 0o700
    for relative_path in created:
        assert (secrets_directory / relative_path).is_file()
        assert stat.S_IMODE((secrets_directory / relative_path).stat().st_mode) == 0o600

    with pytest.raises(
        LocalPostgresSecretProvisioningError,
        match="LOCAL_POSTGRES_SECRET_ALREADY_EXISTS",
    ):
        provision_local_postgres_secrets(secrets_directory)
