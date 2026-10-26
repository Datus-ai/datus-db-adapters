# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from datus_starrocks import StarRocksConnector


@pytest.mark.parametrize(
    "name, literal",
    [
        ("orders_1", r"'orders\\_1'"),
        ("sales%growth", r"'sales\\%growth'"),
        ("a\\b", r"'a\\\\b'"),
        ("owner's", "'owner''s'"),
    ],
)
@pytest.mark.parametrize("missing", [False, True])
def test_targeted_show_uses_literal_like_without_information_schema_rewrite(name, literal, missing):
    connector = StarRocksConnector(dict(host="localhost", username="u", database="db"))
    engine = MagicMock()
    connection = engine.connect.return_value
    queries = []

    def execute(sql, **kwargs):
        queries.append(sql)
        if "information_schema" in sql:
            raise RuntimeError("THRIFT_EAGAIN")
        if sql.startswith("SHOW FULL TABLES"):
            assert sql == f"SHOW FULL TABLES FROM `requested`.`db` LIKE {literal}"
            rows = [] if missing else [{"Tables_in_db": name, "Table_type": "BASE TABLE"}]
        else:
            escaped = name.replace("`", "``")
            assert sql == f"SHOW CREATE TABLE `requested`.`db`.`{escaped}`"
            rows = [{"Table": name, "Create Table": "CREATE TABLE fixture (id BIGINT)"}]
        result = MagicMock()
        result.fetchall.return_value = [SimpleNamespace(_asdict=lambda row=row: row) for row in rows]
        return result

    connection.exec_driver_sql.side_effect = execute
    with patch.object(connector, "_ensure_engine", return_value=engine):
        entries = connector.get_tables_with_ddl(catalog_name="requested", tables=[name])
    assert [entry["table_name"] for entry in entries] == ([] if missing else [name])
    assert [entry["definition"] for entry in entries] == ([] if missing else ["CREATE TABLE fixture (id BIGINT)"])
    assert len(queries) == (2 if missing else 3)
    assert any(str(call.args[0]) == "SET CATALOG `requested`" for call in connection.execute.call_args_list)
