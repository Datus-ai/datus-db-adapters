# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from unittest.mock import MagicMock, patch

import pytest
from redshift_connector.error import (
    DatabaseError,
    DataError,
    IntegrityError,
    InterfaceError,
    InternalError,
    OperationalError,
    ProgrammingError,
)

from datus_db_core import DatusDbException, ErrorCode
from datus_redshift import RedshiftConfig, RedshiftConnector
from datus_redshift.connector import (
    _handle_redshift_exception,
    _validate_sql_identifier,
)

# Redshift connector calls super().__init__() which needs BaseSqlConnector.
# We mock the init chain to avoid a real connection:
# 1. Patch BaseSqlConnector.__init__ to skip actual base initialization
# 2. Patch redshift_connector.connect to avoid a real connection
_MOCK_BASE = "datus_redshift.connector.BaseSqlConnector.__init__"
_MOCK_CONNECT = "datus_redshift.connector.redshift_connector.connect"


def _make_patches():
    """Create patch context managers for connector initialization."""
    return (
        patch(_MOCK_BASE, return_value=None),
        patch(_MOCK_CONNECT),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def basic_config():
    """Create a basic RedshiftConfig for testing."""
    return RedshiftConfig(
        host="cluster.example.com",
        username="testuser",
        password="testpass",
        database="testdb",
    )


@pytest.fixture
def connector(basic_config):
    """Create a RedshiftConnector with mocked connection.

    Patches remain active for the duration of the test.
    """
    p_base, p_connect = _make_patches()
    with p_base, p_connect as mock_connect:
        mock_connect.return_value = MagicMock()
        conn = RedshiftConnector(basic_config)
        yield conn


# ---------------------------------------------------------------------------
# Connector Initialization
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_connector_initialization_with_config_object(basic_config):
    """Test connector initialization with RedshiftConfig object."""
    p_base, p_connect = _make_patches()
    with p_base, p_connect as mock_connect:
        mock_connect.return_value = MagicMock()
        connector = RedshiftConnector(basic_config)

        assert connector.redshift_config == basic_config
        assert connector.database_name == "testdb"
        assert connector.schema_name == "public"


@pytest.mark.acceptance
def test_connector_initialization_with_dict():
    """Test connector initialization with dict config."""
    config_dict = {
        "host": "cluster.example.com",
        "username": "admin",
        "password": "secret",
        "database": "mydb",
        "schema": "myschema",
    }

    p_base, p_connect = _make_patches()
    with p_base, p_connect as mock_connect:
        mock_connect.return_value = MagicMock()
        connector = RedshiftConnector(config_dict)

        assert connector.database_name == "mydb"
        assert connector.schema_name == "myschema"
        assert isinstance(connector.redshift_config, RedshiftConfig)


def test_connector_initialization_invalid_type():
    """Test that connector raises TypeError for invalid config type."""
    with pytest.raises(TypeError, match="config must be RedshiftConfig or dict"):
        RedshiftConnector("invalid_config")


def test_connector_default_database():
    """Test that database defaults to 'dev' when not specified."""
    config = RedshiftConfig(host="cluster.example.com", username="user", password="pass")

    p_base, p_connect = _make_patches()
    with p_base, p_connect as mock_connect:
        mock_connect.return_value = MagicMock()
        connector = RedshiftConnector(config)

        assert connector.database_name == "dev"


def test_connector_default_schema():
    """Test that schema defaults to 'public' when not specified."""
    config = RedshiftConfig(host="cluster.example.com", username="user", password="pass")

    p_base, p_connect = _make_patches()
    with p_base, p_connect as mock_connect:
        mock_connect.return_value = MagicMock()
        connector = RedshiftConnector(config)

        assert connector.schema_name == "public"


def test_connector_custom_schema():
    """Test that custom schema_name is stored correctly."""
    config = RedshiftConfig(host="cluster.example.com", username="user", password="pass", schema="analytics")

    p_base, p_connect = _make_patches()
    with p_base, p_connect as mock_connect:
        mock_connect.return_value = MagicMock()
        connector = RedshiftConnector(config)

        assert connector.schema_name == "analytics"


def test_connector_connect_params_basic():
    """Test that basic connection parameters are passed correctly."""
    config = RedshiftConfig(
        host="cluster.example.com",
        username="user",
        password="pass",
        port=5439,
        database="mydb",
        ssl=True,
    )

    p_base, p_connect = _make_patches()
    with p_base, p_connect as mock_connect:
        mock_connect.return_value = MagicMock()
        RedshiftConnector(config)

        mock_connect.assert_called_once()
        call_kwargs = mock_connect.call_args[1]
        assert call_kwargs["host"] == "cluster.example.com"
        assert call_kwargs["port"] == 5439
        assert call_kwargs["user"] == "user"
        assert call_kwargs["password"] == "pass"
        assert call_kwargs["database"] == "mydb"
        assert call_kwargs["ssl"] is True
        assert call_kwargs["timeout"] == 30


def test_connector_connect_params_iam():
    """Test that IAM parameters are passed when iam=True."""
    config = RedshiftConfig(
        host="cluster.example.com",
        username="user",
        iam=True,
        cluster_identifier="my-cluster",
        region="us-west-2",
        access_key_id="AKID",
        secret_access_key="SECRET",
    )

    p_base, p_connect = _make_patches()
    with p_base, p_connect as mock_connect:
        mock_connect.return_value = MagicMock()
        RedshiftConnector(config)

        call_kwargs = mock_connect.call_args[1]
        assert call_kwargs["iam"] is True
        assert call_kwargs["cluster_identifier"] == "my-cluster"
        assert call_kwargs["region"] == "us-west-2"
        assert call_kwargs["access_key_id"] == "AKID"
        assert call_kwargs["secret_access_key"] == "SECRET"


def test_connector_connect_params_no_iam():
    """Test that IAM parameters are NOT passed when iam=False."""
    config = RedshiftConfig(host="cluster.example.com", username="user", password="pass")

    p_base, p_connect = _make_patches()
    with p_base, p_connect as mock_connect:
        mock_connect.return_value = MagicMock()
        RedshiftConnector(config)

        call_kwargs = mock_connect.call_args[1]
        assert "iam" not in call_kwargs
        assert "cluster_identifier" not in call_kwargs
        assert "region" not in call_kwargs
        assert "access_key_id" not in call_kwargs
        assert "secret_access_key" not in call_kwargs


# ---------------------------------------------------------------------------
# get_type
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_get_type(connector):
    """Test get_type returns 'redshift'."""
    result = connector.get_type()
    assert result == "redshift"


# ---------------------------------------------------------------------------
# _sys_databases / _sys_schemas
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_sys_databases(connector):
    """Test _sys_databases returns correct system databases."""
    sys_dbs = connector._sys_databases()

    assert isinstance(sys_dbs, set)
    assert sys_dbs == {"padb_harvest", "information_schema"}


@pytest.mark.acceptance
def test_sys_schemas(connector):
    """Test _sys_schemas returns correct system schemas."""
    sys_schemas = connector._sys_schemas()

    assert isinstance(sys_schemas, set)
    assert sys_schemas == {"pg_catalog", "information_schema", "pg_internal"}


# ---------------------------------------------------------------------------
# full_name
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_full_name_three_part(connector):
    """Test full_name with database, schema, and table (three-part)."""
    result = connector.full_name(database_name="mydb", schema_name="myschema", table_name="mytable")
    assert result == '"mydb"."myschema"."mytable"'


@pytest.mark.acceptance
def test_full_name_two_part(connector):
    """Test full_name with schema and table only (two-part)."""
    result = connector.full_name(schema_name="myschema", table_name="mytable")
    assert result == '"myschema"."mytable"'


@pytest.mark.acceptance
def test_full_name_table_only(connector):
    """Test full_name with table only (single-part)."""
    result = connector.full_name(table_name="mytable")
    assert result == '"mytable"'


def test_full_name_database_no_schema(connector):
    """Test full_name with database but no schema falls back to table only."""
    result = connector.full_name(database_name="mydb", table_name="mytable")
    # database_name without schema_name: only table_name is used
    assert result == '"mytable"'


# ---------------------------------------------------------------------------
# _validate_sql_identifier (module-level function)
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_validate_valid_identifiers():
    """Test that valid identifiers are accepted."""
    valid_names = [
        "public",
        "my_schema",
        "schema123",
        "Schema_With_Underscores",
        "_private_schema",
        "schema$with$dollar",
    ]

    for name in valid_names:
        _validate_sql_identifier(name, "schema")  # Should not raise


@pytest.mark.acceptance
def test_validate_sql_injection_rejected():
    """Test that SQL injection attempts are rejected."""
    invalid_names = [
        "schema; DROP TABLE users--",
        "schema' OR '1'='1",
        "schema with spaces",
        "schema-name",
        "schema.name",
        "schema;name",
        "schema'name",
    ]

    for name in invalid_names:
        with pytest.raises(ValueError, match="Invalid"):
            _validate_sql_identifier(name, "schema")


def test_validate_empty_identifier_allowed():
    """Test that empty identifiers are allowed."""
    _validate_sql_identifier("", "schema")  # Should not raise


def test_validate_too_long_identifier():
    """Test that identifiers over 127 characters are rejected."""
    long_name = "a" * 128
    with pytest.raises(ValueError, match="Maximum length"):
        _validate_sql_identifier(long_name, "schema")


def test_validate_max_length_identifier():
    """Test that identifier at exactly 127 characters is accepted."""
    max_name = "a" * 127
    _validate_sql_identifier(max_name, "schema")  # Should not raise


def test_validate_starts_with_digit():
    """Test that identifiers starting with a digit are rejected."""
    with pytest.raises(ValueError, match="Invalid"):
        _validate_sql_identifier("123schema", "schema")


# ---------------------------------------------------------------------------
# _handle_redshift_exception (module-level function)
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_handle_programming_error(caplog):
    """Test ProgrammingError maps to DB_EXECUTION_SYNTAX_ERROR."""
    error = ProgrammingError("syntax error")
    ex = _handle_redshift_exception(error, "SELECT bad")
    assert isinstance(ex, DatusDbException)
    assert ex.code == ErrorCode.DB_EXECUTION_SYNTAX_ERROR
    assert "Redshift SQL execution failed; sql_preview='SELECT bad'; sql_chars=10" in caplog.text
    assert "syntax error" in caplog.text


def test_handle_operational_error():
    """Test OperationalError maps to DB_EXECUTION_ERROR."""
    ex = _handle_redshift_exception(OperationalError("timeout"), "SELECT 1")
    assert ex.code == ErrorCode.DB_EXECUTION_ERROR


def test_handle_database_error():
    """Test DatabaseError maps to DB_EXECUTION_ERROR."""
    ex = _handle_redshift_exception(DatabaseError("db error"), "SELECT 1")
    assert ex.code == ErrorCode.DB_EXECUTION_ERROR


def test_handle_integrity_error():
    """Test IntegrityError maps to DB_CONSTRAINT_VIOLATION."""
    ex = _handle_redshift_exception(IntegrityError("duplicate key"), "INSERT INTO t")
    assert ex.code == ErrorCode.DB_CONSTRAINT_VIOLATION


def test_handle_interface_error():
    """Test InterfaceError maps to DB_CONNECTION_FAILED."""
    ex = _handle_redshift_exception(InterfaceError("connection lost"))
    assert ex.code == ErrorCode.DB_CONNECTION_FAILED


def test_handle_internal_error():
    """Test InternalError maps to DB_CONNECTION_FAILED."""
    ex = _handle_redshift_exception(InternalError("internal"))
    assert ex.code == ErrorCode.DB_CONNECTION_FAILED


def test_handle_data_error():
    """Test DataError maps to DB_EXECUTION_ERROR."""
    ex = _handle_redshift_exception(DataError("overflow"), "SELECT big_num")
    assert ex.code == ErrorCode.DB_EXECUTION_ERROR


def test_handle_generic_exception():
    """Test generic Exception maps to DB_FAILED."""
    ex = _handle_redshift_exception(Exception("unknown error"))
    assert ex.code == ErrorCode.DB_FAILED


# ---------------------------------------------------------------------------
# validate_input
# ---------------------------------------------------------------------------


def test_validate_input_params_list(connector):
    """Test validate_input accepts list params."""
    connector.validate_input({"sql_query": "SELECT 1", "params": [1, 2, 3]})  # Should not raise


def test_validate_input_params_dict(connector):
    """Test validate_input accepts dict params."""
    connector.validate_input({"sql_query": "SELECT 1", "params": {"key": "value"}})  # Should not raise


def test_validate_input_params_invalid_type(connector):
    """Test validate_input rejects non-sequence/dict params."""
    with pytest.raises(ValueError, match="params must be dict or Sequence"):
        connector.validate_input({"sql_query": "SELECT 1", "params": 12345})
