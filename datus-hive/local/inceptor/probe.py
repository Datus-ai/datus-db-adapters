"""Authenticated readiness: exercise HS2, an executor-backed ORC write, and metadata."""

import os
import uuid

from datus_hive import HiveConfig, HiveConnector


def config_from_env() -> HiveConfig:
    return HiveConfig(
        host=os.getenv("HIVE_HOST", "localhost"),
        server_type="inceptor",
        port=int(os.getenv("HIVE_PORT", os.getenv("INCEPTOR_HS2_PORT", "19110"))),
        username=os.getenv("HIVE_USERNAME", "hive"),
        password=os.getenv("HIVE_PASSWORD", ""),
        database="default",
        auth=os.getenv("HIVE_AUTH", "NONE"),
        kerberos_service_name=os.getenv("HIVE_KERBEROS_SERVICE_NAME") or None,
    )


def require_success(result):
    if not result.success:
        raise RuntimeError(f"Inceptor readiness operation failed: {result.error}")
    return result.sql_return


def probe() -> None:
    connection = HiveConnector(config_from_env())
    database = "datus_readiness_" + uuid.uuid4().hex[:12]
    table_ref = f"`{database}`.`probe`"
    view_ref = f"`{database}`.`probe_view`"
    created = False
    try:
        rows = require_success(connection.execute({"sql_query": "SELECT 1 AS probe"}, result_format="list"))
        if rows != [{"probe": 1}]:
            raise RuntimeError(f"Unexpected readiness result: {rows!r}")
        require_success(connection.execute_ddl(f"CREATE DATABASE `{database}`"))
        created = True
        require_success(connection.execute_ddl(f"CREATE TABLE {table_ref} (id INT COMMENT 'identifier') STORED AS ORC"))
        require_success(connection.execute_insert(f"INSERT INTO {table_ref} SELECT 1"))
        require_success(connection.execute_ddl(f"CREATE VIEW {view_ref} AS SELECT id FROM {table_ref}"))
        rows = require_success(connection.execute({"sql_query": f"SELECT id FROM {table_ref}"}, result_format="list"))
        columns = connection.get_schema(database_name=database, table_name="probe")
        if (
            rows != [{"id": 1}]
            or database not in connection.get_databases()
            or connection.get_tables(database_name=database) != ["probe"]
            or connection.get_views(database_name=database) != ["probe_view"]
            or [(c["name"], c["type"].lower(), c["comment"]) for c in columns] != [("id", "int", "identifier")]
        ):
            raise RuntimeError("Readiness data or table/view/column metadata did not match the probe objects")
    finally:
        try:
            if created:
                require_success(connection.execute_ddl(f"DROP DATABASE `{database}` CASCADE"))
        finally:
            connection.close()
    print("Inceptor ready: authenticated query, ORC write/read, and table/view/column metadata passed")


if __name__ == "__main__":
    probe()
