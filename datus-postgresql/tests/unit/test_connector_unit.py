# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from unittest.mock import MagicMock, patch

import pytest

from datus_db_core import DatusDbException
from datus_postgresql import PostgreSQLConfig, PostgreSQLConnector


@pytest.mark.acceptance
def test_connector_initialization_with_config_object():
    """Test connector initialization with PostgreSQLConfig object."""
    config = PostgreSQLConfig(
        host="localhost",
        port=5432,
        username="test_user",
        password="test_pass",
        database="testdb",
        schema_name="myschema",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = PostgreSQLConnector(config)

        assert connector.config == config
        assert connector.host == "localhost"
        assert connector.port == 5432
        assert connector.username == "test_user"
        assert connector.password == "test_pass"
        assert connector.database_name == "testdb"
        assert connector.schema_name == "myschema"


@pytest.mark.acceptance
def test_connector_initialization_with_dict():
    """Test connector initialization with dict config."""
    config_dict = {
        "host": "192.168.1.100",
        "port": 5433,
        "username": "admin",
        "password": "secret",
        "database": "mydb",
        "schema_name": "custom_schema",
    }

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = PostgreSQLConnector(config_dict)

        assert connector.host == "192.168.1.100"
        assert connector.port == 5433
        assert connector.username == "admin"
        assert connector.password == "secret"
        assert connector.database_name == "mydb"
        assert connector.schema_name == "custom_schema"


def test_connector_initialization_invalid_type():
    """Test that connector raises TypeError for invalid config type."""
    with pytest.raises(TypeError, match="config must be PostgreSQLConfig or dict"):
        PostgreSQLConnector("invalid_config")


@pytest.mark.acceptance
def test_connector_connection_string_basic():
    """Test connection string generation with basic config."""
    config = PostgreSQLConfig(
        host="localhost",
        port=5432,
        username="user",
        password="pass",
        database="db",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__") as mock_init:
        PostgreSQLConnector(config)

        call_args = mock_init.call_args
        connection_string = call_args[0][0]

        assert "postgresql+psycopg2://user:pass@localhost:5432/db" in connection_string
        assert "sslmode=prefer" in connection_string


@pytest.mark.acceptance
def test_connector_connection_string_special_password():
    """Test connection string generation with special characters in password."""
    config = PostgreSQLConfig(
        host="localhost",
        port=5432,
        username="user",
        password="p@ss!w0rd#$%",
        database="db",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__") as mock_init:
        PostgreSQLConnector(config)

        call_args = mock_init.call_args
        connection_string = call_args[0][0]

        # Password should be URL encoded
        assert "p%40ss%21w0rd%23%24%25" in connection_string


def test_connector_connection_string_no_password():
    """Test connection string generation with empty password."""
    config = PostgreSQLConfig(
        host="localhost",
        port=5432,
        username="user",
        password="",
        database="db",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__") as mock_init:
        PostgreSQLConnector(config)

        call_args = mock_init.call_args
        connection_string = call_args[0][0]

        assert "postgresql+psycopg2://user:@localhost:5432/db" in connection_string


def test_connector_connection_string_no_database():
    """Test connection string generation without database uses 'postgres'."""
    config = PostgreSQLConfig(
        host="localhost",
        port=5432,
        username="user",
        password="pass",
        database=None,
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__") as mock_init:
        PostgreSQLConnector(config)

        call_args = mock_init.call_args
        connection_string = call_args[0][0]

        # Default database should be 'postgres'
        assert "postgresql+psycopg2://user:pass@localhost:5432/postgres" in connection_string


def test_connector_connection_string_custom_sslmode():
    """Test connection string with custom sslmode."""
    config = PostgreSQLConfig(
        host="localhost",
        port=5432,
        username="user",
        password="pass",
        database="db",
        sslmode="require",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__") as mock_init:
        PostgreSQLConnector(config)

        call_args = mock_init.call_args
        connection_string = call_args[0][0]

        assert "sslmode=require" in connection_string


@pytest.mark.acceptance
def test_sys_databases():
    """Test _sys_databases returns correct system databases."""
    config = PostgreSQLConfig(username="user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = PostgreSQLConnector(config)
        sys_dbs = connector._sys_databases()

        assert sys_dbs == {"template0", "template1"}
        assert isinstance(sys_dbs, set)


def test_sys_schemas():
    """Test _sys_schemas returns correct system schemas."""
    config = PostgreSQLConfig(username="user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = PostgreSQLConnector(config)
        sys_schemas = connector._sys_schemas()

        assert "pg_catalog" in sys_schemas
        assert "information_schema" in sys_schemas
        assert "pg_toast" in sys_schemas


@pytest.mark.acceptance
def test_quote_identifier_basic():
    """Test _quote_identifier with basic identifier."""
    assert PostgreSQLConnector.quote_identifier(MagicMock(), "table_name") == '"table_name"'


@pytest.mark.acceptance
def test_quote_identifier_with_double_quotes():
    """Test _quote_identifier escapes double quotes."""
    assert PostgreSQLConnector.quote_identifier(MagicMock(), 'table"name') == '"table""name"'


def test_quote_identifier_with_multiple_double_quotes():
    """Test _quote_identifier escapes multiple double quotes."""
    assert PostgreSQLConnector.quote_identifier(MagicMock(), 'ta"ble"name') == '"ta""ble""name"'


def test_quote_identifier_empty_string():
    """Test _quote_identifier with empty string."""
    assert PostgreSQLConnector.quote_identifier(MagicMock(), "") == '""'


def test_quote_identifier_special_characters():
    """Test _quote_identifier with special characters."""
    assert PostgreSQLConnector.quote_identifier(MagicMock(), "table-name_123") == '"table-name_123"'


@pytest.mark.acceptance
def test_full_name_with_schema():
    """Test full_name method with schema."""
    config = PostgreSQLConfig(username="user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = PostgreSQLConnector(config)
        connector.database_name = "postgres"
        connector.schema_name = "public"
        full_name = connector.full_name(schema_name="myschema", table_name="mytable")

        assert full_name == '"postgres"."myschema"."mytable"'


def test_full_name_with_default_schema():
    """Test full_name method uses default schema."""
    config = PostgreSQLConfig(username="user", schema_name="public")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = PostgreSQLConnector(config)
        connector.database_name = "postgres"
        connector.schema_name = "public"
        full_name = connector.full_name(table_name="mytable")

        assert full_name == '"postgres"."public"."mytable"'


def test_full_name_with_special_characters():
    """Test full_name with special characters (double quotes are escaped)."""
    config = PostgreSQLConfig(username="user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = PostgreSQLConnector(config)
        connector.database_name = "postgres"
        connector.schema_name = "public"
        full_name = connector.full_name(schema_name='my"schema', table_name='my"table')

        assert full_name == '"postgres"."my""schema"."my""table"'


def test_identifier_with_schema():
    """Test identifier method with schema."""
    config = PostgreSQLConfig(username="user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = PostgreSQLConnector(config)
        connector.database_name = "postgres"
        connector.schema_name = "public"
        identifier = connector.identifier(schema_name="myschema", table_name="mytable")

        assert identifier == "postgres.myschema.mytable"


def test_identifier_with_default_schema():
    """Test identifier method uses default schema."""
    config = PostgreSQLConfig(username="user", schema_name="public")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = PostgreSQLConnector(config)
        connector.database_name = "postgres"
        connector.schema_name = "public"
        identifier = connector.identifier(table_name="mytable")

        assert identifier == "postgres.public.mytable"


@pytest.mark.acceptance
def test_get_metadata_config_valid_table_type():
    """Test _get_metadata_config with valid table type."""
    from datus_postgresql.connector import _get_metadata_config

    config = _get_metadata_config("table")
    assert config.info_table == "tables"
    assert config.table_types == ["BASE TABLE"]


def test_get_metadata_config_view_type():
    """Test _get_metadata_config with view type."""
    from datus_postgresql.connector import _get_metadata_config

    config = _get_metadata_config("view")
    assert config.info_table == "views"


def test_get_metadata_config_mv_type():
    """Test _get_metadata_config with materialized view type."""
    from datus_postgresql.connector import _get_metadata_config

    config = _get_metadata_config("mv")
    assert config.info_table == "pg_matviews"


@pytest.mark.acceptance
def test_get_metadata_config_invalid_type():
    """Test _get_metadata_config with invalid table type."""
    from datus_postgresql.connector import _get_metadata_config

    with pytest.raises(DatusDbException, match="Invalid table type"):
        _get_metadata_config("invalid_type")


def test_connector_stores_config():
    """Test that connector stores the config object."""
    config = PostgreSQLConfig(
        host="localhost",
        port=5432,
        username="user",
        password="pass",
        database="db",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = PostgreSQLConnector(config)

        assert connector.config == config
        assert isinstance(connector.config, PostgreSQLConfig)


def test_connector_database_name_attribute():
    """Test that connector sets database_name attribute."""
    config = PostgreSQLConfig(
        host="localhost",
        port=5432,
        username="user",
        password="pass",
        database="testdb",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = PostgreSQLConnector(config)

        assert connector.database_name == "testdb"


def test_connector_database_name_default_when_none():
    """Test that database_name defaults to 'postgres' when config.database is None."""
    config = PostgreSQLConfig(
        host="localhost",
        port=5432,
        username="user",
        password="pass",
        database=None,
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = PostgreSQLConnector(config)

        assert connector.database_name == "postgres"


def test_connector_schema_name_attribute():
    """Test that connector sets schema_name attribute."""
    config = PostgreSQLConfig(
        host="localhost",
        port=5432,
        username="user",
        password="pass",
        schema_name="custom_schema",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = PostgreSQLConnector(config)

        assert connector.schema_name == "custom_schema"


def test_connector_schema_name_default():
    """Test that schema_name defaults to 'public'."""
    config = PostgreSQLConfig(
        host="localhost",
        port=5432,
        username="user",
        password="pass",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = PostgreSQLConnector(config)

        assert connector.schema_name == "public"


# ==================== _get_engine LRU Cache Tests ====================


def _make_connector():
    """Helper: create a PostgreSQLConnector with mocked parent __init__."""
    import threading

    config = PostgreSQLConfig(username="user", password="pass", database="default_db")
    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = PostgreSQLConnector(config)
    # Parent __init__ is mocked, so set attributes that _get_engine needs
    connector._engine_lock = threading.Lock()
    connector.engine = None
    connector._owns_engine = False
    connector.timeout_seconds = 30
    return connector


def test_get_engine_returns_same_engine_for_same_db():
    """Requesting the same database twice returns the cached engine."""
    connector = _make_connector()
    with patch("datus_postgresql.connector.create_engine", return_value=MagicMock()) as mock_ce:
        e1 = connector._get_engine("db1")
        e2 = connector._get_engine("db1")

    assert e1 is e2
    mock_ce.assert_called_once()


def test_get_engine_creates_different_engines_per_db():
    """Different databases get different engines."""
    connector = _make_connector()
    engines = [MagicMock(), MagicMock()]
    with patch("datus_postgresql.connector.create_engine", side_effect=engines):
        e1 = connector._get_engine("db1")
        e2 = connector._get_engine("db2")

    assert e1 is not e2


def test_get_engine_evicts_lru_when_over_max():
    """When cache exceeds max_engines, the least-recently-used engine is disposed."""
    connector = _make_connector()
    connector._max_engines = 3

    created_engines = []

    def make_engine(*args, **kwargs):
        e = MagicMock()
        created_engines.append(e)
        return e

    with patch("datus_postgresql.connector.create_engine", side_effect=make_engine):
        connector._get_engine("db1")
        connector._get_engine("db2")
        connector._get_engine("db3")
        # All 3 fit within max_engines=3
        assert len(connector._engines) == 3
        created_engines[0].dispose.assert_not_called()

        # Adding a 4th should evict db1 (LRU)
        connector._get_engine("db4")
        assert len(connector._engines) == 3
        assert "db1" not in connector._engines
        created_engines[0].dispose.assert_called_once()


def test_get_engine_lru_access_refreshes_order():
    """Accessing an existing engine moves it to most-recently-used, protecting it from eviction."""
    connector = _make_connector()
    connector._max_engines = 3

    created_engines = {}

    def make_engine(*args, **kwargs):
        e = MagicMock()
        created_engines[len(created_engines)] = e
        return e

    with patch("datus_postgresql.connector.create_engine", side_effect=make_engine):
        connector._get_engine("db1")  # engines[0]
        connector._get_engine("db2")  # engines[1]
        connector._get_engine("db3")  # engines[2]

        # Access db1 again — moves it to MRU
        connector._get_engine("db1")

        # Add db4 — should evict db2 (now LRU), NOT db1
        connector._get_engine("db4")

    assert "db1" in connector._engines
    assert "db2" not in connector._engines
    assert "db3" in connector._engines
    assert "db4" in connector._engines
    created_engines[1].dispose.assert_called_once()  # db2 evicted


def test_close_disposes_all_cached_engines():
    """close() disposes all cached engines and clears the cache."""
    connector = _make_connector()

    mock_engines = [MagicMock(), MagicMock()]
    with patch("datus_postgresql.connector.create_engine", side_effect=mock_engines):
        connector._get_engine("db1")
        connector._get_engine("db2")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.close"):
        connector.close()

    for e in mock_engines:
        e.dispose.assert_called_once()
    assert len(connector._engines) == 0


# ==================== Case-insensitive Fallback Tests ====================


def _make_pg_connector_for_metadata(schema_name="public", database_name="testdb"):
    """Helper: PostgreSQLConnector with mocked parent __init__ and a no-op connect()."""
    config = PostgreSQLConfig(username="u", password="p", database=database_name, schema_name=schema_name)
    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = PostgreSQLConnector(config)
    connector.database_name = database_name
    connector.schema_name = schema_name
    connector.connect = MagicMock()
    return connector


_SCHEMA_COLS = [
    "table_schema",
    "table_name",
    "field",
    "type",
    "nullable",
    "default_value",
    "is_pk",
    "comment",
]


def _df(rows, columns):
    import pandas as pd

    return pd.DataFrame(rows, columns=columns)


def test_get_schemas_executes_in_requested_database():
    connector = _make_pg_connector_for_metadata(database_name="default_db")
    connector._execute_pandas = MagicMock(return_value=_df([("reporting",)], ["schema_name"]))

    result = connector.get_schemas(database_name="other_db")

    assert result == ["reporting"]
    connector._execute_pandas.assert_called_once()
    assert connector._execute_pandas.call_args.kwargs["database_name"] == "other_db"


def test_get_schema_strict_match_hits_no_fallback():
    """Exact case match returns rows from first query and never executes the lower() fallback."""
    connector = _make_pg_connector_for_metadata()
    df = _df(
        [
            ("public", "users", "id", "integer", "NO", None, True, None),
            ("public", "users", "name", "text", "YES", None, False, None),
        ],
        _SCHEMA_COLS,
    )
    connector._execute_pandas = MagicMock(return_value=df)

    result = connector.get_schema(schema_name="public", table_name="users")

    assert connector._execute_pandas.call_count == 1
    assert [c["name"] for c in result] == ["id", "name"]
    assert result[0]["pk"] is True


def test_get_schema_executes_in_requested_database():
    connector = _make_pg_connector_for_metadata(database_name="default_db")
    empty = _df([], _SCHEMA_COLS)
    connector._execute_pandas = MagicMock(side_effect=[empty, empty])

    connector.get_schema(database_name="other_db", schema_name="public", table_name="users")

    assert connector._execute_pandas.call_count == 2
    assert all(call.kwargs["database_name"] == "other_db" for call in connector._execute_pandas.call_args_list)


def test_get_schema_falls_back_to_lower_when_strict_empty():
    """If strict match returns empty, fallback to lower() and use those rows."""
    connector = _make_pg_connector_for_metadata()
    empty = _df([], _SCHEMA_COLS)
    fallback = _df(
        [("public", "users", "id", "integer", "NO", None, True, None)],
        _SCHEMA_COLS,
    )
    connector._execute_pandas = MagicMock(side_effect=[empty, fallback])

    result = connector.get_schema(schema_name="PUBLIC", table_name="USERS")

    assert connector._execute_pandas.call_count == 2
    second_sql = connector._execute_pandas.call_args_list[1][0][0]
    assert "lower(c.table_schema)" in second_sql
    assert "lower(c.table_name)" in second_sql
    assert len(result) == 1
    assert result[0]["name"] == "id"


def test_get_schema_lower_fallback_ambiguous_raises():
    """If lower() fallback resolves to two distinct (schema,table) pairs, raise."""
    connector = _make_pg_connector_for_metadata()
    empty = _df([], _SCHEMA_COLS)
    ambiguous = _df(
        [
            ("public", "Users", "id", "integer", "NO", None, True, None),
            ("public", "users", "id", "integer", "NO", None, True, None),
        ],
        _SCHEMA_COLS,
    )
    connector._execute_pandas = MagicMock(side_effect=[empty, ambiguous])

    with pytest.raises(DatusDbException, match="Ambiguous table"):
        connector.get_schema(schema_name="public", table_name="USERS")


def test_get_schema_both_queries_empty_returns_empty():
    """If neither strict nor lower() fallback returns rows, return []."""
    connector = _make_pg_connector_for_metadata()
    empty = _df([], _SCHEMA_COLS)
    connector._execute_pandas = MagicMock(side_effect=[empty, empty])

    result = connector.get_schema(schema_name="public", table_name="missing")

    assert result == []
    assert connector._execute_pandas.call_count == 2


def test_get_schema_empty_table_name_short_circuit():
    """Empty table_name short-circuits before any SQL is executed."""
    connector = _make_pg_connector_for_metadata()
    connector._execute_pandas = MagicMock()

    assert connector.get_schema(schema_name="public", table_name="") == []
    connector._execute_pandas.assert_not_called()


def test_get_ddl_uses_requested_database_for_table_schema():
    connector = _make_pg_connector_for_metadata(database_name="default_db")
    connector.get_schema = MagicMock(return_value=[])

    ddl = connector._get_ddl("public", "orders", database_name="other_db")

    connector.get_schema.assert_called_once_with(
        database_name="other_db",
        schema_name="public",
        table_name="orders",
    )
    assert '"other_db"."public"."orders"' in ddl


@pytest.mark.parametrize("object_type", ["VIEW", "MATERIALIZED VIEW"])
def test_get_ddl_uses_requested_database_for_view_queries(object_type):
    connector = _make_pg_connector_for_metadata(database_name="default_db")
    connector._execute_pandas = MagicMock(return_value=_df([], ["definition"]))

    connector._get_ddl("public", "orders", object_type, database_name="other_db")

    connector._execute_pandas.assert_called_once()
    assert connector._execute_pandas.call_args.kwargs["database_name"] == "other_db"


def test_get_objects_with_ddl_propagates_metadata_database():
    connector = _make_pg_connector_for_metadata(database_name="default_db")
    metadata = [
        {
            "identifier": "other_db.public.orders",
            "catalog_name": "",
            "database_name": "other_db",
            "schema_name": "public",
            "table_name": "orders",
            "table_type": "table",
        }
    ]
    connector._get_metadata = MagicMock(return_value=metadata)
    observed_databases = []

    def get_ddl(*_args):
        observed_databases.append(connector.database_name)
        return "CREATE TABLE orders (id INT);"

    connector._get_ddl = MagicMock(side_effect=get_ddl)

    result = connector._get_objects_with_ddl("table", database_name="other_db", schema_name="public")

    connector._get_ddl.assert_called_once_with(
        "public",
        "orders",
        "TABLE",
    )
    assert observed_databases == ["other_db"]
    assert connector.database_name == "default_db"
    assert result[0]["definition"] == "CREATE TABLE orders (id INT);"


def test_get_sample_rows_executes_in_requested_database():
    connector = _make_pg_connector_for_metadata(database_name="default_db")
    connector._execute_pandas = MagicMock(return_value=_df([(1,)], ["id"]))

    rows = connector.get_sample_rows(
        database_name="other_db",
        schema_name="public",
        tables=["orders"],
        top_n=1,
    )

    connector._execute_pandas.assert_called_once()
    assert connector._execute_pandas.call_args.kwargs["database_name"] == "other_db"
    assert '"other_db"."public"."orders"' in connector._execute_pandas.call_args.args[0]
    assert rows[0]["database_name"] == "other_db"
    assert rows[0]["identifier"] == "other_db.public.orders"


def test_get_metadata_strict_hit_no_fallback():
    """_get_metadata uses only the strict query when it returns rows."""
    connector = _make_pg_connector_for_metadata(schema_name="public")
    df = _df([("public", "t1"), ("public", "t2")], ["table_schema", "table_name"])
    connector._execute_pandas = MagicMock(return_value=df)

    result = connector._get_metadata("table", schema_name="public")

    assert connector._execute_pandas.call_count == 1
    assert [m["table_name"] for m in result] == ["t1", "t2"]


def test_get_metadata_identifier_uses_requested_database():
    connector = _make_pg_connector_for_metadata(database_name="default_db", schema_name="public")
    connector._execute_pandas = MagicMock(return_value=_df([("public", "orders")], ["table_schema", "table_name"]))

    result = connector._get_metadata("table", database_name="other_db", schema_name="public")

    assert result[0]["identifier"] == "other_db.public.orders"


def test_get_metadata_falls_back_to_lower_when_strict_empty():
    """_get_metadata falls back to lower() when strict returns empty and schema_name is provided."""
    connector = _make_pg_connector_for_metadata(schema_name="public")
    empty = _df([], ["table_schema", "table_name"])
    fallback = _df([("public", "t1")], ["table_schema", "table_name"])
    connector._execute_pandas = MagicMock(side_effect=[empty, fallback])

    result = connector._get_metadata("table", schema_name="PUBLIC")

    assert connector._execute_pandas.call_count == 2
    second_sql = connector._execute_pandas.call_args_list[1][0][0]
    assert "lower(table_schema)" in second_sql
    assert [m["table_name"] for m in result] == ["t1"]


def test_get_metadata_lower_fallback_ambiguous_raises():
    """_get_metadata raises when lower() fallback resolves to multiple distinct schemas."""
    connector = _make_pg_connector_for_metadata(schema_name="public")
    empty = _df([], ["table_schema", "table_name"])
    ambiguous = _df([("Public", "t1"), ("public", "t2")], ["table_schema", "table_name"])
    connector._execute_pandas = MagicMock(side_effect=[empty, ambiguous])

    with pytest.raises(DatusDbException, match="Ambiguous schema_name"):
        connector._get_metadata("table", schema_name="PUBLIC")


def test_get_metadata_no_schema_no_fallback():
    """When schema_name is empty, _get_metadata excludes sys schemas and never falls back."""
    connector = _make_pg_connector_for_metadata(schema_name="")
    connector.schema_name = ""
    df = _df([], ["table_schema", "table_name"])
    connector._execute_pandas = MagicMock(return_value=df)

    result = connector._get_metadata("table", schema_name="")

    # Strict query already excludes sys schemas; no fallback for empty input
    assert connector._execute_pandas.call_count == 1
    sql = connector._execute_pandas.call_args_list[0][0][0]
    assert "lower(" not in sql
    assert result == []


def test_get_metadata_mv_falls_back_to_lower_when_strict_empty():
    """MV branch in _get_metadata also honors the case-insensitive fallback."""
    connector = _make_pg_connector_for_metadata(schema_name="public")
    empty = _df([], ["table_schema", "table_name"])
    fallback = _df([("public", "mv1")], ["table_schema", "table_name"])
    connector._execute_pandas = MagicMock(side_effect=[empty, fallback])

    result = connector._get_metadata("mv", schema_name="PUBLIC")

    assert connector._execute_pandas.call_count == 2
    second_sql = connector._execute_pandas.call_args_list[1][0][0]
    assert "lower(schemaname)" in second_sql
    assert [m["table_name"] for m in result] == ["mv1"]
