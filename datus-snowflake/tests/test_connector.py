# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Live Snowflake tests.

| Env var                          | Meaning                                             |
|----------------------------------|-----------------------------------------------------|
| `SNOWFLAKE_ACCOUNT`              | account identifier (required)                       |
| `SNOWFLAKE_USER`                 | user name (required)                                |
| `SNOWFLAKE_WAREHOUSE`            | warehouse (required)                                |
| `SNOWFLAKE_PASSWORD`             | password; required unless a private key file is set |
| `SNOWFLAKE_PRIVATE_KEY_FILE`     | PEM key path for key pair auth                      |
| `SNOWFLAKE_PRIVATE_KEY_FILE_PWD` | passphrase for an encrypted key file                |
| `SNOWFLAKE_DATABASE`             | database the metadata fixtures write into           |
| `SNOWFLAKE_SCHEMA`               | schema the metadata fixtures write into             |
| `SNOWFLAKE_ROLE`                 | role to assume (optional)                           |

The whole module is skipped when the credentials are absent. Once a connection succeeds every
fixture failure is raised, never skipped, so a green run means the adapter actually works.
"""

import os
import uuid
from dataclasses import dataclass
from typing import Generator

import pytest

from datus_snowflake import SnowflakeConfig, SnowflakeConnector

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (
            os.getenv("SNOWFLAKE_ACCOUNT")
            and os.getenv("SNOWFLAKE_USER")
            and os.getenv("SNOWFLAKE_WAREHOUSE")
            and (os.getenv("SNOWFLAKE_PASSWORD") or os.getenv("SNOWFLAKE_PRIVATE_KEY_FILE"))
        ),
        reason="Snowflake live credentials not provided in environment variables",
    ),
]

# Uppercase names with a run-unique suffix: Snowflake stores unquoted identifiers folded to
# uppercase, so an uppercase name quotes back to itself and the objects never collide with a
# concurrent run against the same account.
_OBJECT_SUFFIX = uuid.uuid4().hex[:8].upper()
METADATA_TABLE = f"DATUS_META_TABLE_{_OBJECT_SUFFIX}"
METADATA_VIEW = f"DATUS_META_VIEW_{_OBJECT_SUFFIX}"
METADATA_MV = f"DATUS_META_MV_{_OBJECT_SUFFIX}"

# Snowflake reports a missing edition feature as an "unsupported feature" programming error.
_MV_UNSUPPORTED_MARKERS = ("unsupported feature", "enterprise edition")


def _build_config_dict() -> dict:
    """Create Snowflake configuration from environment."""
    cfg = {
        "account": os.getenv("SNOWFLAKE_ACCOUNT", ""),
        "username": os.getenv("SNOWFLAKE_USER", ""),
        "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE", ""),
        "database": os.getenv("SNOWFLAKE_DATABASE"),
        "schema": os.getenv("SNOWFLAKE_SCHEMA"),
        "role": os.getenv("SNOWFLAKE_ROLE"),
    }
    if os.getenv("SNOWFLAKE_PRIVATE_KEY_FILE"):
        cfg["private_key_file"] = os.getenv("SNOWFLAKE_PRIVATE_KEY_FILE")
        cfg["private_key_file_pwd"] = os.getenv("SNOWFLAKE_PRIVATE_KEY_FILE_PWD")
    else:
        cfg["password"] = os.getenv("SNOWFLAKE_PASSWORD", "")
    return {key: value for key, value in cfg.items() if value is not None}


def _quoted(*parts: str) -> str:
    """Quote and join identifier parts the way Snowflake addresses an object."""
    return ".".join(f'"{part}"' for part in parts)


def _require_success(result, operation: str) -> None:
    if not result.success:
        raise RuntimeError(f"{operation} failed: {result.error}")


@dataclass(frozen=True)
class MetadataObjects:
    """Server-side coordinates of the objects the metadata tests compare against."""

    database: str
    schema: str
    table: str
    view: str

    def identifier(self, object_name: str) -> str:
        return f"{self.database}.{self.schema}.{object_name}"

    def ref(self, object_name: str) -> str:
        return _quoted(self.database, self.schema, object_name)


@pytest.fixture
def config_dict() -> dict:
    return _build_config_dict()


@pytest.fixture
def config(config_dict: dict) -> SnowflakeConfig:
    return SnowflakeConfig(**config_dict)


@pytest.fixture
def connector(config: SnowflakeConfig) -> Generator[SnowflakeConnector, None, None]:
    """Create and cleanup Snowflake connector."""
    conn = SnowflakeConnector(config)
    yield conn
    conn.close()


@pytest.fixture
def database_name(config: SnowflakeConfig) -> str:
    if not config.database:
        pytest.skip("SNOWFLAKE_DATABASE not provided")
    return config.database


@pytest.fixture
def schema_name(config: SnowflakeConfig) -> str:
    if not config.schema_name:
        pytest.skip("SNOWFLAKE_SCHEMA not provided")
    return config.schema_name


@pytest.fixture(scope="module")
def setup_connector() -> Generator[SnowflakeConnector, None, None]:
    """Hold one connection for the whole module's fixture provisioning."""
    config = SnowflakeConfig(**_build_config_dict())
    if not config.database:
        pytest.skip("SNOWFLAKE_DATABASE not provided")
    if not config.schema_name:
        pytest.skip("SNOWFLAKE_SCHEMA not provided")

    try:
        conn = SnowflakeConnector(config)
    except Exception as e:
        pytest.skip(f"Snowflake is unavailable for metadata setup: {e}")

    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(scope="module")
def metadata_objects(setup_connector: SnowflakeConnector) -> Generator[MetadataObjects, None, None]:
    """Create a known table (two known rows) and a view over it.

    The names are resolved from ``CURRENT_DATABASE()``/``CURRENT_SCHEMA()`` rather than from the
    env vars: Snowflake folds an unquoted ``SNOWFLAKE_SCHEMA=public`` to ``PUBLIC``, and the
    metadata calls filter on the stored spelling, so only the server-side name is comparable.
    """
    context = setup_connector.execute_query(
        'SELECT CURRENT_DATABASE() AS "database_name", CURRENT_SCHEMA() AS "schema_name"',
        result_format="list",
    )
    _require_success(context, "resolve session database and schema")
    database = context.sql_return[0]["database_name"]
    schema = context.sql_return[0]["schema_name"]
    if not database or not schema:
        raise RuntimeError(f"Snowflake session has no database/schema context: {database!r}.{schema!r}")

    objects = MetadataObjects(database=database, schema=schema, table=METADATA_TABLE, view=METADATA_VIEW)
    table_ref = objects.ref(METADATA_TABLE)
    view_ref = objects.ref(METADATA_VIEW)

    try:
        _require_success(
            setup_connector.execute_ddl(
                f"""
                CREATE TABLE {table_ref} (
                    "ID" NUMBER(9,0) NOT NULL COMMENT 'row id',
                    "VALUE" VARCHAR(64) COMMENT 'row label'
                )
                """
            ),
            "create metadata table",
        )
        _require_success(
            setup_connector.execute_insert(f"INSERT INTO {table_ref} VALUES (1, 'alpha'), (2, 'beta')"),
            "insert metadata rows",
        )
        _require_success(
            setup_connector.execute_ddl(f'CREATE VIEW {view_ref} AS SELECT "ID", "VALUE" FROM {table_ref}'),
            "create metadata view",
        )
        yield objects
    finally:
        _require_success(setup_connector.execute_ddl(f"DROP VIEW IF EXISTS {view_ref}"), "drop metadata view")
        _require_success(setup_connector.execute_ddl(f"DROP TABLE IF EXISTS {table_ref}"), "drop metadata table")


@pytest.fixture(scope="module")
def materialized_view(
    setup_connector: SnowflakeConnector,
    metadata_objects: MetadataObjects,
) -> Generator[str, None, None]:
    """Create a materialized view over the metadata table.

    MATERIALIZED VIEW is an Enterprise Edition feature, so on a Standard account the CREATE fails
    with "Unsupported feature" — a missing engine capability, not an adapter defect, and the tests
    that need it skip. Every other failure is a real error and is raised.
    """
    mv_ref = metadata_objects.ref(METADATA_MV)
    result = setup_connector.execute_ddl(
        f'CREATE MATERIALIZED VIEW {mv_ref} AS SELECT "ID", "VALUE" FROM {metadata_objects.ref(metadata_objects.table)}'
    )
    if not result.success:
        error = (result.error or "").lower()
        if any(marker in error for marker in _MV_UNSUPPORTED_MARKERS):
            pytest.skip(f"Snowflake edition does not support materialized views: {result.error}")
        raise RuntimeError(f"create metadata materialized view failed: {result.error}")

    try:
        yield METADATA_MV
    finally:
        _require_success(
            setup_connector.execute_ddl(f"DROP MATERIALIZED VIEW IF EXISTS {mv_ref}"),
            "drop metadata materialized view",
        )


# ==================== Connection Tests ====================


def test_connection_with_config_object(config: SnowflakeConfig):
    """Test connection using config object."""
    conn = SnowflakeConnector(config)
    result = conn.test_connection()
    assert result["success"] is True
    conn.close()


def test_connection_with_dict(config_dict: dict):
    """Test connection using dict config."""
    conn = SnowflakeConnector(config_dict)
    result = conn.test_connection()
    assert result["success"] is True
    conn.close()


# ==================== Database Tests ====================


def test_get_databases(connector: SnowflakeConnector):
    """Test getting list of databases."""
    databases = connector.get_databases()
    assert isinstance(databases, list)
    assert len(databases) > 0


def test_get_databases_exclude_system(connector: SnowflakeConnector):
    """Test that system databases are excluded by default."""
    databases = connector.get_databases(include_sys=False)
    system_dbs = {"SNOWFLAKE"}
    for db in databases:
        assert db.upper() not in system_dbs


# ==================== Schema Tests (SchemaNamespaceMixin) ====================


def test_get_schemas(connector: SnowflakeConnector, database_name: str):
    """Test getting list of schemas."""
    schemas = connector.get_schemas(database_name=database_name)
    assert isinstance(schemas, list)


def test_get_schemas_exclude_system(connector: SnowflakeConnector, database_name: str):
    """Test that system schemas are excluded by default."""
    schemas = connector.get_schemas(database_name=database_name, include_sys=False)
    for schema in schemas:
        assert schema.upper() != "INFORMATION_SCHEMA"


# ==================== Table Metadata Tests ====================


def test_get_tables(connector: SnowflakeConnector, metadata_objects: MetadataObjects):
    """The fixture table is listed, schema-qualified when the caller scopes only the database."""
    assert f"{metadata_objects.schema}.{metadata_objects.table}" in connector.get_tables(
        database_name=metadata_objects.database
    )
    assert metadata_objects.table in connector.get_tables(
        database_name=metadata_objects.database,
        schema_name=metadata_objects.schema,
    )


def test_get_tables_with_ddl(connector: SnowflakeConnector, metadata_objects: MetadataObjects):
    """The fixture table comes back with full coordinates and its real DDL."""
    tables = connector.get_tables_with_ddl(
        database_name=metadata_objects.database,
        schema_name=metadata_objects.schema,
    )

    matches = [item for item in tables if item["table_name"] == metadata_objects.table]
    assert len(matches) == 1, f"{metadata_objects.table} missing from {[item['table_name'] for item in tables]}"

    entry = matches[0]
    definition = entry.pop("definition")
    assert entry == {
        "catalog_name": "",
        "database_name": metadata_objects.database,
        "schema_name": metadata_objects.schema,
        "table_name": metadata_objects.table,
        "table_type": "table",
        "identifier": metadata_objects.identifier(metadata_objects.table),
    }
    assert "CREATE" in definition.upper()
    assert "TABLE" in definition.upper()
    assert metadata_objects.table in definition


# ==================== View Tests ====================


def test_get_views(connector: SnowflakeConnector, metadata_objects: MetadataObjects):
    """The fixture view is listed, schema-qualified when the caller scopes only the database."""
    assert f"{metadata_objects.schema}.{metadata_objects.view}" in connector.get_views(
        database_name=metadata_objects.database
    )
    assert metadata_objects.view in connector.get_views(
        database_name=metadata_objects.database,
        schema_name=metadata_objects.schema,
    )


def test_get_views_with_ddl(connector: SnowflakeConnector, metadata_objects: MetadataObjects):
    """The fixture view comes back with full coordinates and a DDL naming its base table."""
    views = connector.get_views_with_ddl(
        database_name=metadata_objects.database,
        schema_name=metadata_objects.schema,
    )

    matches = [item for item in views if item["table_name"] == metadata_objects.view]
    assert len(matches) == 1, f"{metadata_objects.view} missing from {[item['table_name'] for item in views]}"

    entry = matches[0]
    definition = entry.pop("definition")
    assert entry == {
        "catalog_name": "",
        "database_name": metadata_objects.database,
        "schema_name": metadata_objects.schema,
        "table_name": metadata_objects.view,
        "table_type": "view",
        "identifier": metadata_objects.identifier(metadata_objects.view),
    }
    assert "CREATE" in definition.upper()
    assert "VIEW" in definition.upper()
    assert metadata_objects.table in definition


# ==================== Materialized View Tests (MaterializedViewSupportMixin) ====================


def test_get_materialized_views(
    connector: SnowflakeConnector,
    metadata_objects: MetadataObjects,
    materialized_view: str,
):
    """The fixture materialized view is listed under both scoping depths."""
    assert f"{metadata_objects.schema}.{materialized_view}" in connector.get_materialized_views(
        database_name=metadata_objects.database
    )
    assert materialized_view in connector.get_materialized_views(
        database_name=metadata_objects.database,
        schema_name=metadata_objects.schema,
    )


def test_get_materialized_views_with_ddl(
    connector: SnowflakeConnector,
    metadata_objects: MetadataObjects,
    materialized_view: str,
):
    """The fixture materialized view comes back typed ``mv`` with its real DDL."""
    mvs = connector.get_materialized_views_with_ddl(
        database_name=metadata_objects.database,
        schema_name=metadata_objects.schema,
    )

    matches = [item for item in mvs if item["table_name"] == materialized_view]
    assert len(matches) == 1, f"{materialized_view} missing from {[item['table_name'] for item in mvs]}"

    entry = matches[0]
    definition = entry.pop("definition")
    assert entry == {
        "catalog_name": "",
        "database_name": metadata_objects.database,
        "schema_name": metadata_objects.schema,
        "table_name": materialized_view,
        "table_type": "mv",
        "identifier": metadata_objects.identifier(materialized_view),
    }
    assert "CREATE" in definition.upper()
    assert "MATERIALIZED VIEW" in definition.upper()
    assert metadata_objects.table in definition


# ==================== Schema Structure Tests ====================


def test_get_schema(connector: SnowflakeConnector, metadata_objects: MetadataObjects):
    """Every column of the fixture table is described exactly, plus the trailing table summary."""
    schema = connector.get_schema(
        database_name=metadata_objects.database,
        schema_name=metadata_objects.schema,
        table_name=metadata_objects.table,
    )

    assert schema == [
        {
            "cid": 0,
            "name": "ID",
            "type": "NUMBER(9,0)",
            "nullable": False,
            "pk": False,
            "default_value": None,
            "comment": "row id",
        },
        {
            "cid": 1,
            "name": "VALUE",
            "type": "VARCHAR(64)",
            "nullable": True,
            "pk": False,
            "default_value": None,
            "comment": "row label",
        },
        {
            "table": metadata_objects.table,
            "columns": [
                {"name": "ID", "type": "NUMBER(9,0)"},
                {"name": "VALUE", "type": "VARCHAR(64)"},
            ],
            "table_type": "table",
        },
    ]


# ==================== Sample Data Tests ====================


def test_get_sample_rows(connector: SnowflakeConnector, metadata_objects: MetadataObjects):
    """Sampling the fixture table returns its two known rows as CSV."""
    sample_rows = connector.get_sample_rows(
        database_name=metadata_objects.database,
        schema_name=metadata_objects.schema,
        tables=[metadata_objects.table],
        top_n=3,
    )

    assert len(sample_rows) == 1
    assert sample_rows[0] == {
        "identifier": metadata_objects.identifier(metadata_objects.table),
        "catalog_name": "",
        "database_name": metadata_objects.database,
        "schema_name": metadata_objects.schema,
        "table_name": metadata_objects.table,
        "table_type": "table",
        "sample_rows": sample_rows[0]["sample_rows"],
    }

    csv_lines = sample_rows[0]["sample_rows"].strip().split("\n")
    assert csv_lines[0] == "ID,VALUE"
    assert sorted(csv_lines[1:]) == ["1,alpha", "2,beta"]


# ==================== SQL Execution Tests ====================


def test_execute_query_csv(connector: SnowflakeConnector):
    """Test executing query with CSV format."""
    result = connector.execute_query('SELECT 1 AS "num"', result_format="csv")
    assert result.success
    assert not result.error
    assert "num" in result.sql_return


def test_execute_query_list(connector: SnowflakeConnector):
    """Test executing query with list format."""
    result = connector.execute_query('SELECT 1 AS "num"', result_format="list")
    assert result.success
    assert not result.error
    assert result.sql_return == [{"num": 1}]


def test_execute_query_arrow(connector: SnowflakeConnector):
    """Test executing query with Arrow format."""
    result = connector.execute_query('SELECT 1 AS "num"', result_format="arrow")
    assert result.success
    assert not result.error
    assert result.sql_return is not None


def test_execute_query_pandas(connector: SnowflakeConnector):
    """Test executing query with pandas format."""
    result = connector.execute_query('SELECT 1 AS "num"', result_format="pandas")
    assert result.success
    assert not result.error
    assert len(result.sql_return) == 1


def test_execute_show_databases(connector: SnowflakeConnector):
    """Test executing SHOW DATABASES."""
    result = connector.execute_query("SHOW DATABASES", result_format="list")
    assert result.success
    assert isinstance(result.sql_return, list)


def test_execute_show_schemas(connector: SnowflakeConnector, database_name: str):
    """Test executing SHOW SCHEMAS."""
    result = connector.execute_query(f'SHOW SCHEMAS IN DATABASE "{database_name}"', result_format="list")
    assert result.success
    assert isinstance(result.sql_return, list)


# ==================== Error Handling Tests ====================


def test_execute_invalid_sql(connector: SnowflakeConnector):
    """Test exception on invalid SQL."""
    result = connector.execute_query("INVALID SQL SYNTAX")
    assert not result.success
    assert result.error is not None


def test_execute_nonexistent_table(connector: SnowflakeConnector):
    """Test exception on non-existent table."""
    result = connector.execute_query("SELECT * FROM nonexistent_table_xyz")
    assert not result.success
    assert result.error is not None
