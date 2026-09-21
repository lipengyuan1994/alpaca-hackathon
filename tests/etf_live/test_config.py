from pathlib import Path

import pytest

from packages.etf_live.config import LiveConfig, load_config


def test_live_config_rejects_wrong_universe():
    with pytest.raises(ValueError, match="UNIVERSE_INVALID"):
        LiveConfig(account_id="acct", strategy_config_hash="sha256:" + "a" * 64, symbols=("QQQ",))


def test_repository_config_is_observe_only_until_account_is_provided():
    config = load_config(Path("configs/live/l11_tqqq_soxl.yaml"))
    assert config.mode == "observe"
    assert config.account_id == "pending"
    assert config.symbols == ("TQQQ", "SOXL")
    assert config.strategy_config_hash == "sha256:23e2652143a651e22d19789338cd2b954545c349232433bfda33bd4e29749a70"
