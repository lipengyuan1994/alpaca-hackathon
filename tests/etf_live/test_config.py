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


def test_t08_repository_config_is_tecl_only_and_observe_only():
    config = load_config(Path("configs/live/t08_tecl.yaml"))
    assert config.mode == "observe"
    assert config.account_id == "pending"
    assert config.strategy_id == "T08"
    assert config.symbols == ("TECL",)
    assert config.signal_symbols == ("XLK",)


def test_t08_strategy_defaults_are_safe_when_constructed_directly():
    config = LiveConfig(strategy_id="T08", account_id="acct", strategy_config_hash="sha256:" + "b" * 64)
    assert config.symbols == ("TECL",)
    assert config.signal_symbols == ("XLK",)
    assert str(config.state_path).endswith("t08_tecl/state.db")
    assert config.initial_cash == 2000
