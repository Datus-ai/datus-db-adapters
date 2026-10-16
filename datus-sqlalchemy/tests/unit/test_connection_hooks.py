import sqlite3
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text

from datus_sqlalchemy import SQLAlchemyConnector


class Connector(SQLAlchemyConnector):
    def get_databases(self, catalog_name="", include_sys=False):
        return []

    def do_switch_context(self, conn, catalog_name="", database_name="", schema_name=""):
        pass


def test_dynamic_credentials_are_per_physical_connection_and_existing_sessions_survive():
    """Elapsed token lifetime must not force a working pooled session to log in again."""
    passwords = iter(["token-at-start", "token-after-expiry"])
    connector = Connector("sqlite://")
    connector.configure_connection(connection_parameters=lambda: {"password": next(passwords)})
    engine = connector._ensure_engine()
    observed = []

    def connect(*args, **kwargs):
        observed.append(kwargs.pop("password"))
        return sqlite3.connect(*args, **kwargs)

    with patch.object(engine.dialect, "connect", side_effect=connect):
        for _ in range(2):
            with connector._conn() as conn:
                assert conn.scalar(text("SELECT 1")) == 1
        assert observed == ["token-at-start"]
        engine.dispose()
        with connector._conn() as conn:
            assert conn.scalar(text("SELECT 1")) == 1
        assert observed == ["token-at-start", "token-after-expiry"]
    connector.close()


def test_secret_version_change_discards_pool_before_checkout():
    state = ["v1"]
    connector = Connector("sqlite://")
    connector.configure_connection(credential_version=lambda force: state[0])
    first = connector._ensure_engine()
    assert connector._ensure_engine() is first
    state[0] = "v2"
    with patch.object(first, "dispose", wraps=first.dispose) as dispose:
        second = connector._ensure_engine()
        dispose.assert_called_once()
    assert second is not first
    connector.close()


def test_replacing_identity_hooks_discards_pool():
    connector = Connector("sqlite://")
    previous_cleanup = MagicMock()
    connector.configure_connection(connection_cleanup=previous_cleanup)
    first = connector._ensure_engine()
    with patch.object(first, "dispose", wraps=first.dispose) as dispose:
        connector.configure_connection(connection_parameters=lambda: {"password": "another-identity"})
        dispose.assert_called_once()
    previous_cleanup.assert_called_once_with()
    assert connector._ensure_engine() is not first
    connector.close()


def test_close_releases_provider_resources_even_after_pool_disposal_failure(caplog):
    connector = Connector("sqlite://")
    cleanup = MagicMock(side_effect=RuntimeError("sensitive provider data"))
    connector.configure_connection(connection_cleanup=cleanup)
    engine = MagicMock()
    engine.dispose.side_effect = RuntimeError("sensitive engine data")
    connector.engine = engine
    connector.close()
    engine.dispose.assert_called_once_with()
    cleanup.assert_called_once_with()
    assert connector.engine is None
    assert "sensitive" not in caplog.text


def test_connection_failure_keeps_driver_code_but_redacts_password_and_traceback(caplog):
    connector = Connector("sqlite://")
    connector.configure_connection(connection_parameters=lambda: {"password": "sensitive-token"})
    engine = connector._ensure_engine()
    failure = sqlite3.OperationalError("Access denied; sensitive-token")
    with patch.object(engine.dialect, "connect", side_effect=failure):
        result = connector.execute_query("SELECT 1")
    assert not result.success
    assert "Access denied" in str(result.error)
    assert "sensitive-token" not in str(result.error)
    assert "sensitive-token" not in caplog.text
    connector.close()


def test_statement_failure_is_not_replayed():
    from sqlalchemy.exc import OperationalError

    connector = Connector("sqlite://")
    conn = MagicMock()
    conn.execute.side_effect = OperationalError("INSERT INTO t VALUES (1)", {}, RuntimeError("connection lost"))
    engine = MagicMock()
    engine.connect.return_value = conn
    with patch("datus_sqlalchemy.connector.create_engine", return_value=engine):
        result = connector.execute_insert("INSERT INTO t VALUES (1)")
    assert not result.success
    conn.execute.assert_called_once()
    conn.rollback.assert_called_once()


def test_failed_version_refresh_blocks_checkout_without_losing_current_state():
    connector = Connector("sqlite://")
    provider = MagicMock(side_effect=["v1", RuntimeError("Secret unavailable")])
    connector.configure_connection(credential_version=provider)
    first = connector._ensure_engine()
    with pytest.raises(RuntimeError, match="Secret unavailable"):
        connector._ensure_engine()
    assert connector.engine is first
    connector.close()


def test_checkout_role_validation_replaces_demoted_connection_before_issuing_write(tmp_path):
    from sqlalchemy import event
    from sqlalchemy.exc import DisconnectionError

    connector = Connector(f"sqlite:///{tmp_path / 'failover.db'}")
    checked = []
    demoted = [False]

    def validate(connection):
        checked.append(connection)
        if demoted[0] and connection is checked[0]:
            raise DisconnectionError("former writer is now a reader")

    connector.configure_connection(connection_validator=validate)
    engine = connector._ensure_engine()
    writes = []

    @event.listens_for(engine, "before_cursor_execute")
    def record_statement(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT"):
            writes.append(statement)

    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE values_after_failover (value INTEGER)"))
        demoted[0] = True
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO values_after_failover VALUES (42)"))
        with engine.connect() as connection:
            assert connection.execute(text("SELECT value FROM values_after_failover")).all() == [(42,)]
        assert checked[0] is checked[1]
        assert checked[2] is not checked[0]
        assert writes == ["INSERT INTO values_after_failover VALUES (42)"]
    finally:
        connector.close()
