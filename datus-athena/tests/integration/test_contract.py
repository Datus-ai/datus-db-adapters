import os
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from datus_athena import AthenaConfig
from datus_db_core.testing.contract import (
    SelectContractCase,
    TableContractCase,
    assert_select_contract,
    assert_table_contract,
)

pytestmark = pytest.mark.integration


@pytest.mark.acceptance
def test_shared_select_contract(connector):
    values = """1 AS id_value, "Mixed Case" AS mixed_value, "special-name" AS special_value,
        CAST(NULL AS VARCHAR) AS nullable_value, DATE '2024-02-03' AS event_date_value,
        TIMESTAMP '2024-02-03 04:05:06' AS event_ts_value,
        DECIMAL '123.45' AS amount_value, TRUE AS bool_value"""
    table = connector.full_name(table_name="tpch_region")
    quoted = """(VALUES ('Alpha', 'S-1')) AS quoted_fields("Mixed Case", "special-name")"""
    assert_select_contract(
        connector,
        SelectContractCase(
            adapter_name="athena",
            select_sql=f"SELECT {values} FROM {quoted}",
            limit_sql=f"SELECT r_regionkey FROM {table} LIMIT 1",
            qualified_sql=f"SELECT {values} FROM {table} CROSS JOIN {quoted} WHERE r_regionkey = 0",
            dialect_select_sqls=("SELECT element_at(ARRAY[1,2], 2) AS second_value",),
        ),
    )


def build_writable_contract(connector, table_name, location):
    """Hive DDL uses supported physical names; the read contract covers quoted aliases."""
    columns = (
        ("id", "BIGINT", "id_value"),
        ("mixed_case", "STRING", "mixed_value"),
        ("special_name", "STRING", "special_value"),
        ("nullable_text", "STRING", "nullable_value"),
        ("event_date", "DATE", "event_date_value"),
        ("event_ts", "TIMESTAMP", "event_ts_value"),
        ("amount", "DECIMAL(10,2)", "amount_value"),
        ("bool_flag", "BOOLEAN", "bool_value"),
    )
    quote = connector.quote_identifier
    ddl_table = "`" + connector.database_name.replace("`", "``") + "`.`" + table_name + "`"
    table = connector.full_name(table_name=table_name)
    definitions = ", ".join(f"`{name}` {kind}" for name, kind, _ in columns)
    fields = ", ".join(f"{quote(name)} AS {alias}" for name, _, alias in columns)
    location = location.replace("'", "''")
    return TableContractCase(
        adapter_name="athena",
        table_name=table_name,
        drop_sql=f"DROP TABLE IF EXISTS {ddl_table}",
        create_sql=f"CREATE EXTERNAL TABLE {ddl_table} ({definitions}) STORED AS PARQUET LOCATION '{location}'",
        insert_sqls=(
            f"INSERT INTO {table} VALUES "
            "(CAST(1 AS BIGINT), 'Alpha', 'S-1', CAST(NULL AS VARCHAR), DATE '2024-02-03', "
            "TIMESTAMP '2024-02-03 04:05:06', CAST(123.45 AS DECIMAL(10,2)), TRUE), "
            "(CAST(2 AS BIGINT), 'Beta', 'S-2', 'present', DATE '2024-02-04', "
            "TIMESTAMP '2024-02-04 05:06:07', CAST(67.89 AS DECIMAL(10,2)), FALSE)",
        ),
        qualified_select_sql=f"SELECT {fields} FROM {table} ORDER BY {quote('id')}",
        limit_sql=f"SELECT {quote('id')} AS id_value FROM {table} ORDER BY {quote('id')} LIMIT 1",
        schema_kwargs={"catalog_name": connector.catalog_name, "database_name": connector.database_name},
        expected_columns=tuple(name for name, _, _ in columns),
    )


def writable_location(data_location, table_name):
    parsed = urlsplit(AthenaConfig(output_location=data_location).output_location)
    root = parsed.path.strip("/")
    if not root:
        raise ValueError("ATHENA_WRITABLE_DATA_LOCATION requires a dedicated non-root S3 prefix")
    prefix = f"{root}/{table_name}/"
    return parsed.netloc, prefix


def delete_contract_data(s3, bucket, prefix):
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
        if objects:
            response = s3.delete_objects(Bucket=bucket, Delete={"Objects": objects, "Quiet": True})
            if response.get("Errors"):
                raise RuntimeError("Athena contract S3 cleanup failed: " + str(response["Errors"]))


@pytest.fixture
def writable_contract(connector):
    location = os.getenv("ATHENA_WRITABLE_DATA_LOCATION")
    if not location:
        pytest.skip("ATHENA_WRITABLE_DATA_LOCATION is not set for the writable table contract")
    table_name = "contract_" + uuid4().hex
    bucket, prefix = writable_location(location, table_name)
    s3 = connector._identity.client("s3")
    if s3.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1).get("Contents"):
        raise RuntimeError("Athena contract requires an empty isolated S3 prefix")
    try:
        yield build_writable_contract(connector, table_name, f"s3://{bucket}/{prefix}")
    finally:
        # DROP EXTERNAL TABLE removes metadata only; remove this run's data too.
        delete_contract_data(s3, bucket, prefix)


@pytest.mark.acceptance
def test_shared_writable_table_contract(connector, writable_contract):
    assert_table_contract(connector, writable_contract)
