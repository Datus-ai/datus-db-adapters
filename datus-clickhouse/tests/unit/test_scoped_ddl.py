# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from datus_clickhouse import ClickHouseConnector


@pytest.mark.parametrize("tables", [["orders"], None, []])
def test_ddl_metadata_predicate_reaches_database(tables):
    connector = ClickHouseConnector(dict(host="localhost", username="u", password="p", database="db"))
    engine = MagicMock()
    connection = engine.connect.return_value
    connection.exec_driver_sql.return_value.fetchall.return_value = [
        SimpleNamespace(_asdict=lambda: {"TABLE_SCHEMA": "db", "TABLE_NAME": "orders"})
    ]
    with patch.object(connector, "_ensure_engine", return_value=engine):
        rows = connector._get_metadata(tables=tables)
    assert [row["table_name"] for row in rows] == ["orders"]
    sql = connection.exec_driver_sql.call_args.args[0]
    assert ("TABLE_NAME IN ('orders')" in sql) == bool(tables)
    assert "db" in sql
    connection.close.assert_called_once()


def test_missing_target_does_not_fall_back_to_full_catalog():
    connector = ClickHouseConnector(dict(host="localhost", username="u", password="p", database="db"))
    engine = MagicMock()
    connection = engine.connect.return_value
    connection.exec_driver_sql.return_value.fetchall.return_value = []
    with patch.object(connector, "_ensure_engine", return_value=engine):
        rows = connector.get_tables_with_ddl(tables=["missing"])
    assert rows == []
    calls = connection.exec_driver_sql.call_args_list
    assert len(calls) == 1
    assert all("TABLE_NAME IN ('missing')" in call.args[0] for call in calls)
