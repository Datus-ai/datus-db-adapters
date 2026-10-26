# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from datus_gaussdb import GaussDBConnector


@pytest.mark.parametrize("tables", [["orders"], None, []])
def test_ddl_metadata_predicate_reaches_database(tables):
    connector = GaussDBConnector(
        dict(host="localhost", username="u", password="p", database="db", schema_name="public")
    )
    engine = MagicMock()
    connection = engine.connect.return_value
    connection.exec_driver_sql.return_value.fetchall.return_value = [
        SimpleNamespace(_asdict=lambda: {"table_schema": "public", "table_name": "orders"})
    ]
    with (
        patch.object(connector, "_ensure_engine", return_value=engine),
        patch.object(connector, "_get_engine", return_value=engine),
    ):
        rows = connector._get_metadata(tables=tables)
    assert [row["table_name"] for row in rows] == ["orders"]
    sql = connection.exec_driver_sql.call_args.args[0]
    assert ("table_name IN ('orders')" in sql) == bool(tables)
    assert "public" in sql
    connection.close.assert_called_once()


def test_missing_target_does_not_fall_back_to_full_catalog():
    connector = GaussDBConnector(
        dict(host="localhost", username="u", password="p", database="db", schema_name="public")
    )
    engine = MagicMock()
    connection = engine.connect.return_value

    def execute(sql, **kwargs):
        result = MagicMock()
        result.fetchall.return_value = (
            [SimpleNamespace(_asdict=lambda: {"nspname": "public"})] if "pg_namespace" in sql else []
        )
        return result

    connection.exec_driver_sql.side_effect = execute
    with (
        patch.object(connector, "_ensure_engine", return_value=engine),
        patch.object(connector, "_get_engine", return_value=engine),
    ):
        rows = connector.get_tables_with_ddl(tables=["missing"])
    assert rows == []
    calls = connection.exec_driver_sql.call_args_list
    assert len(calls) == 2
    assert all("table_name IN ('missing')" in call.args[0] for call in calls if "pg_namespace" not in call.args[0])
    assert not any("lower(" in call.args[0] for call in calls)
