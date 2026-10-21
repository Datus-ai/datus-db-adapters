"""Strict manual Hive adapter checks against Inceptor.

Environment variables:
    INCEPTOR_TEST: set to 1 to opt into this manual target.
    HIVE_HOST / HIVE_PORT: HS2 endpoint (127.0.0.1:19110 by default).
    HIVE_USERNAME / HIVE_PASSWORD: credentials for NONE/LDAP/CUSTOM.
    HIVE_AUTH: NONE (default), LDAP, CUSTOM or KERBEROS.
    HIVE_KERBEROS_SERVICE_NAME: required GSSAPI service, usually hive.
    HIVE_TEST_ROLE: optionally ADMIN for authorized LDAP fixture provisioning.
    KRB5_CONFIG / KRB5CCNAME: external Kerberos realm and client ticket cache.

Connection/setup errors fail the run. They are never converted into skipped tests.
Only the uniquely named database created by this session is removed afterwards.
"""

import os
import sys
import uuid
from pathlib import Path

import pytest

from datus_db_core.testing import contract
from datus_hive import HiveConfig, HiveConnector

pytestmark = pytest.mark.integration


def pytest_collection_modifyitems(items):
    if os.getenv("INCEPTOR_TEST") != "1":
        marker = pytest.mark.skip(reason="Manual Inceptor target: set INCEPTOR_TEST=1")
        for item in items:
            if str(item.path.parent) == str(Path(__file__).parent):
                item.add_marker(marker)


@pytest.fixture(scope="session")
def connector():
    config = HiveConfig(
        host=os.environ.get("HIVE_HOST", "127.0.0.1"),
        port=int(os.environ.get("HIVE_PORT", "19110")),
        username=os.environ.get("HIVE_USERNAME", "hive"),
        password=os.environ.get("HIVE_PASSWORD", ""),
        auth=os.environ.get("HIVE_AUTH", "NONE"),
        kerberos_service_name=os.environ.get("HIVE_KERBEROS_SERVICE_NAME") or None,
        database="default",
        server_type="inceptor",
    )
    connection = HiveConnector(config)
    try:
        assert connection.test_connection(), "Hive adapter connection test failed"
        role = os.environ.get("HIVE_TEST_ROLE")
        if role:
            assert role == "ADMIN", "This local fixture only supports the ADMIN test role"
            contract.assert_success(connection.execute_ddl("SET ROLE ADMIN"), "select test administrator role")
        yield connection
    finally:
        connection.close()


@pytest.fixture(scope="session")
def objects(connector):
    database = "datus_probe_" + uuid.uuid4().hex[:10]
    table = "probe_table"
    view = "probe_view"
    table_ref = f"`{database}`.`{table}`"
    view_ref = f"`{database}`.`{view}`"
    contract.assert_success(connector.execute_ddl(f"CREATE DATABASE `{database}`"), "create probe database")
    try:
        contract.assert_success(
            connector.execute_ddl(
                f"CREATE TABLE {table_ref} (id INT COMMENT 'identifier', label STRING COMMENT '中文注释') STORED AS ORC"
            ),
            "create ORC table",
        )
        # Inceptor supports INSERT SELECT for nontransactional ORC tables.
        contract.assert_success(
            connector.execute_insert(f"INSERT INTO {table_ref} SELECT 1, '本地联调'"),
            "insert fixture row",
        )
        contract.assert_success(
            connector.execute_ddl(f"CREATE VIEW {view_ref} AS SELECT id, label FROM {table_ref}"),
            "create view",
        )
        yield database, table, view, table_ref, view_ref
    finally:
        original_error = sys.exception()
        try:
            contract.assert_success(
                connector.execute_ddl(f"DROP DATABASE `{database}` CASCADE"), "clean up probe database"
            )
        except Exception as cleanup_error:
            if original_error is None:
                raise
            original_error.add_note(f"Probe cleanup failed: {cleanup_error}")
