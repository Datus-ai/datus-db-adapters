# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Pure-Pydantic SnowflakeConfig tests — no live Snowflake required."""

from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from snowflake.connector.errors import ProgrammingError

from datus_snowflake import SnowflakeConfig, SnowflakeConnector
from datus_snowflake.connector import _handle_snowflake_exception


def _private_key_material(encryption_password: bytes | None = None) -> tuple[str, bytes]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    encryption_algorithm = (
        serialization.NoEncryption()
        if encryption_password is None
        else serialization.BestAvailableEncryption(encryption_password)
    )
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=encryption_algorithm,
    ).decode()
    der = private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem, der


def test_snowflake_execution_error_logs_original_exception(caplog):
    connector = object.__new__(SnowflakeConnector)
    connector.connection = MagicMock()
    error = ProgrammingError("syntax error")
    error.raw_msg = None
    cursor = connector.connection.cursor.return_value.__enter__.return_value
    cursor.execute.side_effect = error
    sql = "SELECT " + ("x" * 220) + " FROM sensitive_tail"

    result = connector.execute_pandas(sql)

    assert result.success is False
    assert "syntax error" in result.error
    assert "Error details: None" not in result.error
    assert "Snowflake SQL execution failed; sql_preview='SELECT " in caplog.text
    assert f"sql_chars={len(sql)}" in caplog.text
    assert "sensitive_tail" not in caplog.text
    matching_records = [record for record in caplog.records if "Snowflake SQL execution failed" in record.message]
    assert len(matching_records) == 1
    assert matching_records[0].exc_info[2] is error.__traceback__


def test_snowflake_unclassified_error_uses_normalized_raw_message():
    error = RuntimeError("generic fallback")
    error.raw_msg = "driver raw message"

    result = _handle_snowflake_exception(error)

    assert "driver raw message" in str(result)
    assert "generic fallback" not in str(result)


def test_config_requires_password_or_key():
    with pytest.raises(ValueError, match="exactly one of `password` or `private_key_file`"):
        SnowflakeConfig(account="a", username="u", warehouse="w")


def test_config_rejects_both_password_and_key(tmp_path):
    key_file = tmp_path / "key.p8"
    key_file.write_text("dummy")
    with pytest.raises(ValueError, match="exactly one of `password` or `private_key_file`"):
        SnowflakeConfig(
            account="a",
            username="u",
            warehouse="w",
            password="p",
            private_key_file=str(key_file),
        )


def test_config_accepts_password_only():
    cfg = SnowflakeConfig(account="a", username="u", warehouse="w", password="p")
    assert cfg.password.get_secret_value() == "p"
    assert cfg.private_key_file is None


def test_config_accepts_key_pair_only(tmp_path):
    key_file = tmp_path / "key.p8"
    key_file.write_text("dummy")
    cfg = SnowflakeConfig(account="a", username="u", warehouse="w", private_key_file=str(key_file))
    assert cfg.private_key_file == str(key_file)
    assert cfg.password is None


def test_config_accepts_private_key_only():
    cfg = SnowflakeConfig(account="a", username="u", warehouse="w", private_key="pem")
    assert cfg.private_key.get_secret_value() == "pem"
    assert cfg.private_key_file is None
    assert cfg.password is None


def test_config_private_key_can_override_other_credentials(tmp_path):
    key_file = tmp_path / "key.p8"
    key_file.write_text("dummy")

    cfg = SnowflakeConfig(
        account="a",
        username="u",
        warehouse="w",
        password="p",
        private_key_file=str(key_file),
        private_key="pem",
    )

    assert cfg.private_key.get_secret_value() == "pem"
    assert cfg.private_key_file == str(key_file)
    assert cfg.password.get_secret_value() == "p"


def test_config_masks_secret_repr():
    """Secrets must not leak through repr/str of the config object."""
    cfg = SnowflakeConfig(account="a", username="u", warehouse="w", password="topsecret")
    assert "topsecret" not in repr(cfg)
    assert "topsecret" not in str(cfg)

    cfg_pem = SnowflakeConfig(account="a", username="u", warehouse="w", private_key="pemsecret")
    assert "pemsecret" not in repr(cfg_pem)
    assert "pemsecret" not in str(cfg_pem)


def test_config_coerces_numeric_credentials(tmp_path):
    """YAML parses unquoted numeric secrets as int; they must coerce to str."""
    cfg_pwd = SnowflakeConfig(account="a", username="u", warehouse="w", password=1234)
    assert cfg_pwd.password.get_secret_value() == "1234"

    key_file = tmp_path / "key.p8"
    key_file.write_text("dummy")
    cfg_key = SnowflakeConfig(
        account="a", username="u", warehouse="w", private_key_file=str(key_file), private_key_file_pwd=5678
    )
    assert cfg_key.private_key_file_pwd.get_secret_value() == "5678"


def test_connector_passes_role_to_snowflake_connect():
    cfg = SnowflakeConfig(
        account="a",
        username="u",
        warehouse="w",
        password="p",
        role="ANALYST",
    )

    with patch("datus_snowflake.connector.Connect") as connect:
        SnowflakeConnector(cfg)

    assert connect.call_args.kwargs["role"] == "ANALYST"


def test_connector_uses_password_auth_kwargs():
    cfg = SnowflakeConfig(account="a", username="u", warehouse="w", password="p")

    with patch("datus_snowflake.connector.Connect") as connect:
        SnowflakeConnector(cfg)

    kwargs = connect.call_args.kwargs
    assert kwargs["password"] == "p"
    assert "authenticator" not in kwargs
    assert "private_key_file" not in kwargs


def test_connector_uses_key_pair_auth_kwargs(tmp_path):
    key_file = tmp_path / "key.p8"
    key_file.write_text("dummy")
    cfg = SnowflakeConfig(
        account="a",
        username="u",
        warehouse="w",
        private_key_file=str(key_file),
        private_key_file_pwd="secret",
        role="ANALYST",
    )

    with patch("datus_snowflake.connector.Connect") as connect:
        SnowflakeConnector(cfg)

    kwargs = connect.call_args.kwargs
    assert kwargs["authenticator"] == "SNOWFLAKE_JWT"
    assert kwargs["private_key_file"] == str(key_file)
    assert kwargs["private_key_file_pwd"] == "secret"
    assert kwargs["role"] == "ANALYST"
    assert "password" not in kwargs


def test_connector_uses_private_key_auth_kwargs(tmp_path):
    key_file = tmp_path / "key.p8"
    key_file.write_text("dummy")
    pem, der = _private_key_material()

    cfg = SnowflakeConfig(
        account="a",
        username="u",
        warehouse="w",
        password="ignored-password",
        private_key_file=str(key_file),
        private_key=pem.replace("\n", "\\n"),
        private_key_file_pwd="unused-file-passphrase",
        role="ANALYST",
    )

    with patch("datus_snowflake.connector.Connect") as connect:
        SnowflakeConnector(cfg)

    kwargs = connect.call_args.kwargs
    assert kwargs["authenticator"] == "SNOWFLAKE_JWT"
    assert kwargs["private_key"] == der
    assert kwargs["role"] == "ANALYST"
    assert "password" not in kwargs
    assert "private_key_file" not in kwargs
    assert "private_key_file_pwd" not in kwargs


def test_connector_uses_encrypted_private_key_auth_kwargs():
    pem, der = _private_key_material(b"secret")
    cfg = SnowflakeConfig(
        account="a",
        username="u",
        warehouse="w",
        private_key=pem,
        private_key_file_pwd="secret",
    )

    with patch("datus_snowflake.connector.Connect") as connect:
        SnowflakeConnector(cfg)

    kwargs = connect.call_args.kwargs
    assert kwargs["authenticator"] == "SNOWFLAKE_JWT"
    assert kwargs["private_key"] == der
    assert "password" not in kwargs
    assert "private_key_file" not in kwargs
