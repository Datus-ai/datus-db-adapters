# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Exercise SQL literal preservation through real SQLAlchemy connections."""

import csv
import io
import sqlite3

import pytest
from sqlalchemy import create_engine, event

from datus_db_core import DatusDbException, ErrorCode
from datus_sqlalchemy import SQLAlchemyConnector


class LiteralConnector(SQLAlchemyConnector):
    def get_databases(self, catalog_name="", include_sys=False):
        return []

    def do_switch_context(self, conn, catalog_name="", database_name="", schema_name=""):
        pass


@pytest.fixture
def connector(tmp_path):
    connection = LiteralConnector(f"sqlite:///{tmp_path / 'literals.db'}", dialect="sqlite")
    try:
        yield connection
    finally:
        connection.close()


@pytest.mark.parametrize(
    "payload",
    [
        '{"customer_id":10,"tags":["vip",null],"active":true}',
        '{"inactive":false,"empty":null,"amount":-10.5}',
        "literal :name, :10, :true and :null",
        "50% / %s / %(name)s / %%",
        r"backslash\:name",
    ],
)
@pytest.mark.parametrize("result_format", ["list", "csv", "pandas", "arrow"])
def test_query_preserves_sql_literals(connector, payload, result_format):
    result = connector.execute_query(f"SELECT '{payload}' AS payload -- :comment", result_format=result_format)
    assert result.success, result.error
    assert result.row_count == 1
    if result_format == "list":
        rows = result.sql_return
    elif result_format == "csv":
        rows = list(csv.DictReader(io.StringIO(result.sql_return)))
    elif result_format == "pandas":
        rows = result.sql_return.to_dict(orient="records")
    else:
        rows = result.sql_return.to_pylist()
    assert rows == [{"payload": payload}]


@pytest.mark.parametrize("method", ["execute_pandas", "execute_csv", "execute_arrow", "execute_csv_iterator"])
def test_specialized_read_methods_preserve_literals(connector, method):
    payload = '{"n":10,"active":true,"empty":null,"note":"50% :name"}'
    result = getattr(connector, method)(f"SELECT '{payload}' AS payload")
    if method == "execute_csv_iterator":
        assert [tuple(row) for row in result] == [("payload",), (payload,)]
    else:
        assert result.success, result.error
        assert result.row_count == 1
        if method == "execute_pandas":
            rows = result.sql_return.to_dict(orient="records")
        elif method == "execute_csv":
            rows = list(csv.DictReader(io.StringIO(result.sql_return)))
        else:
            rows = result.sql_return.to_pylist()
        assert rows == [{"payload": payload}]


def test_ddl_and_writes_preserve_literals_and_commit(connector):
    first = '{"n":10,"active":true,"note":"50%"}'
    second = '{"n":20,"active":false,"empty":null}'
    ddl = connector.execute_ddl(f"CREATE TABLE documents (id INTEGER PRIMARY KEY, payload TEXT DEFAULT '{first}')")
    assert ddl.success, ddl.error
    for sql in ["INSERT INTO documents (id) VALUES (1)", f"INSERT INTO documents VALUES (2, '{first}')"]:
        inserted = connector.execute_insert(sql)
        assert inserted.success, inserted.error
        assert inserted.row_count == 1
    rows = connector.execute_query("SELECT id,payload FROM documents ORDER BY id", result_format="list")
    assert rows.success, rows.error
    assert rows.sql_return == [{"id": 1, "payload": first}, {"id": 2, "payload": first}]

    updated = connector.execute_update(f"UPDATE documents SET payload='{second}' WHERE payload='{first}'")
    assert updated.success, updated.error
    assert updated.row_count == 2
    deleted = connector.execute_delete(f"DELETE FROM documents WHERE id=1 AND payload='{second}'")
    assert deleted.success, deleted.error
    assert deleted.row_count == 1
    rows = connector.execute_query("SELECT id,payload FROM documents", result_format="list")
    assert rows.success, rows.error
    assert rows.sql_return == [{"id": 2, "payload": second}]


def test_batch_queries_preserve_literals_and_commit(connector):
    payload = '{"n":10,"active":true,"empty":null,"note":"50%"}'
    assert connector.execute_queries(
        [
            "CREATE TABLE documents (id INTEGER PRIMARY KEY, payload TEXT)",
            f"INSERT INTO documents VALUES (1, '{payload}')",
            "SELECT id,payload FROM documents",
        ]
    ) == [None, 1, [{"id": 1, "payload": payload}]]
    rows = connector.execute_query("SELECT payload FROM documents", result_format="list")
    assert rows.success, rows.error
    assert rows.sql_return == [{"payload": payload}]


def test_failed_batch_rolls_back_literal_inserts(connector):
    ddl = connector.execute_ddl("CREATE TABLE documents (id INTEGER PRIMARY KEY, payload TEXT)")
    assert ddl.success, ddl.error
    sql = 'INSERT INTO documents VALUES (1, \'{"n":10,"active":true}\')'
    with pytest.raises(DatusDbException) as raised:
        connector.execute_queries([sql, sql])
    assert raised.value.code == ErrorCode.DB_CONSTRAINT_VIOLATION
    rows = connector.execute_query("SELECT id,payload FROM documents", result_format="list")
    assert rows.success, rows.error
    assert rows.sql_return == []


@pytest.mark.parametrize("paramstyle", ["format", "pyformat"])
def test_percent_drivers_receive_sql_without_empty_parameters(paramstyle):
    """Format-style drivers interpolate '%' even for an empty parameter set."""
    calls = []

    class PercentCursor(sqlite3.Cursor):
        def execute(self, statement, *parameters):
            calls.append((statement, parameters))
            if parameters:
                statement = statement % parameters[0]
            return super().execute(statement)

    class PercentConnection(sqlite3.Connection):
        def cursor(self, *args, **kwargs):
            return super().cursor(*args, factory=PercentCursor, **kwargs)

    connection = LiteralConnector("sqlite://", dialect="sqlite")
    connection.engine = create_engine(
        "sqlite://", creator=lambda: sqlite3.connect(":memory:", factory=PercentConnection)
    )
    connection._owns_engine = True
    connection.engine.dialect.paramstyle = paramstyle
    connection.engine.dialect.positional = paramstyle == "format"
    sql = "SELECT '50% / %s / %(name)s / %%' AS payload"
    try:
        result = connection.execute_query(sql, result_format="list")
        assert result.success, result.error
        assert result.sql_return == [{"payload": "50% / %s / %(name)s / %%"}]
        assert calls[-1] == (sql, ())
    finally:
        connection.close()


def test_streaming_preserves_options_and_releases_connection_on_close(connector):
    observed = []
    engine = connector._ensure_engine()

    @event.listens_for(engine, "before_cursor_execute")
    def record(conn, cursor, statement, parameters, context, executemany):
        observed.append(context.execution_options)

    stream = connector.execute_csv_iterator("SELECT ':true' AS payload UNION ALL SELECT ':null'", max_rows=1)
    assert tuple(next(stream)) == ("payload",)
    assert tuple(next(stream)) == (":true",)
    stream.close()
    assert observed[0]["stream_results"] is True
    assert observed[0]["max_row_buffer"] == 1
    assert observed[0]["no_parameters"] is True
    assert engine.pool.checkedout() == 0
