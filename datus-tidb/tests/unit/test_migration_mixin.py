# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest

from datus_tidb import TiDBConfig, TiDBConnector


@pytest.fixture
def connector() -> TiDBConnector:
    return TiDBConnector(TiDBConfig(username="test_user", database="analytics"))


def test_capabilities_describe_a_distributed_oltp_target(connector):
    capabilities = connector.describe_migration_capabilities()

    assert capabilities["supported"] is True
    assert capabilities["dialect_family"] == "mysql-like"
    # No distribution or bucketing clause exists in TiDB.
    assert capabilities["requires"] == []
    forbids = " ".join(capabilities["forbids"]).upper()
    assert "DISTRIBUTED BY" in forbids
    assert "CHECK" in forbids
    assert "FULLTEXT" in forbids
    assert "AUTO_RANDOM" in capabilities["example_ddl"]


def test_capabilities_steer_monotonic_keys_to_auto_random(connector):
    """A monotonically increasing primary key concentrates writes on one region."""
    hints = connector.describe_migration_capabilities()["type_hints"]

    assert "AUTO_RANDOM" in hints["monotonic BIGINT primary key"]
    assert hints["HUGEINT"].startswith("DECIMAL(38,0)")


def test_no_table_layout_to_suggest(connector):
    assert connector.suggest_table_layout([{"name": "id", "type": "BIGINT", "nullable": False}]) == {}
    assert connector.suggest_table_layout([]) == {}


@pytest.mark.acceptance
def test_validate_ddl_flags_check_constraints_which_tidb_does_not_enforce(connector):
    """Accepted, never enforced (tidb_enable_check_constraint defaults to OFF) —
    violating rows insert successfully, so nothing surfaces at runtime."""
    errors = connector.validate_ddl("CREATE TABLE t (id INT, qty INT CHECK (qty > 0))")

    assert any("CHECK" in error and "tidb_enable_check_constraint" in error for error in errors)


@pytest.mark.acceptance
def test_validate_ddl_flags_fulltext_which_tidb_silently_drops(connector):
    errors = connector.validate_ddl("CREATE TABLE t (body TEXT, FULLTEXT (body))")

    assert any("FULLTEXT" in error for error in errors)


@pytest.mark.parametrize(
    ("ddl", "expected"),
    [
        ("CREATE TABLE t (id INT) DUPLICATE KEY (id) DISTRIBUTED BY HASH(id) BUCKETS 4", "DISTRIBUTED BY"),
        ("CREATE TABLE t (id INT, v INT SUM) AGGREGATE KEY (id)", "AGGREGATE KEY"),
    ],
)
def test_validate_ddl_rejects_starrocks_and_doris_table_models(connector, ddl, expected):
    assert any(expected in error for error in connector.validate_ddl(ddl))


def test_validate_ddl_accepts_idiomatic_tidb_ddl(connector):
    ddl = """
    CREATE TABLE orders (
        id BIGINT AUTO_RANDOM PRIMARY KEY,
        customer VARCHAR(255),
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """

    assert connector.validate_ddl(ddl) == []


def test_validate_ddl_ignores_keywords_inside_comments_and_literals(connector):
    """A column default of 'CHECK (x)' or a comment mentioning FULLTEXT is not a
    constraint — matching raw text would make the validator cry wolf."""
    ddl = """
    -- FULLTEXT search is handled by the application
    CREATE TABLE t (
        id BIGINT PRIMARY KEY,
        note VARCHAR(64) DEFAULT 'CHECK (qty > 0)'
    )
    """

    assert connector.validate_ddl(ddl) == []


def test_on_duplicate_key_update_is_not_a_table_model(connector):
    """`ON DUPLICATE KEY UPDATE` is valid TiDB DML sharing a keyword with the
    StarRocks table model."""
    assert connector.validate_ddl("INSERT INTO t VALUES (1) ON DUPLICATE KEY UPDATE v = 2") == []


@pytest.mark.parametrize(
    ("source_type", "expected"),
    [
        ("HUGEINT", "DECIMAL(38,0)"),
        ("hugeint", "DECIMAL(38,0)"),
        ("LARGEINT", "DECIMAL(38,0)"),
        ("DECIMAL(10,2)", None),
        ("VARCHAR(255)", None),
        ("BIGINT", None),
    ],
)
def test_map_source_type_overrides_only_absent_types(connector, source_type, expected):
    assert connector.map_source_type("starrocks", source_type) == expected
