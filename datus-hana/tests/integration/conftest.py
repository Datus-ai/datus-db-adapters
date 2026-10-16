"""Live HANA tests use an isolated pre-provisioned schema.

| Variable | Meaning |
| --- | --- |
| HANA_HOST / HANA_PORT | Tenant SQL endpoint, default port 23941 |
| HANA_USER / HANA_PASSWORD | Dedicated test credentials |
| HANA_USERKEY | Alternative user store key, exclusive with user/password |
| HANA_TEST_USERKEY | Optional additional user-store authentication test key |
| HANA_DATABASE | Optional tenant connection target |
| HANA_SCHEMA | Required dedicated schema; fixture tables may be dropped |
| HANA_ENCRYPT | true by default; false only for isolated Express tests |
| HANA_SSL_TRUST_STORE | Optional CA trust store |
| HANA_SSL_HOST_NAME_IN_CERTIFICATE | Optional expected certificate hostname |
| HANA_REQUIRE_INTEGRATION | 1 makes missing environment/connectivity fail |
"""

import os
import uuid

import pytest

from datus_hana import HanaConnector, register
from datus_hana.testing import config_from_env
from datus_hana.tpch_data import TPCH_DATA, TPCH_DDL, TPCH_TABLES


@pytest.fixture(scope="session")
def config():
    required = os.getenv("HANA_REQUIRE_INTEGRATION") == "1"
    if not os.getenv("HANA_SCHEMA") or not (
        os.getenv("HANA_USERKEY") or os.getenv("HANA_HOST") and os.getenv("HANA_PASSWORD")
    ):
        if required:
            pytest.fail("HANA integration environment is required but incomplete")
        pytest.skip("HANA_HOST/credentials and a dedicated HANA_SCHEMA are not configured")
    config = config_from_env()
    connector = HanaConnector(config)
    try:
        connector.test_connection()
    except Exception as error:
        if required:
            pytest.fail(f"Required HANA environment is unavailable: {error}")
        pytest.skip(f"HANA is unavailable: {error}")
    finally:
        connector.close()
    return config


@pytest.fixture
def connector(config):
    register()
    with HanaConnector(config) as connector:
        yield connector


def checked(result):
    if not result.success:
        raise RuntimeError(result.error)
    return result


@pytest.fixture(scope="session")
def tpch_setup(config):
    connector = HanaConnector(config)
    created = []
    existing = set(connector.get_tables(schema_name=config.schema_name))
    if set(TPCH_TABLES) & existing:
        raise RuntimeError("TPC-H tables already exist; use an isolated schema or clean them explicitly")
    try:
        for table, ddl in zip(TPCH_TABLES, TPCH_DDL, strict=True):
            checked(connector.execute_ddl(ddl))
            created.append(table)
        connector.execute_queries(TPCH_DATA)
        yield
    finally:
        for table in reversed(created):
            checked(connector.execute_ddl(f'DROP TABLE "{table}"'))
        connector.close()


@pytest.fixture
def metadata_objects_setup(connector):
    suffix = uuid.uuid4().hex[:10]
    table, view = f"Mixed.Table_{suffix}", f"Mixed.View_{suffix}"
    table_ref = connector.full_name(table_name=table)
    view_ref = connector.full_name(table_name=view)
    created_table = created_view = False
    try:
        checked(connector.execute_ddl(f'CREATE COLUMN TABLE {table_ref} ("Mixed Column" INTEGER, "text" NVARCHAR(50))'))
        created_table = True
        checked(connector.execute_query(f"INSERT INTO {table_ref} VALUES (?, ?)", parameters=(1, "中文")))
        checked(connector.execute_ddl(f"CREATE VIEW {view_ref} AS SELECT * FROM {table_ref}"))
        created_view = True
        yield {"table": table, "view": view, "table_ref": table_ref}
    finally:
        if created_view:
            checked(connector.execute_ddl(f"DROP VIEW {view_ref}"))
        if created_table:
            checked(connector.execute_ddl(f"DROP TABLE {table_ref}"))
