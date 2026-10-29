# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from datus_db_core import build_reconciliation_checks
from datus_postgresql import PostgreSQLConnector

pytestmark = pytest.mark.integration


@pytest.fixture
def type_metadata_schema(connector):
    """Keep user-defined types outside the connector's configured search path."""
    schema_name = f"Type Metadata_{uuid.uuid4().hex[:8]}"
    schema_ref = connector.quote_identifier(schema_name)
    created = connector.execute_ddl(f"CREATE SCHEMA {schema_ref}")
    assert created.success, created.error
    try:
        yield schema_name, schema_ref
    finally:
        dropped = connector.execute_ddl(f"DROP SCHEMA {schema_ref} CASCADE")
        assert dropped.success, dropped.error


@pytest.mark.parametrize("relation_kind", ["table", "view"])
def test_schema_preserves_declared_column_types(connector, type_metadata_schema, relation_kind):
    """The information_schema type category loses modifiers, arrays and domain identity."""
    schema_name, schema_ref = type_metadata_schema
    table_ref = f'{schema_ref}."Typed Orders"'
    enum_ref = f'{schema_ref}."Order State"'
    domain_ref = f'{schema_ref}."Positive Amount"'
    statements = [
        f"CREATE TYPE {enum_ref} AS ENUM ('pending', 'paid')",
        f"CREATE DOMAIN {domain_ref} AS numeric(8,2) CHECK (VALUE >= 0)",
        f"CREATE TABLE {table_ref} (gone integer, "
        f'"Amount" numeric(6,2) NOT NULL, currency varchar(3), fixed_code char(4), '
        f"unlimited numeric, tags text[], rates numeric(8,3)[], "
        f"recorded_at timestamp(3) with time zone, elapsed interval day to second(2), "
        f"flags bit varying(7), state {enum_ref}, balance {domain_ref})",
        f"ALTER TABLE {table_ref} DROP COLUMN gone",
        f"COMMENT ON COLUMN {table_ref}.\"Amount\" IS 'Invoice amount'",
    ]
    for statement in statements:
        result = connector.execute_ddl(statement)
        assert result.success, result.error
    table_name = "Typed Orders"
    if relation_kind == "view":
        table_name = "Typed View"
        result = connector.execute_ddl(f'CREATE VIEW {schema_ref}."{table_name}" AS SELECT * FROM {table_ref}')
        assert result.success, result.error
        result = connector.execute_ddl(f'COMMENT ON COLUMN {schema_ref}."{table_name}"."Amount" IS \'View amount\'')
        assert result.success, result.error

    expected = {
        "Amount": "numeric(6,2)",
        "currency": "character varying(3)",
        "fixed_code": "character(4)",
        "unlimited": "numeric",
        "tags": "text[]",
        "rates": "numeric(8,3)[]",
        "recorded_at": "timestamp(3) with time zone",
        "elapsed": "interval day to second(2)",
        "flags": "bit varying(7)",
        "state": enum_ref,
        "balance": domain_ref,
    }
    columns = connector.get_schema(schema_name=schema_name, table_name=table_name)
    assert [(col["name"], col["type"]) for col in columns] == list(expected.items())
    assert columns[0]["comment"] == ("View amount" if relation_kind == "view" else "Invoice amount")
    fallback_columns = connector.get_schema(schema_name=schema_name.upper(), table_name=table_name.upper())
    assert fallback_columns == columns

    if relation_kind == "table":
        assert columns[0] == {
            "cid": 0,
            "name": "Amount",
            "type": "numeric(6,2)",
            "nullable": False,
            "default_value": None,
            "pk": False,
            "comment": "Invoice amount",
        }
        definitions = connector.get_tables_with_ddl(schema_name=schema_name, tables=[table_name])
        assert [item["table_name"] for item in definitions] == [table_name]
        ddl = definitions[0]["definition"]
        for name, column_type in expected.items():
            assert f"{connector.quote_identifier(name)} {column_type}" in ddl
        replay = connector.execute_ddl(ddl.replace('"Typed Orders"', '"Replayed Orders"', 1))
        assert replay.success, replay.error
        replay_columns = connector.get_schema(schema_name=schema_name, table_name="Replayed Orders")
        assert [(col["name"], col["type"]) for col in replay_columns] == list(expected.items())


def test_reconstructed_ddl_preserves_amount_and_currency_rules(connector, type_metadata_schema):
    """Replayed types must round amounts and reject the same out-of-range input."""
    schema_name, schema_ref = type_metadata_schema
    source = f'{schema_ref}."invoice"'
    target = f'{schema_ref}."invoice_copy"'
    created = connector.execute_ddl(f"CREATE TABLE {source} (amount numeric(6,2), currency varchar(3))")
    assert created.success, created.error
    ddl = connector.get_tables_with_ddl(schema_name=schema_name, tables=["invoice"])[0]["definition"]
    replayed = connector.execute_ddl(ddl.replace('"invoice"', '"invoice_copy"', 1))
    assert replayed.success, replayed.error

    for table in (source, target):
        with connector._conn() as conn:
            amount = conn.execute(text(f"INSERT INTO {table} VALUES (1.239, 'CNY') RETURNING amount")).scalar_one()
            assert amount == Decimal("1.24")
        for amount, currency, sqlstate in [("10000", "CNY", "22003"), ("1", "CNYY", "22001")]:
            with pytest.raises(DBAPIError) as error, connector._conn() as conn:
                conn.execute(
                    text(f"INSERT INTO {table} VALUES (:amount, :currency)"), {"amount": amount, "currency": currency}
                )
            assert error.value.orig.pgcode == sqlstate


@pytest.mark.parametrize("validated", [None, True, False], ids=["no-fk", "validated-fk", "not-valid-fk"])
def test_ddl_replay_preserves_multiline_defaults_and_unique_indexes(connector, type_metadata_schema, validated):
    """A line of a string literal must never become the boundary for inserting indexes."""
    schema_name, schema_ref = type_metadata_schema
    table_ref = f'{schema_ref}."literal_defaults"'
    default_value = "head's;\nALTER TABLE literal text;\ntail"
    literal = default_value.replace("'", "''")
    statements = [
        f"CREATE TABLE {table_ref} (id integer DEFAULT 0 PRIMARY KEY, code integer DEFAULT 0, "
        f"parent_code integer DEFAULT 0, note text DEFAULT '{literal}')",
        f'CREATE UNIQUE INDEX "literal_code_idx" ON {table_ref} (code)',
    ]
    if validated is not None:
        suffix = "" if validated else " NOT VALID"
        statements.append(
            f'ALTER TABLE {table_ref} ADD CONSTRAINT "parent_fk" '
            f"FOREIGN KEY (parent_code) REFERENCES {table_ref} (code){suffix}"
        )
    for statement in statements:
        result = connector.execute_ddl(statement)
        assert result.success, result.error

    definition = connector.get_tables_with_ddl(schema_name=schema_name, tables=["literal_defaults"])[0]["definition"]
    dropped = connector.execute_ddl(f"DROP TABLE {table_ref}")
    assert dropped.success, dropped.error
    replayed = connector.execute_ddl(definition, schema_name=schema_name)
    assert replayed.success, replayed.error

    with connector._conn() as conn:
        note = conn.execute(
            text(f"INSERT INTO {table_ref} (id, code, parent_code) VALUES (1, 1, 1) RETURNING note")
        ).scalar_one()
        assert note == default_value
        conn.commit()
    with pytest.raises(DBAPIError) as error, connector._conn() as conn:
        conn.execute(text(f"INSERT INTO {table_ref} (id, code, parent_code) VALUES (2, 1, 1)"))
    assert error.value.orig.pgcode == "23505"


@pytest.mark.parametrize("validated", [True, False])
def test_ddl_replay_preserves_cross_schema_foreign_key_target(connector, type_metadata_schema, validated):
    """An FK to a visible parent must retain its schema when replayed beside a namesake."""
    schema_name, schema_ref = type_metadata_schema
    parent_name = f"fk_parent_{uuid.uuid4().hex[:8]}"
    parent_ref = f"{connector.quote_identifier(connector.schema_name)}.{connector.quote_identifier(parent_name)}"
    shadow_ref = f"{schema_ref}.{connector.quote_identifier(parent_name)}"
    child_ref = f'{schema_ref}."child"'
    suffix = "" if validated else " NOT VALID"
    try:
        for statement in [
            f"CREATE TABLE {parent_ref} (id integer PRIMARY KEY)",
            f"CREATE TABLE {shadow_ref} (id integer PRIMARY KEY)",
            f"CREATE TABLE {child_ref} (id integer, parent_id integer)",
            f"ALTER TABLE {child_ref} ADD CONSTRAINT parent_fk FOREIGN KEY (parent_id) REFERENCES {parent_ref}(id){suffix}",
        ]:
            result = connector.execute_ddl(statement)
            assert result.success, result.error
        definition = connector.get_tables_with_ddl(schema_name=schema_name, tables=["child"])[0]["definition"]
        dropped = connector.execute_ddl(f"DROP TABLE {child_ref}")
        assert dropped.success, dropped.error
        replayed = connector.execute_ddl(definition, schema_name=schema_name)
        assert replayed.success, replayed.error

        with connector._conn() as conn:
            target = conn.execute(
                text("""SELECT n.nspname, p.relname, c.convalidated
                    FROM pg_catalog.pg_constraint c
                    JOIN pg_catalog.pg_class p ON p.oid = c.confrelid
                    JOIN pg_catalog.pg_namespace n ON n.oid = p.relnamespace
                    WHERE c.conrelid = CAST(:child AS regclass) AND c.contype = 'f'"""),
                {"child": child_ref},
            ).one()
        assert tuple(target) == (connector.schema_name, parent_name, validated)
    finally:
        dropped = connector.execute_ddl(f"DROP TABLE IF EXISTS {parent_ref} CASCADE")
        assert dropped.success, dropped.error


@pytest.mark.parametrize("type_kind", ["enum", "domain"])
@pytest.mark.parametrize("array", [False, True], ids=["scalar", "array"])
def test_ddl_replay_preserves_visible_user_defined_type_identity(connector, type_metadata_schema, type_kind, array):
    """Visible enum/domain references must not bind to another schema's same-named type."""
    schema_name, schema_ref = type_metadata_schema
    type_name = f"Review Value_{uuid.uuid4().hex[:8]}"
    type_ref = f"{connector.quote_identifier(connector.schema_name)}.{connector.quote_identifier(type_name)}"
    shadow_ref = f"{schema_ref}.{connector.quote_identifier(type_name)}"
    source_ref = f'{schema_ref}."typed_source"'
    copy_ref = f'{schema_ref}."typed_copy"'
    if type_kind == "enum":
        statements = [
            f"CREATE TYPE {type_ref} AS ENUM ('pending', 'paid')",
            f"CREATE TYPE {shadow_ref} AS ENUM ('wrong')",
        ]
    else:
        statements = [
            f"CREATE DOMAIN {type_ref} AS numeric(8,2) CHECK (VALUE >= 0)",
            f"CREATE DOMAIN {shadow_ref} AS numeric(8,2)",
        ]
    statements.append(f"CREATE TABLE {source_ref} (value {type_ref}{'[]' if array else ''})")
    try:
        for statement in statements:
            result = connector.execute_ddl(statement)
            assert result.success, result.error
        definition = connector.get_tables_with_ddl(schema_name=schema_name, tables=["typed_source"])[0]["definition"]
        replayed = connector.execute_ddl(
            definition.replace('"typed_source"', '"typed_copy"', 1), schema_name=schema_name
        )
        assert replayed.success, replayed.error
        with connector._conn() as conn:
            type_oids = [
                conn.execute(
                    text("""SELECT a.atttypid FROM pg_catalog.pg_attribute a
                        WHERE a.attrelid = CAST(:relation AS regclass) AND a.attname = 'value'"""),
                    {"relation": relation},
                ).scalar_one()
                for relation in (source_ref, copy_ref)
            ]
            assert type_oids[0] == type_oids[1]
            # A metadata read must not change the context used by later operations.
            visible_oid = conn.execute(
                text("SELECT CAST(:type AS regtype)::oid"), {"type": connector.quote_identifier(type_name)}
            ).scalar_one()
            expected_oid = conn.execute(text("SELECT CAST(:type AS regtype)::oid"), {"type": type_ref}).scalar_one()
            assert visible_oid == expected_oid
    finally:
        drop_kind = "TYPE" if type_kind == "enum" else "DOMAIN"
        dropped = connector.execute_ddl(f"DROP {drop_kind} IF EXISTS {type_ref} CASCADE")
        assert dropped.success, dropped.error


def test_declared_array_types_produce_executable_reconciliation_checks(connector, type_metadata_schema):
    """Array types retain their SQL identity without entering scalar SUM/AVG checks."""
    schema_name, schema_ref = type_metadata_schema
    table_ref = f'{schema_ref}."array_checks"'
    created = connector.execute_ddl(
        f"CREATE TABLE {table_ref} (id integer, amount numeric(6,2), amounts numeric(8,3)[], days date[])"
    )
    assert created.success, created.error
    inserted = connector.execute_insert(
        f"INSERT INTO {table_ref} VALUES (1, 1.24, ARRAY[1.123, 4.567], ARRAY[DATE '2024-01-01'])"
    )
    assert inserted.success, inserted.error
    columns = connector.get_schema(schema_name=schema_name, table_name="array_checks")
    assert [column["type"] for column in columns] == ["integer", "numeric(6,2)", "numeric(8,3)[]", "date[]"]
    checks = build_reconciliation_checks(table_ref, table_ref, columns, key_columns=["id"])
    with connector._conn() as conn:
        results = {check["name"]: conn.execute(text(check["source_query"])).fetchall() for check in checks}
    assert results["numeric_aggregate"] == [(1, Decimal("1"), Decimal("1.24"), Decimal("1.24"))]
    assert results["duplicate_key"] == []


def test_ddl_preserves_composite_keys_and_standalone_unique_indexes(config):
    """Key order comes from constraints; index predicates and expressions must survive."""
    connector = PostgreSQLConnector(config)
    table_name = f"ddl_keys_{uuid.uuid4().hex[:8]}"
    table_ref = f'{connector.quote_identifier(config.schema_name)}."{table_name}"'
    index_prefix = f"{table_name}_index"
    statements = [
        f'CREATE TABLE {table_ref} (id INT, "Tenant ID" INT, email TEXT, active BOOLEAN, '
        'PRIMARY KEY ("Tenant ID", id), CONSTRAINT "Email Key" UNIQUE (email, "Tenant ID"))',
        f"CREATE UNIQUE INDEX {index_prefix}_plain ON {table_ref} (email, id)",
        f"CREATE UNIQUE INDEX {index_prefix}_partial ON {table_ref} (email) WHERE active",
        f"CREATE UNIQUE INDEX {index_prefix}_expression ON {table_ref} (lower(email))",
        f"CREATE INDEX {index_prefix}_ordinary ON {table_ref} (active)",
    ]
    try:
        for statement in statements:
            result = connector.execute_ddl(statement)
            assert result.success, result.error

        tables = connector.get_tables_with_ddl(schema_name=config.schema_name, tables=[table_name])

        assert [table["table_name"] for table in tables] == [table_name]
        definition = tables[0]["definition"]
        assert f'CONSTRAINT "{table_name}_pkey" PRIMARY KEY ("Tenant ID", id)' in definition
        assert 'CONSTRAINT "Email Key" UNIQUE (email, "Tenant ID")' in definition
        assert definition.count("CREATE UNIQUE INDEX") == 3
        assert f"{index_prefix}_plain" in definition and "USING btree (email, id);" in definition
        assert f"{index_prefix}_partial" in definition and "USING btree (email) WHERE active;" in definition
        assert f"{index_prefix}_expression" in definition and "USING btree (lower(email));" in definition
        assert f"{index_prefix}_ordinary" not in definition
    finally:
        try:
            dropped = connector.execute_ddl(f"DROP TABLE IF EXISTS {table_ref}")
            assert dropped.success, dropped.error
        finally:
            connector.close()


@pytest.mark.parametrize("validated", [True, False])
def test_ddl_round_trip_self_referencing_foreign_key_with_standalone_unique_index(config, validated):
    """Test that self-referencing foreign keys referencing standalone unique indexes round-trip successfully.

    Validated foreign keys must be deferred out of CREATE TABLE to run after the
    standalone unique index is created. NOT VALID foreign keys must also run after
    the unique index. Replaying the reconstructed DDL on a clean table must succeed.
    """
    connector = PostgreSQLConnector(config)
    table_name = f"ddl_self_fk_{uuid.uuid4().hex[:8]}"
    replay_table_name = f"{table_name}_replay"
    table_ref = f'{connector.quote_identifier(config.schema_name)}."{table_name}"'
    replay_ref = f'{connector.quote_identifier(config.schema_name)}."{replay_table_name}"'
    index_name = f"{table_name}_code_idx"
    fk_name = f"{table_name}_parent_fk"

    fk_suffix = "" if validated else " NOT VALID"
    statements = [
        f"CREATE TABLE {table_ref} (id INT PRIMARY KEY, code INT, parent_code INT)",
        f'CREATE UNIQUE INDEX "{index_name}" ON {table_ref} (code)',
        f'ALTER TABLE {table_ref} ADD CONSTRAINT "{fk_name}" FOREIGN KEY (parent_code) REFERENCES {table_ref} (code){fk_suffix}',
    ]
    try:
        for statement in statements:
            result = connector.execute_ddl(statement)
            assert result.success, result.error

        tables = connector.get_tables_with_ddl(schema_name=config.schema_name, tables=[table_name])
        assert [table["table_name"] for table in tables] == [table_name]
        definition = tables[0]["definition"]

        # Verify dependency ordering in generated DDL:
        # CREATE TABLE must not contain the foreign key referencing the standalone index
        assert f'CONSTRAINT "{fk_name}"' not in definition.split("CREATE UNIQUE INDEX")[0]
        # CREATE UNIQUE INDEX must precede ALTER TABLE ADD CONSTRAINT
        assert definition.find("CREATE UNIQUE INDEX") < definition.find(f'ADD CONSTRAINT "{fk_name}"')

        # Live round-trip: Replay reconstructed DDL under replay table name.
        # Drop the source table first to guarantee replay does not depend on the existing table.
        dropped = connector.execute_ddl(f"DROP TABLE IF EXISTS {table_ref} CASCADE")
        assert dropped.success, dropped.error

        replay_ddl = definition.replace(table_name, replay_table_name)

        for stmt in [s.strip() for s in replay_ddl.split(";") if s.strip()]:
            res = connector.execute_ddl(stmt)
            assert res.success, f"Replaying statement failed: {stmt}\nError: {res.error}\nFull DDL:\n{definition}"

    finally:
        try:
            connector.execute_ddl(f"DROP TABLE IF EXISTS {table_ref} CASCADE")
            connector.execute_ddl(f"DROP TABLE IF EXISTS {replay_ref} CASCADE")
        finally:
            connector.close()
