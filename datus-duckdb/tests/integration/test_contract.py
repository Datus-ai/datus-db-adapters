import pytest

from datus_db_core.testing import contract
from datus_duckdb import DuckDBConfig, DuckdbConnector

pytestmark = pytest.mark.integration


def test_common_adapter_contract(tmp_path):
    connector = DuckdbConnector(DuckDBConfig(uri=str(tmp_path / "contract.duckdb")))
    case = contract.TableContractCase(
        adapter_name="duckdb",
        table_name="contract_rows",
        drop_sql='DROP TABLE IF EXISTS "contract_rows"',
        create_sql="""CREATE TABLE "contract_rows" (
            id BIGINT, "Mixed Case" VARCHAR, "special-name" VARCHAR,
            nullable_text VARCHAR, event_date DATE, event_ts TIMESTAMP,
            amount DECIMAL(12,2), bool_flag BOOLEAN)""",
        insert_sqls=[
            """INSERT INTO "contract_rows" VALUES
            (1, 'Alpha', 'S-1', NULL, DATE '2024-02-03', TIMESTAMP '2024-02-03 04:05:06', 123.45, TRUE),
            (2, 'Beta', 'S-2', '数据', DATE '2024-02-04', TIMESTAMP '2024-02-04 05:06:07', 9.99, FALSE)"""
        ],
        qualified_select_sql="""SELECT id AS id_value, "Mixed Case" AS mixed_value,
            "special-name" AS special_value, nullable_text AS nullable_value,
            event_date AS event_date_value, event_ts AS event_ts_value,
            amount AS amount_value, bool_flag AS bool_value
            FROM "contract"."main"."contract_rows" ORDER BY id""",
        limit_sql='SELECT id FROM "contract_rows" ORDER BY id LIMIT 1',
        schema_kwargs={"database_name": "contract", "schema_name": "main"},
        expected_columns=(
            "id",
            "Mixed Case",
            "special-name",
            "nullable_text",
            "event_date",
            "event_ts",
            "amount",
            "bool_flag",
        ),
    )
    try:
        contract.assert_table_contract(connector, case)
        result = connector.execute_query("SELECT '数据' AS nullable_text", result_format="list")
        assert result.success is True
        assert result.sql_return == [{"nullable_text": "数据"}]
    finally:
        connector.close()
