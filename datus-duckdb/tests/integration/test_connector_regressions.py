"""Native DuckDB and SQLAlchemy coexistence and metadata regressions."""

import sys

import duckdb
import pytest
import sqlalchemy
from sqlalchemy.pool import StaticPool

from datus_duckdb import DuckDBConfig, DuckdbConnector

pytestmark = pytest.mark.integration


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "smoke.duckdb")


def test_connect_and_close(db_path):
    connector = DuckdbConnector(DuckDBConfig(db_path=db_path))
    connector.connect()
    assert isinstance(connector.connection, duckdb.DuckDBPyConnection)
    connector.connection.execute("SELECT 1").fetchone()

    connector.close()
    assert connector.connection is None


def test_in_memory_default_database_is_empty():
    """In-memory DuckDB must not derive a bogus ``:memory:`` database name.

    ``file_stem_from_uri(":memory:")`` yields ``":memory:"`` (no file stem to
    strip), but DuckDB names the in-memory database ``memory``. Pinning the
    connector to ``":memory:"`` made every metadata query filter on a name that
    matches no database, so ``catalog/list`` returned an empty list. Leaving the
    default empty lets listing fall back to enumerating all attached databases.
    """
    connector = DuckdbConnector(DuckDBConfig(db_path=":memory:"))
    try:
        assert connector.database_name == ""
        # get_databases() must surface the real in-memory database name.
        assert "memory" in connector.get_databases()
    finally:
        connector.close()


def test_coexist_with_sqlalchemy_duckdb_engine(db_path):
    """Regression: a second connection via SQLAlchemy+duckdb_engine on the same
    file in the same process must succeed. Pre-fix, DuckDB rejected it with
    'Can't open a connection to same database file with a different configuration'.
    """
    connector = DuckdbConnector(DuckDBConfig(db_path=db_path))
    connector.connect()
    connector.connection.execute("CREATE TABLE t(x INT)")
    connector.connection.execute("INSERT INTO t VALUES (1)")

    engine = sqlalchemy.create_engine(f"duckdb:///{db_path}", poolclass=StaticPool)
    try:
        raw = engine.raw_connection()
        cur = raw.cursor()
        cur.execute("SELECT COUNT(*) FROM t")
        assert cur.fetchone()[0] == 1
        raw.close()
    finally:
        engine.dispose()
        connector.close()


def test_falls_back_when_duckdb_engine_missing(db_path, monkeypatch):
    """If duckdb_engine isn't installed, connect() falls back to a bare
    duckdb.connect(self.db_path) without aligning custom_user_agent."""
    monkeypatch.setitem(sys.modules, "duckdb_engine", None)

    seen_kwargs: dict = {}
    original_connect = duckdb.connect

    def spy_connect(path, **kwargs):
        seen_kwargs.update(kwargs)
        return original_connect(path, **kwargs)

    monkeypatch.setattr(duckdb, "connect", spy_connect)

    connector = DuckdbConnector(DuckDBConfig(db_path=db_path))
    connector.connect()
    try:
        assert isinstance(connector.connection, duckdb.DuckDBPyConnection)
        # Fallback path must NOT pass a config dict.
        assert "config" not in seen_kwargs
    finally:
        connector.close()


def test_execute_query_arrow_counts_rows(db_path):
    connector = DuckdbConnector(DuckDBConfig(db_path=db_path))
    connector.execute_ddl("CREATE TABLE t AS SELECT 1 AS x UNION ALL SELECT 2 AS x")

    result = connector.execute_query("SELECT * FROM t ORDER BY x", result_format="arrow")

    try:
        assert result.success is True
        assert result.row_count == 2
        assert result.sql_return.num_rows == 2
    finally:
        connector.close()


@pytest.mark.parametrize("in_memory", [False, True])
def test_ddl_filter_resolves_the_default_schema(tmp_path, in_memory):
    db = DuckdbConnector({"uri": ":memory:" if in_memory else str(tmp_path / "metadata.duckdb")})
    try:
        assert db.execute_ddl("CREATE TABLE orders (order_id BIGINT)").success
        assert db.execute_ddl("CREATE SCHEMA archive").success
        assert db.execute_ddl("CREATE TABLE archive.orders (old_id BIGINT)").success
        default = db.get_tables_with_ddl(tables=["orders"])
        assert [(item["schema_name"], item["table_name"]) for item in default] == [("main", "orders")]
        assert "order_id" in default[0]["definition"]
        archive = db.get_tables_with_ddl(schema_name="archive", tables=["orders"])
        assert [(item["schema_name"], item["table_name"]) for item in archive] == [("archive", "orders")]
        assert "old_id" in archive[0]["definition"]
    finally:
        db.close()
