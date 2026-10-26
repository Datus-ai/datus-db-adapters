# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from datus_hive import HiveConnector


@pytest.mark.parametrize("missing", [False, True])
def test_targeted_ddl_uses_filtered_catalog(missing):
    connector = HiveConnector(dict(host="localhost", username="u", database="db"))
    engine = MagicMock()
    connection = engine.connect.return_value
    queries = []

    def execute(sql, **kwargs):
        queries.append(sql)
        if sql.startswith("SHOW CREATE"):
            rows = [{"ddl": "CREATE TABLE orders (id INT)"}]
        elif sql.startswith("SHOW VIEWS"):
            assert "LIKE 'orders'" in sql
            rows = []
        else:
            assert "'orders'" in sql
            rows = [] if missing else [{"tab_name": "orders"}]
        result = MagicMock()
        result.fetchall.return_value = [SimpleNamespace(_asdict=lambda row=row: row) for row in rows]
        return result

    connection.exec_driver_sql.side_effect = execute
    with patch.object(connector, "_ensure_engine", return_value=engine):
        rows = connector.get_tables_with_ddl(tables=["orders"])
    assert [row["table_name"] for row in rows] == ([] if missing else ["orders"])
    assert [row["definition"] for row in rows] == ([] if missing else ["CREATE TABLE orders (id INT)"])
    assert len(queries) == (2 if missing else 3)
