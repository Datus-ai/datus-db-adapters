# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Tests for Snowflake MigrationTargetMixin implementation."""

import pytest

from datus_db_core import MigrationTargetMixin
from datus_snowflake import SnowflakeConnector


@pytest.fixture
def connector():
    return SnowflakeConnector.__new__(SnowflakeConnector)


class TestMixinInheritance:
    def test_snowflake_is_migration_target(self, connector):
        assert isinstance(connector, MigrationTargetMixin)


class TestDescribeMigrationCapabilities:
    def test_supported_true(self, connector):
        assert connector.describe_migration_capabilities()["supported"] is True

    def test_dialect_family_snowflake(self, connector):
        assert connector.describe_migration_capabilities()["dialect_family"] == "snowflake"

    def test_no_hard_distribution_required(self, connector):
        """Snowflake handles partitioning transparently (micro-partitions)."""
        assert connector.describe_migration_capabilities()["requires"] == []

    def test_type_hints_mention_variant(self, connector):
        hints = connector.describe_migration_capabilities()["type_hints"]
        hints_str = " ".join(hints.values())
        assert "VARIANT" in hints_str.upper() or "variant" in hints_str

    def test_example_ddl_has_create_table(self, connector):
        ddl = connector.describe_migration_capabilities()["example_ddl"].upper()
        assert "CREATE" in ddl and "TABLE" in ddl


class TestValidateDdl:
    def test_accepts_standard_snowflake_ddl(self, connector):
        ddl = "CREATE TABLE db.schema.t (id NUMBER(38,0), name VARCHAR(16777216))"
        assert connector.validate_ddl(ddl) == []

    def test_accepts_cluster_by(self, connector):
        ddl = "CREATE TABLE db.schema.t (id NUMBER, dt DATE) CLUSTER BY (dt)"
        assert connector.validate_ddl(ddl) == []

    def test_rejects_starrocks_distributed_by(self, connector):
        ddl = "CREATE TABLE t (id BIGINT) DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 10"
        errors = connector.validate_ddl(ddl)
        assert any("DUPLICATE KEY" in e.upper() or "DISTRIBUTED BY" in e.upper() for e in errors)

    def test_rejects_clickhouse_engine_merge_tree(self, connector):
        ddl = "CREATE TABLE t (id BIGINT) ENGINE = MergeTree() ORDER BY id"
        errors = connector.validate_ddl(ddl)
        assert any("ENGINE" in e.upper() for e in errors)


class TestSuggestTableLayout:
    def test_returns_optional_cluster_by_hint(self, connector):
        """Snowflake CLUSTER BY is optional — prefer date/time columns when present."""
        columns = [
            {"name": "id", "type": "BIGINT", "nullable": False},
            {"name": "event_date", "type": "DATE", "nullable": False},
        ]
        layout = connector.suggest_table_layout(columns)
        # Layout may be empty (defer to LLM) OR suggest cluster_by on date column
        assert isinstance(layout, dict)
        if layout:
            assert "cluster_by" in layout

    def test_empty_columns_returns_dict(self, connector):
        assert isinstance(connector.suggest_table_layout([]), dict)


class TestMapSourceType:
    def test_hugeint_to_number(self, connector):
        assert connector.map_source_type("duckdb", "HUGEINT") == "NUMBER(38,0)"

    def test_json_to_variant(self, connector):
        assert connector.map_source_type("postgresql", "JSON") == "VARIANT"

    def test_jsonb_to_variant(self, connector):
        assert connector.map_source_type("postgresql", "JSONB") == "VARIANT"

    def test_unknown_returns_none(self, connector):
        assert connector.map_source_type("duckdb", "VARCHAR") is None
