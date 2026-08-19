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
    # Doris derives both the key model and the distribution clause when they are
    # omitted, so neither is a hard requirement.
    assert result["requires"] == []
    assert "One of DUPLICATE KEY / UNIQUE KEY / AGGREGATE KEY" in result["recommends"]
    assert "key model" in result["defaults_when_omitted"]
    assert "distribution" in result["defaults_when_omitted"]
    assert result["type_hints"]["unbounded VARCHAR"].startswith("VARCHAR(65533)")
    assert "DUPLICATE KEY" in result["example_ddl"]
    assert "DISTRIBUTED BY HASH" in result["example_ddl"]


def test_describe_migration_capabilities_forbids_unsupported_clauses(connector):
    forbids = connector.describe_migration_capabilities()["forbids"]

    assert any("PRIMARY KEY" in item for item in forbids)
    assert any("FOREIGN KEY" in item for item in forbids)
    assert any("FULLTEXT" in item for item in forbids)
    assert any("CHECK" in item for item in forbids)


@pytest.mark.parametrize(
    "ddl",
    [
        # Doris derives DUPLICATE KEY over a short-key prefix.
        "CREATE TABLE db.t (id BIGINT NOT NULL) DISTRIBUTED BY HASH(id) BUCKETS 10",
        # Doris defaults to random distribution with 10 buckets.
        "CREATE TABLE db.t (id BIGINT NOT NULL) DUPLICATE KEY(id)",
        # Both derived at once.
        "CREATE TABLE db.t (id BIGINT NOT NULL)",
    ],
)
def test_validate_ddl_accepts_omitted_key_and_distribution(connector, ddl):
    assert connector.validate_ddl(ddl) == []


@pytest.mark.parametrize(
    ("ddl", "expected_error"),
    [
        (
            "CREATE TABLE db.t (id BIGINT NOT NULL, PRIMARY KEY (id)) DISTRIBUTED BY HASH(id) BUCKETS 10",
            "PRIMARY KEY is not a Doris table model",
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
        (
            "CREATE TABLE db.t (id BIGINT NOT NULL) UNIQUE KEY(id) DISTRIBUTED BY RANDOM BUCKETS 10",
            "DISTRIBUTED BY RANDOM",
        ),
        (
            "CREATE TABLE db.t (\n  started_at TIME,\n  id BIGINT NOT NULL\n) DUPLICATE KEY(id)",
            "TIME columns",
        ),
    ],
)
def test_validate_ddl_rejects_unsupported_clauses(connector, ddl, expected_error):
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


@pytest.mark.parametrize(
    "key_clause",
    ["DUPLICATE KEY(id)", "UNIQUE KEY(id)"],
)
def test_validate_ddl_accepts_auto_increment_on_detail_and_upsert_models(connector, key_clause):
    ddl = f"CREATE TABLE db.t (id BIGINT NOT NULL AUTO_INCREMENT) {key_clause} DISTRIBUTED BY HASH(id) BUCKETS 10"
    assert connector.validate_ddl(ddl) == []


@pytest.mark.parametrize(
    ("ddl", "expected_error"),
    [
        (
            "CREATE TABLE db.t (id BIGINT NOT NULL AUTO_INCREMENT, v INT SUM) "
            "AGGREGATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 10",
            "only supported in DUPLICATE KEY and UNIQUE KEY",
        ),
        (
            "CREATE TABLE db.t (id INT NOT NULL AUTO_INCREMENT) UNIQUE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 10",
            "must be BIGINT",
        ),
        (
            "CREATE TABLE db.t (id BIGINT NULL AUTO_INCREMENT) UNIQUE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 10",
            "must be NOT NULL",
        ),
        (
            "CREATE TABLE db.t (id BIGINT NOT NULL DEFAULT 1 AUTO_INCREMENT) "
            "UNIQUE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 10",
            "cannot have a DEFAULT value",
        ),
        (
            "CREATE TABLE db.t (id BIGINT NOT NULL AUTO_INCREMENT, other BIGINT NOT NULL AUTO_INCREMENT) "
            "UNIQUE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 10",
            "at most one AUTO_INCREMENT column",
        ),
    ],
)
def test_validate_ddl_enforces_auto_increment_rules(connector, ddl, expected_error):
    assert any(expected_error in error for error in connector.validate_ddl(ddl))


@pytest.mark.parametrize(
    ("column_type", "column_name"),
    [
        ("DOUBLE", "score"),
        ("FLOAT", "ratio"),
        ("STRING", "payload"),
        ("JSON", "doc"),
        ("VARIANT", "raw"),
    ],
)
def test_validate_ddl_rejects_forbidden_key_column_types(connector, column_type, column_name):
    ddl = (
        f"CREATE TABLE db.t ({column_name} {column_type} NOT NULL, v BIGINT) "
        f"DUPLICATE KEY({column_name}) DISTRIBUTED BY HASH({column_name}) BUCKETS 10"
    )

    assert any("as a key column" in error for error in connector.validate_ddl(ddl))


def test_validate_ddl_allows_forbidden_key_types_as_value_columns(connector):
    ddl = (
        "CREATE TABLE db.t (id BIGINT NOT NULL, score DOUBLE, payload STRING) "
        "DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 10"
    )

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
            note VARCHAR(255) DEFAULT 'FULLTEXT CHECK(id) FOREIGN KEY ON DUPLICATE KEY'
        )
        DUPLICATE KEY(id)
        DISTRIBUTED BY HASH(id) BUCKETS 10
        """,
    ],
)
def test_validate_ddl_ignores_keywords_inside_quoted_regions(connector, ddl):
    assert connector.validate_ddl(ddl) == []


def test_validate_ddl_does_not_read_clauses_out_of_string_literals(connector):
    """A PRIMARY KEY mention inside a literal must not count as a key clause."""
    ddl = """
    CREATE TABLE db.t (
        id BIGINT NOT NULL,
        note VARCHAR(255) DEFAULT 'DUPLICATE KEY(id) DISTRIBUTED BY HASH(id)'
    )
    PRIMARY KEY(id)
    """

    assert any("PRIMARY KEY is not a Doris table model" in error for error in connector.validate_ddl(ddl))


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
    """Three matches Doris' own short-key ceiling (shortkey_max_column_count)."""
    columns = [{"name": f"col{i}_id", "type": "BIGINT", "nullable": False} for i in range(5)]
    assert len(connector.suggest_table_layout(columns)["duplicate_key"]) == 3


def test_suggest_table_layout_skips_types_doris_rejects_as_keys(connector):
    columns = [
        {"name": "score", "type": "DOUBLE", "nullable": False},
        {"name": "payload", "type": "STRING", "nullable": False},
        {"name": "id", "type": "BIGINT", "nullable": False},
    ]

    assert connector.suggest_table_layout(columns)["duplicate_key"] == ["id"]


def test_suggest_table_layout_returns_no_keys_when_every_column_is_ineligible(connector):
    columns = [
        {"name": "score", "type": "DOUBLE", "nullable": False},
        {"name": "payload", "type": "STRING", "nullable": False},
    ]

    assert connector.suggest_table_layout(columns)["duplicate_key"] == []


@pytest.mark.parametrize(
    ("source_type", "expected"),
    [
        ("HUGEINT", "LARGEINT"),
        ("TIMESTAMP(6)", "DATETIME"),
        ("TIMESTAMP WITH TIME ZONE", "DATETIME"),
        ("TEXT", "STRING"),
        ("TIME", "VARCHAR(20)"),
        ("UUID", "VARCHAR(36)"),
        ("BYTEA", "VARBINARY"),
        ("INET", "IPV4"),
        ("JSONB", "JSON"),
        ("DECIMAL(18,2)", None),
    ],
)
def test_map_source_type(connector, source_type, expected):
    assert connector.map_source_type("postgres", source_type) == expected
