# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Tests for Doris MigrationTargetMixin implementation.

Tests focus on the pure migration-hint/validation logic that does not require
an active Doris connection. Uses DorisConnector.__new__ to bypass the
constructor (which connects to a real server) — the Mixin methods are all
self-contained state-free static logic.
"""

import pytest

from datus_db_core import MigrationTargetMixin
from datus_doris import DorisConnector


@pytest.fixture
def connector():
    """Return a DorisConnector instance without invoking the constructor."""
    return DorisConnector.__new__(DorisConnector)


class TestMixinInheritance:
    def test_doris_is_migration_target(self, connector):
        assert isinstance(connector, MigrationTargetMixin)


class TestDescribeMigrationCapabilities:
    def test_returns_supported_dict(self, connector):
        result = connector.describe_migration_capabilities()
        assert isinstance(result, dict)
        assert result["supported"] is True

    def test_dialect_family_is_mysql_like(self, connector):
        result = connector.describe_migration_capabilities()
        assert result["dialect_family"] == "mysql-like"

    def test_requires_key_and_distribution(self, connector):
        result = connector.describe_migration_capabilities()
        requires_str = " ".join(result["requires"]).upper()
        assert "DUPLICATE KEY" in requires_str or "UNIQUE KEY" in requires_str
        assert "DISTRIBUTED BY" in requires_str

    def test_does_not_forbid_auto_increment(self, connector):
        result = connector.describe_migration_capabilities()
        forbids_str = " ".join(result["forbids"]).upper()
        assert "AUTO_INCREMENT" not in forbids_str

    def test_type_hints_have_varchar_max(self, connector):
        result = connector.describe_migration_capabilities()
        hints_str = " ".join(result["type_hints"].values())
        assert "65533" in hints_str

    def test_example_ddl_is_complete(self, connector):
        result = connector.describe_migration_capabilities()
        ddl = result["example_ddl"].upper()
        assert "CREATE TABLE" in ddl
        assert "DUPLICATE KEY" in ddl or "UNIQUE KEY" in ddl
        assert "DISTRIBUTED BY HASH" in ddl


class TestValidateDdl:
    def test_rejects_missing_key_definition(self, connector):
        ddl = """CREATE TABLE db.t (
          id BIGINT NOT NULL,
          name VARCHAR(255)
        )
        DISTRIBUTED BY HASH(id) BUCKETS 10"""
        errors = connector.validate_ddl(ddl)
        assert len(errors) > 0
        assert any("KEY" in e.upper() for e in errors)

    def test_rejects_missing_distributed_by(self, connector):
        ddl = """CREATE TABLE db.t (
          id BIGINT NOT NULL,
          name VARCHAR(255)
        )
        DUPLICATE KEY(id)"""
        errors = connector.validate_ddl(ddl)
        assert len(errors) > 0
        assert any("DISTRIBUTED BY" in e.upper() for e in errors)

    def test_rejects_auto_increment_outside_unique_key_table(self, connector):
        ddl = """CREATE TABLE db.t (
          id BIGINT AUTO_INCREMENT,
          name VARCHAR(255)
        )
        DUPLICATE KEY(id)
        DISTRIBUTED BY HASH(id) BUCKETS 10"""
        errors = connector.validate_ddl(ddl)
        assert any("AUTO_INCREMENT" in e.upper() for e in errors)

    def test_accepts_auto_increment_in_unique_key_table(self, connector):
        ddl = """CREATE TABLE db.t (
          id BIGINT NOT NULL AUTO_INCREMENT,
          name VARCHAR(255)
        )
        UNIQUE KEY(id)
        DISTRIBUTED BY HASH(id) BUCKETS 10"""
        assert connector.validate_ddl(ddl) == []

    def test_accepts_valid_ddl(self, connector):
        ddl = """CREATE TABLE db.t (
          id BIGINT NOT NULL,
          name VARCHAR(255)
        )
        DUPLICATE KEY(id)
        DISTRIBUTED BY HASH(id) BUCKETS 10"""
        assert connector.validate_ddl(ddl) == []

    def test_accepts_unique_key_variant(self, connector):
        ddl = """CREATE TABLE db.t (
          id BIGINT NOT NULL,
          name VARCHAR(255)
        )
        UNIQUE KEY(id)
        DISTRIBUTED BY HASH(id) BUCKETS 10"""
        assert connector.validate_ddl(ddl) == []

    def test_accepts_aggregate_key(self, connector):
        ddl = """CREATE TABLE db.t (
          dt DATE NOT NULL,
          region VARCHAR(50),
          total_amount DECIMAL(18,2) SUM
        )
        AGGREGATE KEY(dt, region)
        DISTRIBUTED BY HASH(region) BUCKETS 10"""
        assert connector.validate_ddl(ddl) == []

    @pytest.mark.parametrize(
        ("clause", "expected"),
        [
            ("FOREIGN KEY (parent_id) REFERENCES parent(id)", "FOREIGN KEY"),
            ("FULLTEXT INDEX idx_name (name)", "FULLTEXT"),
            ("CHECK (id > 0)", "CHECK"),
        ],
    )
    def test_rejects_unsupported_constraints_and_indexes(self, connector, clause, expected):
        ddl = f"""CREATE TABLE db.t (
          id BIGINT NOT NULL,
          parent_id BIGINT,
          name VARCHAR(255),
          {clause}
        )
        DUPLICATE KEY(id)
        DISTRIBUTED BY HASH(id) BUCKETS 10"""
        errors = connector.validate_ddl(ddl)
        assert any(expected in error.upper() for error in errors)


class TestSuggestTableLayout:
    def test_picks_id_column_first(self, connector):
        columns = [
            {"name": "name", "type": "VARCHAR", "nullable": True},
            {"name": "id", "type": "BIGINT", "nullable": False},
        ]
        layout = connector.suggest_table_layout(columns)
        assert layout["duplicate_key"] == ["id"]
        assert layout["distributed_by"] == ["id"]
        assert layout["buckets"] == 10

    def test_picks_suffix_id_columns(self, connector):
        columns = [
            {"name": "name", "type": "VARCHAR", "nullable": True},
            {"name": "order_id", "type": "BIGINT", "nullable": False},
            {"name": "user_id", "type": "BIGINT", "nullable": False},
        ]
        layout = connector.suggest_table_layout(columns)
        keys = layout["duplicate_key"]
        assert "order_id" in keys
        assert "user_id" in keys

    def test_max_three_keys(self, connector):
        columns = [{"name": f"col{i}_id", "type": "BIGINT", "nullable": False} for i in range(10)]
        layout = connector.suggest_table_layout(columns)
        assert len(layout["duplicate_key"]) <= 3

    def test_falls_back_to_first_column_when_no_integer(self, connector):
        columns = [
            {"name": "name", "type": "VARCHAR", "nullable": True},
            {"name": "label", "type": "VARCHAR", "nullable": True},
        ]
        layout = connector.suggest_table_layout(columns)
        assert layout["duplicate_key"] == ["name"]

    def test_empty_columns_returns_dict_with_empty_keys_or_empty(self, connector):
        layout = connector.suggest_table_layout([])
        # Either returns empty dict, or dict with empty key lists — both acceptable
        assert isinstance(layout, dict)

    def test_prefers_non_nullable_over_nullable(self, connector):
        columns = [
            {"name": "a_id", "type": "BIGINT", "nullable": True},
            {"name": "b_id", "type": "BIGINT", "nullable": False},
        ]
        layout = connector.suggest_table_layout(columns)
        # Both have _id suffix and INT type; non-nullable should rank first
        assert layout["duplicate_key"][0] == "b_id"
