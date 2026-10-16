import pytest

from datus_db_core.testing.contract import SelectContractCase, assert_select_contract

pytestmark = pytest.mark.integration


@pytest.mark.acceptance
def test_shared_select_contract(connector):
    values = """1 AS id_value, 'Alpha' AS mixed_value, 'S-1' AS special_value,
        CAST(NULL AS VARCHAR) AS nullable_value, DATE '2024-02-03' AS event_date_value,
        TIMESTAMP '2024-02-03 04:05:06' AS event_ts_value,
        DECIMAL '123.45' AS amount_value, TRUE AS bool_value"""
    table = connector.full_name(table_name="tpch_region")
    assert_select_contract(
        connector,
        SelectContractCase(
            adapter_name="athena",
            select_sql=f"SELECT {values}",
            limit_sql=f"SELECT r_regionkey FROM {table} LIMIT 1",
            qualified_sql=f"SELECT {values} FROM {table} WHERE r_regionkey = 0",
            dialect_select_sqls=("SELECT element_at(ARRAY[1,2], 2) AS second_value",),
        ),
    )
