import pytest

from datus_db_core.testing import contract

pytestmark = pytest.mark.integration


def test_common_adapter_contract(connector):
    case = contract.TableContractCase(
        adapter_name="sqlite",
        table_name="contract_rows",
        drop_sql='DROP TABLE IF EXISTS "contract_rows"',
        create_sql="""CREATE TABLE "contract_rows" (
            id INTEGER, "Mixed Case" TEXT, "special-name" TEXT,
            nullable_text TEXT, event_date TEXT, event_ts TEXT,
            amount NUMERIC, bool_flag INTEGER)""",
        insert_sqls=[
            """INSERT INTO "contract_rows" VALUES
            (1, 'Alpha', 'S-1', NULL, '2024-02-03', '2024-02-03 04:05:06', 123.45, 1),
            (2, 'Beta', 'S-2', '数据', '2024-02-04', '2024-02-04 05:06:07', 9.99, 0)"""
        ],
        qualified_select_sql="""SELECT id AS id_value, "Mixed Case" AS mixed_value,
            "special-name" AS special_value, nullable_text AS nullable_value,
            event_date AS event_date_value, event_ts AS event_ts_value,
            amount AS amount_value, bool_flag AS bool_value
            FROM "main"."contract_rows" ORDER BY id""",
        limit_sql='SELECT id FROM "contract_rows" ORDER BY id LIMIT 1',
        schema_kwargs={},
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
    contract.assert_table_contract(connector, case)
    assert connector.get_tables() == []
