# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from types import SimpleNamespace

import pytest
from datus.storage.backends.vector.interfaces import And, Condition, Not, Op, Or
from datus.utils.exceptions import DatusException
from datus_postgresql.vector_backend import PgVectorBackend, PgVectorTable
from sqlalchemy import Column, Integer, MetaData, Table, Text


def _build_vector_table() -> PgVectorTable:
    table = Table(
        "semantic_model",
        MetaData(),
        Column("namespace", Text),
        Column("kind", Text),
        Column("name", Text),
        Column("score", Integer),
        Column("vector", Text),
        schema="public",
    )
    spec = SimpleNamespace(vector_column="vector", text_source="name")
    return PgVectorTable(
        engine=object(),
        table=table,
        spec=spec,
        namespace="ns_test",
        distance_op="<=>",
        fts_enabled=True,
    )


def test_compile_where_sql_supports_nested_condition_nodes():
    vector_table = _build_vector_table()
    where_expr = And(
        [
            Condition("kind", Op.EQ, "metric"),
            Or(
                [
                    Condition("score", Op.GTE, 10),
                    Not(Condition("name", Op.LIKE, "%tmp%")),
                ]
            ),
        ]
    )

    clause, params = vector_table._compile_where_sql(where_expr)

    assert '"kind" = :where_and_0_value' in clause
    assert '"score" >= :where_and_1_or_0_value' in clause
    assert 'NOT ("name" LIKE :where_and_1_or_1_not_value)' in clause
    assert params == {
        "where_and_0_value": "metric",
        "where_and_1_or_0_value": 10,
        "where_and_1_or_1_not_value": "%tmp%",
    }


def test_compile_where_sql_empty_in_returns_false_clause():
    vector_table = _build_vector_table()

    clause, params = vector_table._compile_where_sql(Condition("name", Op.IN, []))

    assert clause == "1=0"
    assert params == {}


def test_compile_where_sql_unknown_column_raises():
    vector_table = _build_vector_table()

    with pytest.raises(ValueError, match="Unknown column"):
        vector_table._compile_where_sql(Condition("missing_col", Op.EQ, "x"))


class _ScalarResult:
    def __init__(self, value: bool):
        self._value = value

    def scalar(self) -> bool:
        return self._value


class _FakeConnection:
    def __init__(self, extension_enabled: bool):
        self._extension_enabled = extension_enabled

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, _stmt):
        return _ScalarResult(self._extension_enabled)


class _FakeEngine:
    def __init__(self, extension_enabled: bool):
        self._extension_enabled = extension_enabled

    def connect(self):
        return _FakeConnection(self._extension_enabled)


def test_backend_init_passes_when_vector_extension_is_available(monkeypatch):
    monkeypatch.setattr(PgVectorBackend, "_create_engine", lambda self, _: _FakeEngine(True))

    backend = PgVectorBackend(
        db_path="/tmp",
        connection_string="postgresql+psycopg2://u:p@localhost:5432/db",
        namespace="ns_test",
        ensure_vector_extension=True,
    )

    assert backend.name == "pgvector"


def test_backend_init_raises_clear_error_when_vector_extension_missing(monkeypatch):
    monkeypatch.setattr(PgVectorBackend, "_create_engine", lambda self, _: _FakeEngine(False))

    with pytest.raises(DatusException, match="CREATE EXTENSION IF NOT EXISTS vector"):
        PgVectorBackend(
            db_path="/tmp",
            connection_string="postgresql+psycopg2://u:p@localhost:5432/db",
            namespace="ns_test",
            ensure_vector_extension=True,
        )
