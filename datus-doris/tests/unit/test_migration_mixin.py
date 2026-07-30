# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest

from datus_doris import DorisConnector


@pytest.fixture
def connector():
    return DorisConnector.__new__(DorisConnector)


def test_describe_migration_capabilities(connector):
    result = connector.describe_migration_capabilities()

    assert result["supported"] is True
    assert result["dialect_family"] == "mysql-like"
    assert result["requires"] == [
        "One of DUPLICATE KEY / UNIQUE KEY / AGGREGATE KEY",
        "DISTRIBUTED BY HASH(cols) BUCKETS N",
    ]
    assert result["forbids"] == ["FOREIGN KEY", "FULLTEXT INDEX", "CHECK"]
    assert result["type_hints"]["unbounded VARCHAR"] == "VARCHAR(65533)"
    assert "DUPLICATE KEY" in result["example_ddl"]
    assert "DISTRIBUTED BY HASH" in result["example_ddl"]


@pytest.mark.parametrize(
    ("ddl", "expected_error"),
    [
        (
            "CREATE TABLE db.t (id BIGINT) DISTRIBUTED BY HASH(id) BUCKETS 10",
            "must define one of",
        ),
        (
            "CREATE TABLE db.t (id BIGINT) DUPLICATE KEY(id)",
            "DISTRIBUTED BY",
        ),
        (
            "CREATE TABLE db.t (id BIGINT AUTO_INCREMENT) DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 10",
            "AUTO_INCREMENT",
        ),
        (
            "CREATE TABLE db.t (id BIGINT, FOREIGN KEY (id) REFERENCES p(id)) "
            "DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 10",
            "FOREIGN KEY",
        ),
        (
            "CREATE TABLE db.t (id BIGINT, FULLTEXT INDEX idx (id)) "
            "DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 10",
            "FULLTEXT",
        ),
        (
            "CREATE TABLE db.t (id BIGINT, CHECK (id > 0)) DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 10",
            "CHECK",
        ),
    ],
)
def test_validate_ddl_rejects_invalid_layouts(connector, ddl, expected_error):
    assert any(expected_error in error for error in connector.validate_ddl(ddl))


@pytest.mark.parametrize(
    "key_clause",
    [
        "DUPLICATE KEY(id)",
        "UNIQUE KEY(id)",
        "AGGREGATE KEY(id)",
    ],
)
def test_validate_ddl_accepts_supported_key_models(connector, key_clause):
    ddl = f"CREATE TABLE db.t (id BIGINT NOT NULL) {key_clause} DISTRIBUTED BY HASH(id) BUCKETS 10"
    assert connector.validate_ddl(ddl) == []


def test_validate_ddl_accepts_auto_increment_for_unique_key(connector):
    ddl = "CREATE TABLE db.t (id BIGINT AUTO_INCREMENT) UNIQUE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 10"
    assert connector.validate_ddl(ddl) == []


def test_validate_ddl_ignores_keywords_inside_identifiers_and_comments(connector):
    ddl = """
    CREATE TABLE db.t (
        checksum_value BIGINT,
        check_flag BOOLEAN
    )
    DUPLICATE KEY(checksum_value)
    DISTRIBUTED BY HASH(checksum_value) BUCKETS 10
    -- FULLTEXT and CHECK(id > 0) are not active clauses
    """
    assert connector.validate_ddl(ddl) == []


@pytest.mark.parametrize(
    "ddl",
    [
        """
        CREATE TABLE db.t (`FULLTEXT` VARCHAR(20), id BIGINT)
        DUPLICATE KEY(id)
        DISTRIBUTED BY HASH(id) BUCKETS 10
        """,
        """
        CREATE TABLE db.t ("FULLTEXT" VARCHAR(20), id BIGINT)
        DUPLICATE KEY(id)
        DISTRIBUTED BY HASH(id) BUCKETS 10
        """,
        """
        CREATE TABLE db.t (
            id BIGINT,
            note VARCHAR(255) DEFAULT 'FULLTEXT CHECK(id) FOREIGN KEY AUTO_INCREMENT ON DUPLICATE KEY'
        )
        DUPLICATE KEY(id)
        DISTRIBUTED BY HASH(id) BUCKETS 10
        """,
    ],
)
def test_validate_ddl_ignores_keywords_inside_quoted_regions(connector, ddl):
    assert connector.validate_ddl(ddl) == []


def test_validate_ddl_does_not_accept_required_clauses_inside_string_literals(connector):
    ddl = """
    CREATE TABLE db.t (
        note VARCHAR(255) DEFAULT 'DUPLICATE KEY(id) DISTRIBUTED BY HASH(id)'
    )
    """

    errors = connector.validate_ddl(ddl)

    assert any("must define one of" in error for error in errors)
    assert any("DISTRIBUTED BY" in error for error in errors)


@pytest.mark.parametrize(
    ("columns", "expected_keys"),
    [
        ([], []),
        ([{"name": "name", "type": "VARCHAR", "nullable": True}], ["name"]),
        (
            [
                {"name": "name", "type": "VARCHAR", "nullable": True},
                {"name": "id", "type": "BIGINT", "nullable": False},
            ],
            ["id"],
        ),
        (
            [
                {"name": "a_id", "type": "BIGINT", "nullable": True},
                {"name": "b_id", "type": "BIGINT", "nullable": False},
            ],
            ["b_id", "a_id"],
        ),
    ],
)
def test_suggest_table_layout(connector, columns, expected_keys):
    assert connector.suggest_table_layout(columns) == {
        "duplicate_key": expected_keys,
        "distributed_by": expected_keys,
        "buckets": 10,
    }


def test_suggest_table_layout_limits_keys_to_three(connector):
    columns = [{"name": f"col{i}_id", "type": "BIGINT", "nullable": False} for i in range(5)]
    assert len(connector.suggest_table_layout(columns)["duplicate_key"]) == 3


@pytest.mark.parametrize(
    ("source_type", "expected"),
    [
        ("HUGEINT", "LARGEINT"),
        ("TIMESTAMP(6)", "DATETIME"),
        ("TIMESTAMP WITH TIME ZONE", "DATETIME"),
        ("TEXT", "STRING"),
        ("TIME", "VARCHAR(20)"),
        ("UUID", "VARCHAR(36)"),
        ("DECIMAL(18,2)", None),
    ],
)
def test_map_source_type(connector, source_type, expected):
    assert connector.map_source_type("postgres", source_type) == expected
