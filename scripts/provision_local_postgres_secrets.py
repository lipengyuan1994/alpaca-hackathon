#!/usr/bin/env python3
"""Generate the external, non-overwritable file secrets for local PostgreSQL."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from packages.local_postgres_secrets import (
    LocalPostgresSecretProvisioningError,
    provision_local_postgres_secrets,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--secrets-dir",
        type=Path,
        required=True,
        help="External secret directory; never place it inside the repository.",
    )
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    try:
        created = provision_local_postgres_secrets(arguments.secrets_dir)
    except LocalPostgresSecretProvisioningError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print("LOCAL_POSTGRES_SECRETS_PROVISIONED")
    for path in created:
        print(f"- {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
