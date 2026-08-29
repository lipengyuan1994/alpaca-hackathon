"""Operator CLI only validates an explicit signed command payload in this skeleton."""

from __future__ import annotations

import argparse
from pathlib import Path

from packages.contracts.models import ControlCommandV1


def main() -> None:
    parser = argparse.ArgumentParser(description="Private control-command validator; no direct database DML.")
    parser.add_argument("command", type=Path, help="canonical ControlCommandV1 JSON")
    args = parser.parse_args()
    command = ControlCommandV1.model_validate_json(args.command.read_text(encoding="utf-8"))
    print(command.command_hash)


if __name__ == "__main__":
    main()
