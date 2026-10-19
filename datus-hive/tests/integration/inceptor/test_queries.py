"""Strict manual query contracts for the Hive adapter against Inceptor.

Connection/setup errors fail the run. They are never converted into skipped tests.
Only the uniquely named database created by this session is removed afterwards.
"""

import csv
import io

import pytest

from datus_db_core.testing import contract

pytestmark = pytest.mark.integration


def query(connection, sql):
    result = connection.execute({"sql_query": sql}, result_format="list")
    contract.assert_success(result, sql)
    return result.sql_return


def test_connection_and_unicode(connector):
    assert query(connector, "SELECT 1 AS probe_id, '连接正常' AS probe_text") == [
        {"probe_id": 1, "probe_text": "连接正常"}
    ]


def test_table_query_and_types(connector, objects):
    database, _, _, table_ref, _ = objects
    q = connector.quote_identifier
    typed_ref = f"{q(database)}.{q('typed_contract')}"
    # CE 2024.5 rejects spaces/hyphens in physical column names (error 20484).
    # Exercise its accepted identifiers here and quoted result aliases separately.
    columns = (
        ("id", "INT", "id_value"),
        ("MixedCase", "STRING", "mixed_value"),
        ("special_name", "STRING", "special_value"),
        ("nullable_text", "STRING", "nullable_value"),
        ("event_date", "DATE", "event_date_value"),
        ("event_ts", "TIMESTAMP", "event_ts_value"),
        ("amount", "DECIMAL(10,2)", "amount_value"),
        ("bool_flag", "BOOLEAN", "bool_value"),
    )
    definitions = ", ".join(f"{q(name)} {sql_type}" for name, sql_type, _ in columns)
    projections = ", ".join(f"{q(name)} AS {alias}" for name, _, alias in columns)
    case = contract.TableContractCase(
        adapter_name="hive-on-inceptor",
        table_name="typed_contract",
        drop_sql=f"DROP TABLE IF EXISTS {typed_ref}",
        create_sql=f"CREATE TABLE {typed_ref} ({definitions}) STORED AS ORC",
        insert_sqls=[
            f"INSERT INTO {typed_ref} "
            "SELECT 1, 'Alpha', 'S-1', CAST(NULL AS STRING), DATE '2024-02-03', "
            "TIMESTAMP '2024-02-03 04:05:06', CAST(123.45 AS DECIMAL(10,2)), TRUE "
            "UNION ALL SELECT 2, 'Beta', 'S-2', 'present', DATE '2024-02-04', "
            "TIMESTAMP '2024-02-04 05:06:07', CAST(67.89 AS DECIMAL(10,2)), FALSE"
        ],
        qualified_select_sql=f"SELECT {projections} FROM {typed_ref} ORDER BY {q('id')}",
        limit_sql=f"SELECT {q('id')} AS id_value FROM {typed_ref} ORDER BY {q('id')} LIMIT 1",
        schema_kwargs={"database_name": database},
        expected_columns=tuple(name for name, _, _ in columns),
    )
    contract.assert_table_contract(connector, case)
    assert query(connector, f"SELECT id, label FROM {table_ref}") == [{"id": 1, "label": "本地联调"}]


@pytest.mark.parametrize("result_format", ["list", "csv", "pandas", "arrow"])
def test_result_formats(connector, objects, result_format):
    _, _, _, table_ref, _ = objects
    result = connector.execute({"sql_query": f"SELECT id, label FROM {table_ref}"}, result_format=result_format)
    contract.assert_success(result, f"{result_format} query")
    if result_format == "list":
        rows = result.sql_return
    elif result_format == "csv":
        rows = list(csv.DictReader(io.StringIO(result.sql_return)))
    elif result_format == "pandas":
        rows = result.sql_return.to_dict(orient="records")
    else:
        rows = result.sql_return.to_pylist()
    assert len(rows) == 1
    assert int(rows[0]["id"]) == 1
    assert rows[0]["label"] == "本地联调"


def test_quoted_result_aliases(connector):
    rows = query(connector, "SELECT 1 AS `Mixed Case`, 'quoted' AS `special-name`")
    assert [{key.casefold(): value for key, value in row.items()} for row in rows] == [
        {"mixed case": 1, "special-name": "quoted"}
    ]


@pytest.mark.parametrize("column_name", ["Mixed Case", "special-name"])
def test_inceptor_rejects_special_physical_column_names(connector, objects, column_name):
    """Record the server DDL constraint rather than misclassifying it as an adapter failure."""
    database, _, _, _, _ = objects
    table_ref = f"`{database}`.`unsupported_identifier`"
    try:
        result = connector.execute_ddl(
            f"CREATE TABLE {table_ref} ({connector.quote_identifier(column_name)} INT) STORED AS ORC"
        )
        assert result.success is False
        assert "20484" in result.error
        assert "Invalid column name" in result.error
    finally:
        contract.assert_success(connector.execute_ddl(f"DROP TABLE IF EXISTS {table_ref}"), "cleanup identifier probe")
