"""Exercise file-export streaming leases through real SQLite."""

import sqlite3

import pytest
from sqlalchemy import event

from datus_sqlalchemy import SQLAlchemyConnector


class BatchConnector(SQLAlchemyConnector):
    def get_databases(self, catalog_name="", include_sys=False):
        return []

    def do_switch_context(self, conn, catalog_name="", database_name="", schema_name=""):
        pass


@pytest.fixture
def connector(tmp_path):
    connector = BatchConnector(f"sqlite:///{tmp_path / 'batch.db'}", dialect="sqlite")
    try:
        yield connector
    finally:
        connector.close()


def test_stream_preserves_values_and_releases_lease_on_close(connector):
    sql = "SELECT '0012345678901234567' AS id UNION ALL SELECT '1721599112147287481'"
    batches = connector.iter_query_batches(sql, batch_size=1)
    assert next(batches).to_pylist() == [{"id": "0012345678901234567"}]
    assert connector.engine.pool.checkedout() == 1
    batches.close()
    assert connector.engine.pool.checkedout() == 0
    assert [batch.to_pylist() for batch in connector.iter_query_batches(sql, 1)] == [
        [{"id": "0012345678901234567"}],
        [{"id": "1721599112147287481"}],
    ]


def test_empty_result_retains_columns(connector):
    batches = list(connector.iter_query_batches("SELECT 1 AS id, '数据' AS name WHERE 0", 2))
    assert len(batches) == 1
    assert batches[0].column_names == ["id", "name"]
    assert batches[0].num_rows == 0


@pytest.mark.parametrize("batch_size", [0, -1, True, 1.5])
def test_invalid_batch_size_is_rejected_before_execution(connector, batch_size):
    with pytest.raises(ValueError, match="positive integer"):
        next(connector.iter_query_batches("SELECT 1", batch_size))


def test_abandoned_stream_invalidates_transport_before_cursor_close(tmp_path):
    events = []

    class TrackedCursor(sqlite3.Cursor):
        def close(self):
            events.append("cursor_close")
            return super().close()

    class TrackedConnection(sqlite3.Connection):
        def cursor(self, *args, **kwargs):
            return super().cursor(*args, **{**kwargs, "factory": TrackedCursor})

    class TrackedConnector(BatchConnector):
        def _create_engine(self, connection_string, **options):
            options["creator"] = lambda: sqlite3.connect(tmp_path / "tracked.db", factory=TrackedConnection)
            return super()._create_engine(connection_string, **options)

    connector = TrackedConnector("sqlite://", dialect="sqlite")
    try:

        @event.listens_for(connector._ensure_engine(), "invalidate")
        def invalidated(*args):
            events.append("invalidated")

        batches = connector.iter_query_batches("SELECT 1 AS id UNION ALL SELECT 2", batch_size=1)
        assert next(batches).to_pylist() == [{"id": 1}]
        events.clear()
        batches.close()
        assert events == ["invalidated", "cursor_close"]
        assert [batch.to_pylist() for batch in connector.iter_query_batches("SELECT 3 AS id")] == [[{"id": 3}]]
    finally:
        connector.close()
