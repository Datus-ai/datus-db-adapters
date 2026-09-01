# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from dataclasses import dataclass

import pyarrow as pa
import pytest
from snowflake.connector.errors import NotSupportedError

from datus_snowflake import SnowflakeConnector


@dataclass
class ResultColumn:
    name: str


class JsonResultCursor:
    def __init__(self):
        self.description = [ResultColumn("plan")]
        self.rowcount = 2
        self.executed = []
        self.fetch_arrow_calls = 0
        self.fetchall_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetch_arrow_all(self, *, force_return_table):
        assert force_return_table is True
        self.fetch_arrow_calls += 1
        raise NotSupportedError

    def fetchall(self):
        self.fetchall_calls += 1
        return [("GlobalStats",), ("Result",)]


class JsonResultConnection:
    def __init__(self):
        self.cursor_obj = JsonResultCursor()

    def cursor(self):
        return self.cursor_obj


@pytest.fixture
def connector() -> SnowflakeConnector:
    instance = SnowflakeConnector.__new__(SnowflakeConnector)
    instance.connection = JsonResultConnection()
    instance.dialect = "snowflake"
    return instance


def test_execute_query_arrow_falls_back_to_rows_for_json_result(connector: SnowflakeConnector):
    result = connector.execute_query("EXPLAIN SELECT 1", result_format="arrow")

    assert result.success
    assert result.result_format == "arrow"
    assert isinstance(result.sql_return, pa.Table)
    assert result.sql_return.to_pylist() == [{"plan": "GlobalStats"}, {"plan": "Result"}]
    assert result.row_count == 2
    assert connector.connection.cursor_obj.fetch_arrow_calls == 1
    assert connector.connection.cursor_obj.fetchall_calls == 1


def test_execute_query_list_falls_back_to_rows_for_json_result(connector: SnowflakeConnector):
    result = connector.execute_query("EXPLAIN SELECT 1", result_format="list")

    assert result.success
    assert result.result_format == "list"
    assert result.sql_return == [{"plan": "GlobalStats"}, {"plan": "Result"}]
