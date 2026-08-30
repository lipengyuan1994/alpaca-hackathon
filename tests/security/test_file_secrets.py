from __future__ import annotations

from pathlib import Path

import pytest

from packages.alpaca_execution_mcp import AlpacaPaperExecutionAdapter, PaperEndpointError
from packages.runtime_secrets import (
    SecretConfigurationError,
    require_file_secret,
    require_yaml_file_secret,
)


def test_file_secret_requires_a_mounted_path_and_does_not_accept_plain_environment_value() -> None:
    with pytest.raises(SecretConfigurationError, match="MODEL_API_KEY_FILE_REQUIRED"):
        require_file_secret("MODEL_API_KEY", environ={"MODEL_API_KEY": "not-accepted"})


def test_file_secret_reads_only_an_allowed_regular_file(tmp_path: Path) -> None:
    secret_path = tmp_path / "model_key"
    secret_path.write_text("test-token\n", encoding="utf-8")
    assert require_file_secret(
        "MODEL_API_KEY",
        environ={"MODEL_API_KEY_FILE": str(secret_path)},
        allowed_roots=(tmp_path,),
    ) == "test-token"


def test_file_secret_rejects_a_path_outside_its_secret_root(tmp_path: Path) -> None:
    secret_path = tmp_path / "model_key"
    secret_path.write_text("test-token", encoding="utf-8")
    with pytest.raises(SecretConfigurationError, match="MODEL_API_KEY_FILE_OUTSIDE_ALLOWED_ROOT"):
        require_file_secret(
            "MODEL_API_KEY",
            environ={"MODEL_API_KEY_FILE": str(secret_path)},
            allowed_roots=(tmp_path / "different",),
        )


def test_yaml_file_secret_reads_only_the_fixed_allowlisted_key(tmp_path: Path) -> None:
    secret_path = tmp_path / "model_key.yaml"
    secret_path.write_text("gemini: fixture-gemini-token\nother: ignored\n", encoding="utf-8")

    assert require_yaml_file_secret(
        "MODEL_API_KEY",
        key_path=("gemini",),
        environ={"MODEL_API_KEY_FILE": str(secret_path)},
        allowed_roots=(tmp_path,),
    ) == "fixture-gemini-token"


def test_yaml_file_secret_rejects_missing_or_non_string_key(tmp_path: Path) -> None:
    secret_path = tmp_path / "model_key.yaml"
    secret_path.write_text("gemini: 123\n", encoding="utf-8")

    with pytest.raises(SecretConfigurationError, match="MODEL_API_KEY_FILE_YAML_VALUE_INVALID"):
        require_yaml_file_secret(
            "MODEL_API_KEY",
            key_path=("gemini",),
            environ={"MODEL_API_KEY_FILE": str(secret_path)},
            allowed_roots=(tmp_path,),
        )
    with pytest.raises(SecretConfigurationError, match="MODEL_API_KEY_FILE_YAML_KEY_MISSING"):
        require_yaml_file_secret(
            "MODEL_API_KEY",
            key_path=("other",),
            environ={"MODEL_API_KEY_FILE": str(secret_path)},
            allowed_roots=(tmp_path,),
        )


def test_paper_adapter_rejects_plain_environment_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPER_ALPACA_API_KEY", "not-accepted")
    monkeypatch.setenv("PAPER_ALPACA_API_SECRET", "not-accepted")
    monkeypatch.setenv("PAPER_ACCOUNT_ID", "not-accepted")
    monkeypatch.delenv("PAPER_ALPACA_API_KEY_FILE", raising=False)
    monkeypatch.delenv("PAPER_ALPACA_API_SECRET_FILE", raising=False)
    monkeypatch.delenv("PAPER_ACCOUNT_ID_FILE", raising=False)
    with pytest.raises(PaperEndpointError, match="PAPER_EXECUTION_CREDENTIALS_MISSING"):
        AlpacaPaperExecutionAdapter.from_environment()
