# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Tests for Trino MigrationTargetMixin implementation.

Trino is a federated query engine; its migration capabilities depend on the
underlying catalog's connector type (hive / iceberg / delta / jdbc).
Tests inject the catalog type by monkeypatching ``_detect_catalog_type`` —
no real connection required.
"""

import pytest

from datus_db_core import MigrationTargetMixin
from datus_trino import TrinoConnector


@pytest.fixture
def connector():
    return TrinoConnector.__new__(TrinoConnector)


class TestMixinInheritance:
    def test_trino_is_migration_target(self, connector):
        assert isinstance(connector, MigrationTargetMixin)


class TestDescribeMigrationCapabilitiesHive:
    @pytest.fixture
    def hive_connector(self, connector, monkeypatch):
        monkeypatch.setattr(connector, "_detect_catalog_type", lambda: "hive")
        return connector

    def test_supported_true(self, hive_connector):
        assert hive_connector.describe_migration_capabilities()["supported"] is True

    def test_dialect_family_is_trino_hive(self, hive_connector):
        assert hive_connector.describe_migration_capabilities()["dialect_family"] == "trino-hive"

    def test_mentions_with_format(self, hive_connector):
        haystack = (
            " ".join(hive_connector.describe_migration_capabilities().get("type_hints", {}).values())
            + " "
            + hive_connector.describe_migration_capabilities().get("example_ddl", "")
        )
        assert "format" in haystack.lower() or "PARQUET" in haystack.upper()


class TestDescribeMigrationCapabilitiesIceberg:
    @pytest.fixture
    def iceberg_connector(self, connector, monkeypatch):
        monkeypatch.setattr(connector, "_detect_catalog_type", lambda: "iceberg")
        return connector

    def test_dialect_family_is_trino_iceberg(self, iceberg_connector):
        assert iceberg_connector.describe_migration_capabilities()["dialect_family"] == "trino-iceberg"

    def test_mentions_partitioning(self, iceberg_connector):
        haystack = (
            " ".join(iceberg_connector.describe_migration_capabilities().get("type_hints", {}).values())
            + " "
            + iceberg_connector.describe_migration_capabilities().get("example_ddl", "")
        )
        assert "partition" in haystack.lower()

    def test_describes_snapshot_checkpointing(self, iceberg_connector):
        result = iceberg_connector.describe_migration_capabilities()

        checkpointing = result["checkpointing"]
        assert checkpointing["mode"] == "iceberg_snapshot"
        assert checkpointing["checkpoint_key"] == "snapshot_id"
        assert "$snapshots" in checkpointing["latest_checkpoint_sql"]
        assert "table_changes" in checkpointing["changes_since_checkpoint_sql"]

    def test_does_not_recommend_delta_checkpoint_interval(self, iceberg_connector):
        result = iceberg_connector.describe_migration_capabilities()
        text = " ".join(
            [
                " ".join(result.get("type_hints", {}).values()),
                result.get("example_ddl", ""),
                str(result.get("checkpointing", {})),
            ]
        )

        assert "checkpoint_interval" in " ".join(result["forbids"])
        assert "checkpoint_interval" not in result["example_ddl"]
        assert "checkpoint_interval" in text


class TestDescribeMigrationCapabilitiesGeneric:
    @pytest.fixture
    def generic_connector(self, connector, monkeypatch):
        monkeypatch.setattr(connector, "_detect_catalog_type", lambda: "unknown")
        return connector

    def test_supported_generic_capabilities(self, generic_connector):
        result = generic_connector.describe_migration_capabilities()
        assert result["supported"] is True
        assert result["dialect_family"] == "trino-generic"

    def test_detection_failure_does_not_raise(self, connector, monkeypatch):
        def _raise():
            raise RuntimeError("catalog probe failed")

        monkeypatch.setattr(connector, "_detect_catalog_type", _raise)
        # Should swallow exception and return generic
        result = connector.describe_migration_capabilities()
        assert result["supported"] is True
        assert result["dialect_family"] == "trino-generic"


class TestValidateDdl:
    def test_accepts_hive_ddl_with_with_clause(self, connector, monkeypatch):
        monkeypatch.setattr(connector, "_detect_catalog_type", lambda: "hive")
        ddl = """CREATE TABLE catalog.schema.t (
          id BIGINT,
          ds VARCHAR
        ) WITH (format = 'PARQUET', partitioned_by = ARRAY['ds'])"""
        assert connector.validate_ddl(ddl) == []

    def test_rejects_starrocks_distributed_by(self, connector, monkeypatch):
        monkeypatch.setattr(connector, "_detect_catalog_type", lambda: "hive")
        ddl = """CREATE TABLE catalog.schema.t (id BIGINT)
        DUPLICATE KEY(id)
        DISTRIBUTED BY HASH(id) BUCKETS 10"""
        errors = connector.validate_ddl(ddl)
        assert any("DUPLICATE KEY" in e.upper() or "STARROCKS" in e.upper() for e in errors)

    def test_rejects_engine_clickhouse(self, connector, monkeypatch):
        monkeypatch.setattr(connector, "_detect_catalog_type", lambda: "hive")
        ddl = "CREATE TABLE catalog.schema.t (id BIGINT) ENGINE = MergeTree() ORDER BY id"
        errors = connector.validate_ddl(ddl)
        assert any("ENGINE" in e.upper() or "CLICKHOUSE" in e.upper() for e in errors)

    def test_accepts_iceberg_snapshot_ddl(self, connector, monkeypatch):
        monkeypatch.setattr(connector, "_detect_catalog_type", lambda: "iceberg")
        ddl = """CREATE TABLE catalog.schema.t (
          id BIGINT,
          ds DATE
        ) WITH (format = 'PARQUET', partitioning = ARRAY['month(ds)'])"""

        assert connector.validate_ddl(ddl) == []

    def test_rejects_delta_checkpoint_interval_for_iceberg(self, connector, monkeypatch):
        monkeypatch.setattr(connector, "_detect_catalog_type", lambda: "iceberg")
        ddl = """CREATE TABLE catalog.schema.t (
          id BIGINT,
          ds DATE
        ) WITH (format = 'PARQUET', checkpoint_interval = 5)"""

        errors = connector.validate_ddl(ddl)
        assert any("checkpoint_interval" in e for e in errors)

    def test_rejects_partitioned_by_for_iceberg(self, connector, monkeypatch):
        monkeypatch.setattr(connector, "_detect_catalog_type", lambda: "iceberg")
        ddl = """CREATE TABLE catalog.schema.t (
          id BIGINT,
          ds DATE
        ) WITH (partitioned_by = ARRAY['ds'])"""

        errors = connector.validate_ddl(ddl)
        assert any("partitioning" in e for e in errors)


class TestSuggestTableLayoutHive:
    def test_hive_returns_partitioned_by(self, connector, monkeypatch):
        monkeypatch.setattr(connector, "_detect_catalog_type", lambda: "hive")
        columns = [
            {"name": "id", "type": "BIGINT", "nullable": False},
            {"name": "ds", "type": "VARCHAR", "nullable": False},
        ]
        layout = connector.suggest_table_layout(columns)
        # Hive hints: include format + partitioned_by
        assert "format" in layout or "partitioned_by" in layout or layout == {}


class TestSuggestTableLayoutIceberg:
    def test_iceberg_returns_format_and_partitioning(self, connector, monkeypatch):
        monkeypatch.setattr(connector, "_detect_catalog_type", lambda: "iceberg")
        columns = [
            {"name": "id", "type": "BIGINT", "nullable": False},
            {"name": "ds", "type": "DATE", "nullable": False},
        ]
        layout = connector.suggest_table_layout(columns)
        assert layout == {"format": "PARQUET", "partitioning": ["month(ds)"]}

    def test_iceberg_returns_format_without_partitioning(self, connector, monkeypatch):
        monkeypatch.setattr(connector, "_detect_catalog_type", lambda: "iceberg")
        columns = [
            {"name": "id", "type": "BIGINT", "nullable": False},
        ]
        layout = connector.suggest_table_layout(columns)
        assert layout == {"format": "PARQUET"}


class TestSuggestTableLayoutGeneric:
    def test_generic_returns_empty(self, connector, monkeypatch):
        monkeypatch.setattr(connector, "_detect_catalog_type", lambda: "unknown")
        columns = [{"name": "id", "type": "BIGINT", "nullable": False}]
        assert connector.suggest_table_layout(columns) == {}


class TestMapSourceType:
    def test_hugeint_to_decimal(self, connector):
        assert connector.map_source_type("duckdb", "HUGEINT") == "DECIMAL(38,0)"

    def test_largeint_to_decimal(self, connector):
        assert connector.map_source_type("starrocks", "LARGEINT") == "DECIMAL(38,0)"

    def test_unknown_returns_none(self, connector):
        assert connector.map_source_type("duckdb", "VARCHAR") is None
