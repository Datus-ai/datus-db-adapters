# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Shared fixtures for the live Snowflake integration tests.

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

Modules are skipped when the credentials are absent (`requires_live_credentials` /
`requires_key_pair_credentials`). Once a connection succeeds every fixture failure is raised,
never skipped, so a green run means the adapter actually works.
"""

import os
import uuid
from dataclasses import dataclass
from typing import Generator

import pytest

from datus_snowflake import SnowflakeConfig, SnowflakeConnector

requires_live_credentials = pytest.mark.skipif(
    not (
        os.getenv("SNOWFLAKE_ACCOUNT")
        and os.getenv("SNOWFLAKE_USER")
        and os.getenv("SNOWFLAKE_WAREHOUSE")
        and (os.getenv("SNOWFLAKE_PASSWORD") or os.getenv("SNOWFLAKE_PRIVATE_KEY_FILE"))
    ),
    reason="Snowflake live credentials not provided in environment variables",
)

requires_key_pair_credentials = pytest.mark.skipif(
    not all(
        [
            os.getenv("SNOWFLAKE_ACCOUNT"),
            os.getenv("SNOWFLAKE_USER"),
            os.getenv("SNOWFLAKE_PRIVATE_KEY_FILE"),
            os.getenv("SNOWFLAKE_WAREHOUSE"),
        ]
    ),
    reason="Snowflake key pair credentials (SNOWFLAKE_PRIVATE_KEY_FILE) not provided",
)

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
