# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

from unittest.mock import MagicMock

import pyarrow as pa
import pytest

from datus_snowflake import SnowflakeConnector


@pytest.mark.parametrize("missing", [False, True])
@pytest.mark.parametrize("name, literal", [("ORDERS", "'ORDERS'"), ("owner's\\table", "'owner''s\\\\table'")])
def test_targeted_metadata_uses_filtered_query_instead_of_show(missing, name, literal):
    connector = SnowflakeConnector.__new__(SnowflakeConnector)
    connector.dialect = "snowflake"
    connector.connection = MagicMock()
    cursor = connector.connection.cursor.return_value.__enter__.return_value
    cursor.fetch_arrow_all.return_value = pa.table(
        {
            "database_name": [] if missing else ["DB"],
            "schema_name": [] if missing else ["PUBLIC"],
            "name": [] if missing else [name],
        }
    )
    rows = connector._get_tables_per_db(database_name="DB", schema_name="PUBLIC", tables=[name], table_type="table")
    assert [row["table_name"] for row in rows] == ([] if missing else [name])
    queries = [call.args[0] for call in cursor.execute.call_args_list]
    assert len(queries) == 2
    assert 'FROM "DB".INFORMATION_SCHEMA.TABLES' in queries[1]
    assert "TABLE_SCHEMA = 'PUBLIC'" in queries[1]
    assert f"TABLE_NAME IN  ({literal})" in queries[1]


@pytest.mark.parametrize("fail_show", [False, True])
def test_bulk_show_fallback_still_returns_metadata(fail_show):
    connector = SnowflakeConnector.__new__(SnowflakeConnector)
    connector.dialect = "snowflake"
    connector.connection = MagicMock()
    cursor = connector.connection.cursor.return_value.__enter__.return_value
    payload = pa.table({"database_name": ["DB"], "schema_name": ["PUBLIC"], "name": ["ORDERS"]})
    cursor.fetch_arrow_all.return_value = payload
    cursor.description = [("database_name",), ("schema_name",), ("name",)]
    cursor.fetchall.side_effect = RuntimeError("SHOW unavailable") if fail_show else None
    cursor.fetchall.return_value = [{"database_name": "DB", "schema_name": "PUBLIC", "name": "ORDERS"}]
    rows = connector._get_tables_per_db(database_name="DB", schema_name="PUBLIC", table_type="table")
    assert [row["table_name"] for row in rows] == ["ORDERS"]
    queries = [call.args[0] for call in cursor.execute.call_args_list]
    assert 'SHOW TERSE TABLES IN DATABASE "DB"' in queries
    assert any("INFORMATION_SCHEMA.TABLES" in sql for sql in queries) == fail_show
