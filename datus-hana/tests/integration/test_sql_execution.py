import pytest

from datus_db_core import DatusDbException

pytestmark = pytest.mark.integration


def test_bound_unicode_crud_and_transactions(connector, metadata_objects_setup):
    table = metadata_objects_setup["table_ref"]
    result = connector.execute_query(f"INSERT INTO {table} VALUES (?, ?)", parameters=(2, "数据"))
    assert result.success and result.row_count == 1
    result = connector.execute_update(f'UPDATE {table} SET "text" = \'更新\' WHERE "Mixed Column" = 2')
    assert result.success and result.row_count == 1
    result = connector.execute_query(f'SELECT "text" FROM {table} WHERE "Mixed Column" = ?', "list", parameters=(2,))
    assert result.success and result.sql_return == [{"text": "更新"}]
    result = connector.execute_delete(f'DELETE FROM {table} WHERE "Mixed Column" = 2')
    assert result.success and result.row_count == 1
    with pytest.raises(DatusDbException):
        connector.execute_queries(
            [f"INSERT INTO {table} VALUES (3, 'rollback')", "INSERT INTO no_such_hana_table VALUES (1)"]
        )
    result = connector.execute_query(f'SELECT COUNT(*) AS "n" FROM {table} WHERE "Mixed Column" = 3', "list")
    assert result.success and result.sql_return == [{"n": 0}]


def test_invalid_sql_is_error_result(connector):
    result = connector.execute_query("SELECT * FROM", "list")
    assert not result.success and result.error
