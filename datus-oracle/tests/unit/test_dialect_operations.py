# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pandas as pd
import pytest

from datus_db_core import DialectOperations
from datus_oracle import OracleDialectOperations
from datus_oracle.dialect_operations import quote_oracle_identifier


@pytest.fixture
def ops():
    return OracleDialectOperations()


@pytest.mark.acceptance
def test_conforms_to_protocol(ops):
    assert isinstance(ops, DialectOperations)


class TestRenderLimit:
    @pytest.mark.acceptance
    def test_basic(self, ops):
        assert ops.render_limit("SELECT * FROM t", 5) == "SELECT * FROM t FETCH FIRST 5 ROWS ONLY"

    def test_no_limit_keyword(self, ops):
        sql = "SELECT c, COUNT(*) AS n FROM t GROUP BY c ORDER BY n DESC"
        rendered = ops.render_limit(sql, 10)
        assert "LIMIT" not in rendered
        assert rendered.endswith("FETCH FIRST 10 ROWS ONLY")


class TestRenderCount:
    @pytest.mark.acceptance
    def test_no_as_before_table_alias(self, ops):
        rendered = ops.render_count("SELECT * FROM t WHERE x > 1", "__datus_src")
        assert rendered == "SELECT COUNT(*) AS __datus_count FROM (SELECT * FROM t WHERE x > 1) __datus_src"
        assert ") AS __datus_src" not in rendered


class TestQuoteIdentifier:
    @pytest.mark.acceptance
    def test_upper_cases(self, ops):
        assert ops.quote_identifier("orders") == '"ORDERS"'

    def test_reserved_word(self, ops):
        assert ops.quote_identifier("order") == '"ORDER"'

    def test_already_upper(self, ops):
        assert ops.quote_identifier("ORDERS") == '"ORDERS"'

    def test_strips_embedded_quotes(self, ops):
        assert quote_oracle_identifier('or"ders') == '"ORDERS"'


class TestInferTransferType:
    @pytest.mark.acceptance
    def test_bool_is_number_1(self, ops):
        assert ops.infer_transfer_type(pd.Series([True, False])) == "NUMBER(1)"

    def test_int(self, ops):
        assert ops.infer_transfer_type(pd.Series([1, 2, 3])) == "NUMBER(19)"

    def test_float(self, ops):
        assert ops.infer_transfer_type(pd.Series([1.5, 2.5])) == "BINARY_DOUBLE"

    def test_datetime(self, ops):
        assert ops.infer_transfer_type(pd.Series(pd.to_datetime(["2026-01-01"]))) == "TIMESTAMP"

    def test_datetime_tz(self, ops):
        series = pd.Series(pd.to_datetime(["2026-01-01"]).tz_localize("UTC"))
        assert ops.infer_transfer_type(series) == "TIMESTAMP WITH TIME ZONE"

    def test_string(self, ops):
        assert ops.infer_transfer_type(pd.Series(["a", "b"])) == "VARCHAR2(4000)"

    def test_object_date(self, ops):
        assert ops.infer_transfer_type(pd.Series([date(2026, 1, 1)])) == "DATE"

    def test_object_datetime(self, ops):
        assert ops.infer_transfer_type(pd.Series([datetime(2026, 1, 1, 12)])) == "TIMESTAMP"

    def test_object_decimal(self, ops):
        assert ops.infer_transfer_type(pd.Series([Decimal("1.23")])) == "NUMBER(38,10)"

    def test_object_bytes(self, ops):
        assert ops.infer_transfer_type(pd.Series([b"\x00\x01"])) == "BLOB"

    def test_all_null_defaults_to_text(self, ops):
        assert ops.infer_transfer_type(pd.Series([None, None], dtype=object)) == "VARCHAR2(4000)"


class TestWriteDataframe:
    def _connector_with_recorded_conn(self):
        conn = MagicMock()
        connector = MagicMock()

        @contextmanager
        def _fake_conn():
            yield conn

        connector._conn = _fake_conn
        return connector, conn

    @pytest.mark.acceptance
    def test_writes_with_bound_parameters(self):
        ops = OracleDialectOperations()
        connector, conn = self._connector_with_recorded_conn()
        df = pd.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"]})

        written = ops.write_dataframe(connector, '"SALES"."T"', df, batch_size=2)

        assert written == 3
        assert conn.execute.call_count == 2  # 2 batches: 2 rows + 1 row
        insert_sql = str(conn.execute.call_args_list[0].args[0])
        assert insert_sql == 'INSERT INTO "SALES"."T" ("ID", "NAME") VALUES (:p0, :p1)'
        # No inline values, no multi-row VALUES
        assert "VALUES (1" not in insert_sql
        first_batch_params = conn.execute.call_args_list[0].args[1]
        assert first_batch_params == [{"p0": 1, "p1": "a"}, {"p0": 2, "p1": "b"}]
        conn.commit.assert_called_once()

    @pytest.mark.acceptance
    def test_bools_bound_as_1_0(self):
        ops = OracleDialectOperations()
        connector, conn = self._connector_with_recorded_conn()
        df = pd.DataFrame({"flag": [True, False]})

        ops.write_dataframe(connector, "T", df, batch_size=10)

        params = conn.execute.call_args_list[0].args[1]
        assert params == [{"p0": 1}, {"p0": 0}]
        assert all(not isinstance(p["p0"], bool) for p in params)

    def test_none_values_pass_through(self):
        ops = OracleDialectOperations()
        connector, conn = self._connector_with_recorded_conn()
        df = pd.DataFrame({"v": ["x", None]})

        ops.write_dataframe(connector, "T", df, batch_size=10)

        params = conn.execute.call_args_list[0].args[1]
        assert params[1]["p0"] is None

    def test_empty_dataframe_writes_nothing(self):
        ops = OracleDialectOperations()
        connector, conn = self._connector_with_recorded_conn()
        df = pd.DataFrame({"id": pd.Series([], dtype="int64")})

        written = ops.write_dataframe(connector, "T", df, batch_size=10)

        assert written == 0
        conn.execute.assert_not_called()
        conn.commit.assert_called_once()
