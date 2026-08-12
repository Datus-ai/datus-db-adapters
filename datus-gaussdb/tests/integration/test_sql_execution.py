# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""SQL execution against a live GaussDB / openGauss server.

The bound-parameter tests are the important ones: GaussDB silently converts
binary-format bound parameters (int, date, ...) into NULL, which the dialect
works around by forcing a client-side interpolating cursor. Reading the exact
values back is what proves that workaround is in place.
"""

import uuid
from datetime import date, datetime

import pandas as pd
import pytest
from sqlalchemy import text

from datus_gaussdb import GaussDBConnector


@pytest.fixture
def typed_table(connector: GaussDBConnector):
    """Table covering the parameter types that GaussDB nulls out when binary-bound."""
    table_name = f"exec_{uuid.uuid4().hex[:8]}"
    connector.execute_ddl(
        f"""
        CREATE TABLE {table_name} (
            id INTEGER,
            name VARCHAR(50),
            event_date DATE,
            event_ts TIMESTAMP,
            amount DECIMAL(10, 2)
        )
        """
    )
    try:
        yield table_name
    finally:
        connector.execute_ddl(f"DROP TABLE IF EXISTS {table_name}")


def _rows(connector: GaussDBConnector, sql: str):
    result = connector.execute_query(sql, result_format="list")
    assert result.success, result.error
    return result.sql_return


# ==================== Bound Parameters ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_bound_parameters_round_trip_exact_values(connector: GaussDBConnector, typed_table: str):
    """Regression: bound int/date/timestamp values must not come back NULL."""
    with connector._conn() as conn:
        conn.execute(
            text(
                f"INSERT INTO {typed_table} (id, name, event_date, event_ts, amount) "
                "VALUES (:id, :name, :event_date, :event_ts, :amount)"
            ),
            {
                "id": 42,
                "name": "alpha",
                "event_date": date(2024, 2, 3),
                "event_ts": datetime(2024, 2, 3, 4, 5, 6),
                "amount": 123.45,
            },
        )
        conn.commit()

    row = _rows(connector, f"SELECT id, name, event_date, event_ts, amount FROM {typed_table}")[0]

    assert row["id"] == 42
    assert row["name"] == "alpha"
    assert str(row["event_date"]).startswith("2024-02-03")
    assert "2024-02-03" in str(row["event_ts"])
    assert float(row["amount"]) == pytest.approx(123.45)


@pytest.mark.integration
@pytest.mark.acceptance
def test_bound_integer_parameter_in_where_clause(connector: GaussDBConnector, typed_table: str):
    """A bound int in a predicate must match, not silently compare against NULL."""
    connector.execute_insert(f"INSERT INTO {typed_table} (id, name) VALUES (1, 'one'), (2, 'two')")

    with connector._conn() as conn:
        matched = conn.execute(text(f"SELECT name FROM {typed_table} WHERE id = :id"), {"id": 2}).fetchall()

    assert [row[0] for row in matched] == ["two"]


@pytest.mark.integration
@pytest.mark.acceptance
def test_executemany_batch_insert(connector: GaussDBConnector, typed_table: str):
    """Batched parameter sets keep their values through the client-side cursor."""
    payload = [{"id": index, "name": f"row-{index}"} for index in range(1, 6)]

    with connector._conn() as conn:
        conn.execute(text(f"INSERT INTO {typed_table} (id, name) VALUES (:id, :name)"), payload)
        conn.commit()

    rows = _rows(connector, f"SELECT id, name FROM {typed_table} ORDER BY id")

    assert [row["id"] for row in rows] == [1, 2, 3, 4, 5]
    assert [row["name"] for row in rows] == [f"row-{index}" for index in range(1, 6)]


# ==================== DML ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_execute_insert_update_delete(connector: GaussDBConnector, typed_table: str):
    """The insert/update/delete verbs report the rows they touched."""
    inserted = connector.execute_insert(f"INSERT INTO {typed_table} (id, name) VALUES (1, 'alpha'), (2, 'beta')")
    assert inserted.success, inserted.error
    assert inserted.row_count == 2

    updated = connector.execute_update(f"UPDATE {typed_table} SET name = 'alpha2' WHERE id = 1")
    assert updated.success, updated.error
    assert updated.row_count == 1
    assert _rows(connector, f"SELECT name FROM {typed_table} WHERE id = 1") == [{"name": "alpha2"}]

    deleted = connector.execute_delete(f"DELETE FROM {typed_table} WHERE id = 2")
    assert deleted.success, deleted.error
    assert deleted.row_count == 1
    assert [row["id"] for row in _rows(connector, f"SELECT id FROM {typed_table}")] == [1]


@pytest.mark.integration
def test_execute_ddl_alter(connector: GaussDBConnector, typed_table: str):
    """ALTER TABLE goes through execute_ddl and is visible in the schema."""
    result = connector.execute_ddl(f"ALTER TABLE {typed_table} ADD COLUMN note VARCHAR(20)")

    assert result.success, result.error
    columns = {column["name"] for column in connector.get_schema(table_name=typed_table)}
    assert "note" in columns


# ==================== pandas ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_execute_pandas_dtypes(connector: GaussDBConnector, typed_table: str):
    """Numeric columns arrive as numeric dtypes, not as objects or NaN."""
    connector.execute_insert(f"INSERT INTO {typed_table} (id, name) VALUES (1, 'alpha'), (2, 'beta')")

    result = connector.execute_pandas(f"SELECT id, name FROM {typed_table} ORDER BY id")

    assert result.success, result.error
    df = result.sql_return
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    assert pd.api.types.is_integer_dtype(df["id"])
    assert df["id"].tolist() == [1, 2]
    assert df["name"].tolist() == ["alpha", "beta"]


# ==================== A-mode Semantics ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_a_mode_stores_empty_string_as_null(connector: GaussDBConnector, typed_table: str, compat_mode: str):
    """In 'A' (Oracle) mode an inserted '' is stored — and read back — as NULL."""
    if compat_mode != "A":
        pytest.skip(f"empty-string-as-NULL is 'A' mode behavior; database runs in '{compat_mode}' mode")

    connector.execute_insert(f"INSERT INTO {typed_table} (id, name) VALUES (1, '')")

    row = _rows(connector, f"SELECT name FROM {typed_table} WHERE id = 1")[0]

    assert row["name"] is None
    assert _rows(connector, f"SELECT count(*) AS c FROM {typed_table} WHERE name IS NULL")[0]["c"] == 1
    assert _rows(connector, f"SELECT count(*) AS c FROM {typed_table} WHERE name = ''")[0]["c"] == 0


# ==================== Error Handling ====================


@pytest.mark.integration
def test_syntax_error_is_reported(connector: GaussDBConnector):
    """A malformed statement returns a failed result with an error message."""
    result = connector.execute_query("SELECT FROM WHERE", result_format="list")

    assert not result.success
    assert result.error


@pytest.mark.integration
def test_missing_table_is_reported(connector: GaussDBConnector):
    """Querying a non-existent table fails cleanly."""
    result = connector.execute_query(f"SELECT * FROM missing_{uuid.uuid4().hex[:8]}", result_format="list")

    assert not result.success
    assert result.error
