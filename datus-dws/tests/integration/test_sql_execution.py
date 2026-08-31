# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

"""Query execution, parameter binding, transactions and result formats."""

import pandas as pd
import pyarrow as pa
import pytest
from sqlalchemy import text

from datus_dws import DWSConnector


def _assert_success(result, operation: str):
    assert result.success, f"{operation} failed: {result.error}"


@pytest.mark.integration
@pytest.mark.acceptance
def test_query_returns_expected_rows(dws_objects: DWSConnector):
    schema = dws_objects.schema_name
    result = dws_objects.execute(
        {"sql_query": f'SELECT id, name FROM "{schema}"."t_row_hash" ORDER BY id'},
        result_format="list",
    )

    _assert_success(result, "ordered select")
    assert result.sql_return == [
        {"id": 1, "name": "alpha"},
        {"id": 2, "name": "beta"},
        {"id": 3, "name": "gamma"},
    ]


@pytest.mark.integration
@pytest.mark.acceptance
def test_all_result_formats(dws_objects: DWSConnector):
    query = {"sql_query": "SELECT 1 AS id, 'alpha'::text AS name"}

    list_result = dws_objects.execute(query, result_format="list")
    csv_result = dws_objects.execute(query, result_format="csv")
    pandas_result = dws_objects.execute(query, result_format="pandas")
    arrow_result = dws_objects.execute(query, result_format="arrow")

    for name, result in (
        ("list", list_result),
        ("csv", csv_result),
        ("pandas", pandas_result),
        ("arrow", arrow_result),
    ):
        _assert_success(result, f"{name} format query")

    assert list_result.sql_return == [{"id": 1, "name": "alpha"}]
    assert "id,name" in csv_result.sql_return
    assert isinstance(pandas_result.sql_return, pd.DataFrame)
    assert isinstance(arrow_result.sql_return, pa.Table)


@pytest.mark.integration
@pytest.mark.acceptance
def test_bound_parameters(dws_objects: DWSConnector):
    schema = dws_objects.schema_name
    with dws_objects._conn() as conn:
        scalar = conn.execute(text("SELECT CAST(:value AS INTEGER) + 1"), {"value": 41}).scalar()
        assert scalar == 42

        rows = conn.execute(
            text(f'SELECT name FROM "{schema}"."t_row_hash" WHERE id = :id'),
            {"id": 2},
        ).fetchall()
        assert [row[0] for row in rows] == ["beta"]


@pytest.mark.integration
def test_parameter_binding_does_not_interpolate_sql(dws_objects: DWSConnector):
    """A quote in a bound value must stay data, not become syntax."""
    with dws_objects._conn() as conn:
        value = conn.execute(text("SELECT CAST(:v AS VARCHAR)"), {"v": "o'brien"}).scalar()

    assert value == "o'brien"


@pytest.mark.integration
def test_transaction_commit_and_rollback(connector: DWSConnector):
    schema = connector.schema_name
    table = f'"{schema}"."t_txn"'
    _assert_success(
        connector.execute_ddl(f"CREATE TABLE {table} (id INTEGER) DISTRIBUTE BY HASH (id)"),
        "create transaction table",
    )
    try:
        with connector._conn() as conn:
            conn.execute(text(f"INSERT INTO {table} VALUES (1)"))
            conn.commit()

        with connector._conn() as conn:
            conn.execute(text(f"INSERT INTO {table} VALUES (2)"))
            conn.rollback()

        result = connector.execute({"sql_query": f"SELECT id FROM {table} ORDER BY id"}, result_format="list")
        _assert_success(result, "read back after transactions")
        assert result.sql_return == [{"id": 1}]
    finally:
        connector.execute_ddl(f"DROP TABLE IF EXISTS {table}")


@pytest.mark.integration
def test_sample_rows(dws_objects: DWSConnector):
    samples = dws_objects.get_sample_rows(tables=["t_row_hash"], top_n=2, schema_name=dws_objects.schema_name)

    assert len(samples) == 1
    assert samples[0]["table_name"] == "t_row_hash"
    assert "alpha" in samples[0]["sample_rows"] or "beta" in samples[0]["sample_rows"]


@pytest.mark.integration
def test_failing_query_reports_error_without_raising(dws_objects: DWSConnector):
    result = dws_objects.execute(
        {"sql_query": "SELECT * FROM a_table_that_does_not_exist_here"},
        result_format="list",
    )

    assert result.success is False
    assert result.error
