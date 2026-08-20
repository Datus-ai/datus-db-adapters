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
        "DISTRIBUTED BY HASH(cols) BUCKETS N (or BUCKETS AUTO)",
    ]
    # Documented so a reader knows what the engine would have derived instead.
    assert "key model" in result["defaults_when_omitted"]
    assert "distribution" in result["defaults_when_omitted"]
    assert result["type_hints"]["unbounded VARCHAR"].startswith("VARCHAR(65533)")
    assert "DUPLICATE KEY" in result["example_ddl"]
    assert "DISTRIBUTED BY HASH" in result["example_ddl"]


def test_requires_matches_what_validate_ddl_enforces(connector):
    """``requires`` is the contract ``validate_ddl`` implements; keep them in sync."""
    requires = connector.describe_migration_capabilities()["requires"]
    errors = connector.validate_ddl("CREATE TABLE db.t (id BIGINT NOT NULL)")

    assert len(errors) == len(requires)
    assert any("DUPLICATE KEY / UNIQUE KEY / AGGREGATE KEY" in error for error in errors)
    assert any("DISTRIBUTED BY" in error for error in errors)


def test_no_migration_hint_ever_targets_varbinary(connector):
    """``VARBINARY`` is reachable only through an external catalog.

    Doris 4.0 added the type, but ``CREATE TABLE`` rejects it — it exists so a
    catalog can expose a binary column from another engine. A migration hint
    naming it would produce DDL no Doris version accepts, so neither the
    capability report nor ``map_source_type`` may hand it back as a target.
    """
    hints = connector.describe_migration_capabilities()["type_hints"]

    assert hints["BYTEA / BLOB"].startswith("STRING")
    for source in ("BYTEA", "BLOB", "BINARY", "VARBINARY", "RAW"):
        assert connector.map_source_type("postgres", source) != "VARBINARY"


def test_no_migration_hint_targets_an_ip_type_that_cannot_hold_the_source_value(connector):
    """``IPV4``/``IPV6`` hold a bare address, and reject the rest by writing NULL.

    PostgreSQL ``cidr`` always carries a netmask and ``inet`` may, and either
    can be v4 or v6. Doris stores an out-of-range or malformed value as NULL
    rather than failing the load, so a hint naming an IP type would drop rows
    silently. Asserted as a property so any future edit reintroducing one fails
    whatever spelling it arrives under.
    """
    hints = connector.describe_migration_capabilities()["type_hints"]

    for source in ("INET", "CIDR"):
        assert connector.map_source_type("postgres", source) not in ("IPV4", "IPV6")
    # The two hint surfaces must agree: the capability report is what an agent
    # reads when it does not call map_source_type.
    assert hints["INET / CIDR"].startswith(connector.map_source_type("postgres", "INET"))


def test_describe_migration_capabilities_forbids_unsupported_clauses(connector):
    forbids = connector.describe_migration_capabilities()["forbids"]

    assert any("PRIMARY KEY" in item for item in forbids)
    assert any("FOREIGN KEY" in item for item in forbids)
    assert any("FULLTEXT" in item for item in forbids)
    assert any("CHECK" in item for item in forbids)


@pytest.mark.parametrize(
    ("ddl", "expected_error"),
    [
        # Doris would derive DUPLICATE KEY over a short-key prefix; a migration
        # must not inherit a key model nobody chose.
        (
            "CREATE TABLE db.t (id BIGINT NOT NULL) DISTRIBUTED BY HASH(id) BUCKETS 10",
            "must define one of",
        ),
        # Doris would default to random distribution with 10 buckets.
        (
            "CREATE TABLE db.t (id BIGINT NOT NULL) DUPLICATE KEY(id)",
            "must include a DISTRIBUTED BY clause",
        ),
    ],
)
def test_validate_ddl_requires_explicit_layout(connector, ddl, expected_error):
    assert any(expected_error in error for error in connector.validate_ddl(ddl))


def test_validate_ddl_explains_what_doris_would_have_derived(connector):
    """The message must not read as a parse error — Doris accepts this DDL."""
    errors = connector.validate_ddl("CREATE TABLE db.t (id BIGINT NOT NULL)")

    assert any("Doris would otherwise derive a key model" in error for error in errors)
    assert any("random distribution with 10 buckets" in error for error in errors)


@pytest.mark.parametrize("bucket_clause", ["BUCKETS 10", "BUCKETS AUTO"])
def test_validate_ddl_accepts_any_explicit_bucket_form(connector, bucket_clause):
    ddl = f"CREATE TABLE db.t (id BIGINT NOT NULL) DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) {bucket_clause}"
    assert connector.validate_ddl(ddl) == []


def test_validate_ddl_rejects_an_implicit_bucket_count(connector):
    """BUCKETS is optional to Doris and defaults to 10, so the policy covers it too.

    ``requires`` advertises ``BUCKETS N (or BUCKETS AUTO)``; a DISTRIBUTED BY
    clause that stops short of one leaves the bucket layout to
    ``FeConstants.default_bucket_num``, which is the same silent default the
    key-clause and distribution-clause rules exist to prevent.
    """
    ddl = "CREATE TABLE db.t (id BIGINT NOT NULL) DUPLICATE KEY(id) DISTRIBUTED BY HASH(id)"

    errors = connector.validate_ddl(ddl)

    assert any("must state a bucket count" in error for error in errors)
    assert any("default to 10 buckets" in error for error in errors)


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
            "CREATE TABLE db.t (started_at TIME, id BIGINT NOT NULL) "
            "DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 10",
            "TIME columns",
        ),
        (
            "CREATE TABLE db.t (`started_at` TIME, id BIGINT NOT NULL) "
            "DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 10",
            "TIME columns",
        ),
        (
            'CREATE TABLE db.t ("started_at" TIME, id BIGINT NOT NULL) '
            "DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 10",
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
        # No nullability keyword at all: ColumnNullableType.DEFAULT makes the
        # column nullable, so Doris rejects this exactly as it rejects NULL.
        (
            "CREATE TABLE db.t (id BIGINT AUTO_INCREMENT) UNIQUE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 10",
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


@pytest.mark.parametrize(
    "ddl",
    [
        # A column named "time". The quoted-name check runs against the
        # unmasked statement for exactly this reason: a rule that inferred the
        # blanked-out identifier from the surrounding whitespace would flag the
        # indentation here instead.
        """
        CREATE TABLE db.t (
            id BIGINT NOT NULL,
            time INT
        )
        DUPLICATE KEY(id)
        DISTRIBUTED BY HASH(id) BUCKETS 10
        """,
        # A quoted column named "time" carrying another type: the quoted-name
        # rule must read the type that follows, not the name.
        """
        CREATE TABLE db.t (id BIGINT NOT NULL, `time` INT)
        DUPLICATE KEY(id)
        DISTRIBUTED BY HASH(id) BUCKETS 10
        """,
        # A property key mentioning time must not be read as a column.
        """
        CREATE TABLE db.t (id BIGINT NOT NULL, dt DATE)
        DUPLICATE KEY(id)
        DISTRIBUTED BY HASH(id) BUCKETS 10
        PROPERTIES ("dynamic_partition.time_unit" = "DAY")
        """,
        # TIME as a result type is fine; only storing one is rejected.
        """
        CREATE TABLE db.t (id BIGINT NOT NULL, dur INT)
        DUPLICATE KEY(id)
        DISTRIBUTED BY HASH(id) BUCKETS 10
        COMMENT 'CAST(x AS TIME) is allowed'
        """,
        # DATETIME must not trip \bTIME\b.
        """
        CREATE TABLE db.t (id BIGINT NOT NULL, ts DATETIME)
        DUPLICATE KEY(id)
        DISTRIBUTED BY HASH(id) BUCKETS 10
        """,
    ],
)
def test_validate_ddl_does_not_mistake_other_constructs_for_a_time_column(connector, ddl):
    assert connector.validate_ddl(ddl) == []


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


def test_validate_ddl_does_not_read_required_clauses_out_of_string_literals(connector):
    """Clauses that only appear inside a literal must not satisfy the requirements."""
    ddl = """
    CREATE TABLE db.t (
        id BIGINT NOT NULL,
        note VARCHAR(255) DEFAULT 'DUPLICATE KEY(id) DISTRIBUTED BY HASH(id)'
    )
    PRIMARY KEY(id)
    """

    errors = connector.validate_ddl(ddl)

    assert any("PRIMARY KEY is not a Doris table model" in error for error in errors)
    assert any("must include a DISTRIBUTED BY clause" in error for error in errors)


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
        ("BYTEA", "STRING"),
        ("BLOB", "STRING"),
        ("INET", "VARCHAR(43)"),
        ("CIDR", "VARCHAR(43)"),
        ("JSONB", "JSON"),
        ("DECIMAL(18,2)", None),
    ],
)
def test_map_source_type(connector, source_type, expected):
    assert connector.map_source_type("postgres", source_type) == expected
