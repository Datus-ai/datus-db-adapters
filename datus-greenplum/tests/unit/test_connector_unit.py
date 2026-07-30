# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from datus_greenplum import GreenplumConfig, GreenplumConnector
from datus_greenplum.connector import _escape_literal


@pytest.mark.acceptance
def test_connector_initialization_with_config_object():
    """Test connector initialization with GreenplumConfig object."""
    config = GreenplumConfig(
        host="localhost",
        port=5432,
        username="gpadmin",
        password="test_pass",
        database="testdb",
        schema_name="myschema",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = GreenplumConnector(config)

        assert connector.greenplum_config == config
        assert connector.config == config
        assert connector.host == "localhost"
        assert connector.port == 5432
        assert connector.username == "gpadmin"
        assert connector.password == "test_pass"
        assert connector.database_name == "testdb"
        assert connector.schema_name == "myschema"


@pytest.mark.acceptance
def test_connector_initialization_with_dict():
    """Test connector initialization with dict config."""
    config_dict = {
        "host": "192.168.1.100",
        "port": 5433,
        "username": "gpadmin",
        "password": "secret",
        "database": "mydb",
        "schema_name": "custom_schema",
    }

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = GreenplumConnector(config_dict)

        assert connector.host == "192.168.1.100"
        assert connector.port == 5433
        assert connector.username == "gpadmin"
        assert connector.password == "secret"
        assert connector.database_name == "mydb"
        assert connector.schema_name == "custom_schema"


def test_connector_initialization_invalid_type():
    """Test that connector raises TypeError for invalid config type."""
    with pytest.raises(TypeError, match="config must be GreenplumConfig or dict"):
        GreenplumConnector("invalid_config")


@pytest.mark.acceptance
def test_connector_connection_string_basic():
    """Test connection string generation with basic config."""
    config = GreenplumConfig(
        host="localhost",
        port=5432,
        username="gpadmin",
        password="pass",
        database="db",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__") as mock_init:
        GreenplumConnector(config)

        call_args = mock_init.call_args
        connection_string = call_args[0][0]

        assert "postgresql+psycopg2://gpadmin:pass@localhost:5432/db" in connection_string
        assert "sslmode=prefer" in connection_string


@pytest.mark.acceptance
def test_connector_connection_string_special_password():
    """Test connection string generation with special characters in password."""
    config = GreenplumConfig(
        host="localhost",
        port=5432,
        username="gpadmin",
        password="p@ss!w0rd#$%",
        database="db",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__") as mock_init:
        GreenplumConnector(config)

        call_args = mock_init.call_args
        connection_string = call_args[0][0]

        # Password should be URL encoded
        assert "p%40ss%21w0rd%23%24%25" in connection_string


def test_connector_connection_string_no_database():
    """Test connection string generation without database uses 'postgres'."""
    config = GreenplumConfig(
        host="localhost",
        port=5432,
        username="gpadmin",
        password="pass",
        database=None,
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__") as mock_init:
        GreenplumConnector(config)

        call_args = mock_init.call_args
        connection_string = call_args[0][0]

        assert "postgresql+psycopg2://gpadmin:pass@localhost:5432/postgres" in connection_string


@pytest.mark.acceptance
def test_sys_databases():
    """Test _sys_databases returns correct system databases including Greenplum-specific."""
    config = GreenplumConfig(username="gpadmin")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = GreenplumConnector(config)
        sys_dbs = connector._sys_databases()

        assert sys_dbs == {"template0", "template1", "gpperfmon"}
        assert isinstance(sys_dbs, set)


@pytest.mark.acceptance
def test_sys_schemas():
    """Test _sys_schemas returns correct system schemas including Greenplum-specific."""
    config = GreenplumConfig(username="gpadmin")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = GreenplumConnector(config)
        sys_schemas = connector._sys_schemas()

        # PostgreSQL standard schemas
        assert "pg_catalog" in sys_schemas
        assert "information_schema" in sys_schemas
        assert "pg_toast" in sys_schemas
        # Greenplum-specific schemas
        assert "gp_toolkit" in sys_schemas
        assert "pg_aoseg" in sys_schemas
        assert "pg_bitmapindex" in sys_schemas


@pytest.mark.acceptance
def test_quote_identifier_basic():
    """Test _quote_identifier with basic identifier."""
    assert GreenplumConnector.quote_identifier(MagicMock(), "table_name") == '"table_name"'


@pytest.mark.acceptance
def test_quote_identifier_with_double_quotes():
    """Test _quote_identifier escapes double quotes."""
    assert GreenplumConnector.quote_identifier(MagicMock(), 'table"name') == '"table""name"'


@pytest.mark.acceptance
def test_full_name_with_schema():
    """Test full_name method with schema."""
    config = GreenplumConfig(username="gpadmin")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = GreenplumConnector(config)
        connector.schema_name = "public"
        full_name = connector.full_name(schema_name="myschema", table_name="mytable")

        assert full_name == '"postgres"."myschema"."mytable"'


def test_full_name_with_default_schema():
    """Test full_name method uses default schema."""
    config = GreenplumConfig(username="gpadmin", schema_name="public")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = GreenplumConnector(config)
        connector.schema_name = "public"
        full_name = connector.full_name(table_name="mytable")

        assert full_name == '"postgres"."public"."mytable"'


def test_identifier_with_schema():
    """Test identifier method with schema."""
    config = GreenplumConfig(username="gpadmin")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = GreenplumConnector(config)
        connector.schema_name = "public"
        identifier = connector.identifier(schema_name="myschema", table_name="mytable")

        assert identifier == "postgres.myschema.mytable"


def test_identifier_with_default_schema():
    """Test identifier method uses default schema."""
    config = GreenplumConfig(username="gpadmin", schema_name="public")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = GreenplumConnector(config)
        connector.schema_name = "public"
        identifier = connector.identifier(table_name="mytable")

        assert identifier == "postgres.public.mytable"


def test_connector_stores_greenplum_config():
    """Test that connector stores both greenplum_config and config."""
    config = GreenplumConfig(
        host="localhost",
        port=5432,
        username="gpadmin",
        password="pass",
        database="db",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = GreenplumConnector(config)

        assert connector.greenplum_config == config
        assert connector.config == config
        assert isinstance(connector.greenplum_config, GreenplumConfig)


def test_connector_database_name_default_when_none():
    """Test that database_name defaults to 'postgres' when config.database is None."""
    config = GreenplumConfig(
        host="localhost",
        port=5432,
        username="gpadmin",
        password="pass",
        database=None,
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = GreenplumConnector(config)
        assert connector.database_name == "postgres"


def test_connector_schema_name_default():
    """Test that schema_name defaults to 'public'."""
    config = GreenplumConfig(
        host="localhost",
        port=5432,
        username="gpadmin",
        password="pass",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = GreenplumConnector(config)
        assert connector.schema_name == "public"


def test_connector_connection_string_custom_sslmode():
    """Test connection string with custom sslmode."""
    config = GreenplumConfig(
        host="localhost",
        port=5432,
        username="gpadmin",
        password="pass",
        database="db",
        sslmode="require",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__") as mock_init:
        GreenplumConnector(config)

        call_args = mock_init.call_args
        connection_string = call_args[0][0]

        assert "sslmode=require" in connection_string


# ==================== _escape_literal Tests ====================


def test_escape_literal_basic():
    """Test _escape_literal with normal string."""
    assert _escape_literal("public") == "public"


def test_escape_literal_single_quote():
    """Test _escape_literal escapes single quotes."""
    assert _escape_literal("it's") == "it''s"


def test_escape_literal_multiple_quotes():
    """Test _escape_literal escapes multiple single quotes."""
    assert _escape_literal("it's a 'test'") == "it''s a ''test''"


def test_escape_literal_empty_string():
    """Test _escape_literal with empty string."""
    assert _escape_literal("") == ""


# ==================== _get_distribution_policy Tests ====================


@pytest.mark.acceptance
def test_get_distribution_policy_by_column():
    """Test _get_distribution_policy returns DISTRIBUTED BY for keyed tables."""
    config = GreenplumConfig(username="gpadmin")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = GreenplumConnector(config)
        connector._execute_pandas = MagicMock(
            side_effect=[
                pd.DataFrame({"attname": ["distkey"]}),
                pd.DataFrame({"attname": ["id", "name"]}),
            ]
        )

        result = connector._get_distribution_policy("public", "test_table")

        assert result == 'DISTRIBUTED BY ("id", "name")'
        sql_arg = connector._execute_pandas.call_args_list[1][0][0]
        assert "dp.distkey" in sql_arg
        assert "dp.attrnums" not in sql_arg
        assert "WITH ORDINALITY AS dist(attnum, ord)" in sql_arg
        assert "ORDER BY dist.ord" in sql_arg
        assert "ORDER BY a.attnum" not in sql_arg


def test_get_distribution_policy_uses_attrnums_for_older_greenplum():
    """Test _get_distribution_policy supports older Greenplum catalog shape."""
    config = GreenplumConfig(username="gpadmin")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = GreenplumConnector(config)
        connector._execute_pandas = MagicMock(
            side_effect=[
                pd.DataFrame({"attname": ["attrnums"]}),
                pd.DataFrame({"attname": ["id"]}),
            ]
        )

        result = connector._get_distribution_policy("public", "test_table")

        assert result == 'DISTRIBUTED BY ("id")'
        sql_arg = connector._execute_pandas.call_args_list[1][0][0]
        assert "dp.attrnums" in sql_arg
        assert "dp.distkey" not in sql_arg


@pytest.mark.acceptance
def test_get_distribution_policy_random():
    """Test _get_distribution_policy returns DISTRIBUTED RANDOMLY for null keys."""
    config = GreenplumConfig(username="gpadmin")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = GreenplumConnector(config)
        connector._execute_pandas = MagicMock(
            side_effect=[
                pd.DataFrame({"attname": ["distkey"]}),
                pd.DataFrame({"attname": [None]}),
            ]
        )

        result = connector._get_distribution_policy("public", "test_table")

        assert result == "DISTRIBUTED RANDOMLY"


def test_get_distribution_policy_empty_result():
    """Test _get_distribution_policy returns DISTRIBUTED RANDOMLY for empty result."""
    config = GreenplumConfig(username="gpadmin")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = GreenplumConnector(config)
        connector._execute_pandas = MagicMock(
            side_effect=[
                pd.DataFrame({"attname": ["distkey"]}),
                pd.DataFrame({"attname": []}),
            ]
        )

        result = connector._get_distribution_policy("public", "test_table")

        assert result == "DISTRIBUTED RANDOMLY"


@pytest.mark.acceptance
def test_get_distribution_policy_error_returns_none():
    """Test _get_distribution_policy returns None on error (not empty string)."""
    config = GreenplumConfig(username="gpadmin")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = GreenplumConnector(config)
        connector._execute_pandas = MagicMock(
            side_effect=[
                pd.DataFrame({"attname": ["distkey"]}),
                Exception("catalog error"),
            ]
        )

        result = connector._get_distribution_policy("public", "test_table")

        assert result is None


def test_get_distribution_policy_escapes_input():
    """Test _get_distribution_policy escapes schema/table names in SQL."""
    config = GreenplumConfig(username="gpadmin")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = GreenplumConnector(config)
        connector._execute_pandas = MagicMock(
            side_effect=[
                pd.DataFrame({"attname": ["distkey"]}),
                pd.DataFrame({"attname": ["id"]}),
            ]
        )

        connector._get_distribution_policy("it's", "tab'le")

        sql_arg = connector._execute_pandas.call_args_list[1][0][0]
        assert "it''s" in sql_arg
        assert "tab''le" in sql_arg


# ==================== _get_ddl Override Tests ====================


@pytest.mark.acceptance
def test_get_ddl_appends_distribution_for_table():
    """Test _get_ddl appends distribution policy for TABLE type."""
    config = GreenplumConfig(username="gpadmin")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = GreenplumConnector(config)
        # Mock parent _get_ddl to return basic DDL
        with patch(
            "datus_postgresql.PostgreSQLConnector._get_ddl",
            return_value='CREATE TABLE "public"."t" (\n    "id" integer\n);',
        ):
            connector._get_distribution_policy = MagicMock(return_value='DISTRIBUTED BY ("id")')

            ddl = connector._get_ddl("public", "t", "TABLE")

            assert ddl.endswith('DISTRIBUTED BY ("id");')
            assert "CREATE TABLE" in ddl


def test_get_ddl_no_distribution_for_view():
    """Test _get_ddl does NOT append distribution for VIEW type."""
    config = GreenplumConfig(username="gpadmin")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = GreenplumConnector(config)
        with patch(
            "datus_postgresql.PostgreSQLConnector._get_ddl", return_value='CREATE VIEW "public"."v" AS\nSELECT 1'
        ):
            connector._get_distribution_policy = MagicMock()

            ddl = connector._get_ddl("public", "v", "VIEW")

            connector._get_distribution_policy.assert_not_called()
            assert "DISTRIBUTED" not in ddl


@pytest.mark.acceptance
def test_get_ddl_skips_distribution_on_error():
    """Test _get_ddl omits distribution when _get_distribution_policy returns None."""
    config = GreenplumConfig(username="gpadmin")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = GreenplumConnector(config)
        with patch(
            "datus_postgresql.PostgreSQLConnector._get_ddl",
            return_value='CREATE TABLE "public"."t" (\n    "id" integer\n);',
        ):
            connector._get_distribution_policy = MagicMock(return_value=None)

            ddl = connector._get_ddl("public", "t", "TABLE")

            assert "DISTRIBUTED" not in ddl
            assert ddl.endswith(";")


def test_get_objects_with_ddl_keeps_legacy_override_compatible_for_other_database():
    config = GreenplumConfig(username="gpadmin", database="default_db")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = GreenplumConnector(config)
        connector._get_metadata = MagicMock(
            return_value=[
                {
                    "identifier": "other_db.public.orders",
                    "catalog_name": "",
                    "database_name": "other_db",
                    "schema_name": "public",
                    "table_name": "orders",
                    "table_type": "table",
                }
            ]
        )
        observed_databases = []

        def get_distribution_policy(*_args):
            observed_databases.append(connector.database_name)
            return 'DISTRIBUTED BY ("id")'

        connector._get_distribution_policy = MagicMock(side_effect=get_distribution_policy)
        with patch(
            "datus_postgresql.PostgreSQLConnector._get_ddl",
            return_value='CREATE TABLE "other_db"."public"."orders" (\n    "id" integer\n);',
        ):
            result = connector._get_objects_with_ddl(
                "table",
                database_name="other_db",
                schema_name="public",
            )

    assert observed_databases == ["other_db"]
    assert connector.database_name == "default_db"
    assert result[0]["definition"].endswith('DISTRIBUTED BY ("id");')


# ==================== get_storage_info Tests ====================


@pytest.mark.acceptance
def test_get_storage_info_heap():
    """Test get_storage_info returns heap storage type."""
    config = GreenplumConfig(username="gpadmin")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = GreenplumConnector(config)
        connector.schema_name = "public"
        mock_df = pd.DataFrame({"relstorage": ["h"], "storage_type": ["heap"]})
        connector._execute_pandas = MagicMock(return_value=mock_df)

        result = connector.get_storage_info(schema_name="public", table_name="test_table")

        assert result == {"storage_code": "h", "storage_type": "heap"}


def test_get_storage_info_ao():
    """Test get_storage_info returns append-optimized storage type."""
    config = GreenplumConfig(username="gpadmin")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = GreenplumConnector(config)
        connector.schema_name = "public"
        mock_df = pd.DataFrame({"relstorage": ["a"], "storage_type": ["append-optimized"]})
        connector._execute_pandas = MagicMock(return_value=mock_df)

        result = connector.get_storage_info(schema_name="public", table_name="test_table")

        assert result == {"storage_code": "a", "storage_type": "append-optimized"}


def test_get_storage_info_no_table_name():
    """Test get_storage_info returns None when table_name is empty."""
    config = GreenplumConfig(username="gpadmin")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = GreenplumConnector(config)
        connector.schema_name = "public"

        result = connector.get_storage_info(schema_name="public", table_name="")

        assert result is None


def test_get_storage_info_error_returns_none():
    """Test get_storage_info returns None on error."""
    config = GreenplumConfig(username="gpadmin")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = GreenplumConnector(config)
        connector.schema_name = "public"
        connector._execute_pandas = MagicMock(side_effect=Exception("query failed"))

        result = connector.get_storage_info(schema_name="public", table_name="test_table")

        assert result is None


def test_get_storage_info_escapes_input():
    """Test get_storage_info escapes schema/table names in SQL."""
    config = GreenplumConfig(username="gpadmin")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = GreenplumConnector(config)
        connector.schema_name = "public"
        mock_df = pd.DataFrame({"relstorage": ["h"], "storage_type": ["heap"]})
        connector._execute_pandas = MagicMock(return_value=mock_df)

        connector.get_storage_info(schema_name="it's", table_name="tab'le")

        sql_arg = connector._execute_pandas.call_args[0][0]
        assert "it''s" in sql_arg
        assert "tab''le" in sql_arg


# ==================== sys_databases/schemas Inheritance Tests ====================


def test_sys_databases_inherits_from_parent():
    """Test _sys_databases is a superset of PostgreSQLConnector._sys_databases."""
    config = GreenplumConfig(username="gpadmin")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        from datus_postgresql import PostgreSQLConnector

        pg_connector = PostgreSQLConnector.__new__(PostgreSQLConnector)
        gp_connector = GreenplumConnector(config)

        pg_dbs = pg_connector._sys_databases()
        gp_dbs = gp_connector._sys_databases()

        assert pg_dbs.issubset(gp_dbs)
        assert "gpperfmon" in gp_dbs


def test_sys_schemas_inherits_from_parent():
    """Test _sys_schemas is a superset of PostgreSQLConnector._sys_schemas."""
    config = GreenplumConfig(username="gpadmin")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        from datus_postgresql import PostgreSQLConnector

        pg_connector = PostgreSQLConnector.__new__(PostgreSQLConnector)
        gp_connector = GreenplumConnector(config)

        pg_schemas = pg_connector._sys_schemas()
        gp_schemas = gp_connector._sys_schemas()

        assert pg_schemas.issubset(gp_schemas)
        assert "gp_toolkit" in gp_schemas
