from __future__ import annotations

from pathlib import Path

import pytest

from packages.research_data.cli import _client_from_environment


def test_read_only_collector_loads_fixed_alpaca_bundle(tmp_path: Path) -> None:
    bundle = tmp_path / "alpaca" / "alpaca_api_key.yaml"
    bundle.parent.mkdir()
    bundle.write_text(
        "paper_alpaca_api_key: fixture-key\npaper_alpaca_api_secret: fixture-secret\n",
        encoding="utf-8",
    )

    client = _client_from_environment({"REGIMESWITCH_SECRETS_DIR": str(tmp_path)})

    assert client.headers == {
        "APCA-API-KEY-ID": "fixture-key",
        "APCA-API-SECRET-KEY": "fixture-secret",
    }


def test_read_only_collector_refuses_missing_fixed_bundle(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="ALPACA_READ_ONLY_CREDENTIALS_UNAVAILABLE"):
        _client_from_environment({"REGIMESWITCH_SECRETS_DIR": str(tmp_path)})
