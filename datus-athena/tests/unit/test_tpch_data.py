import pytest
import sqlglot

from datus_athena.tpch_data import build_tpch_ctas
from datus_db_core.testing.tpch import ROW_COUNTS, TPCH_TABLES


def test_tpch_uses_shared_rows_and_is_valid_athena_sql():
    statements = build_tpch_ctas("s3://fixture-bucket/isolated/", "test")
    assert len(statements) == len(TPCH_TABLES)
    for sql, name, count in zip(statements, TPCH_TABLES, ROW_COUNTS, strict=True):
        tree = sqlglot.parse_one(sql, read="athena")
        values = tree.find(sqlglot.exp.Values)
        assert len(values.expressions) == count
        assert f'CREATE TABLE "test"."{name}"' in sql
        assert f"external_location='s3://fixture-bucket/isolated/{name}/'" in sql


def test_fixture_prefix_cannot_be_bucket_root():
    with pytest.raises(ValueError, match="dedicated"):
        build_tpch_ctas("s3://bucket/", "test")
