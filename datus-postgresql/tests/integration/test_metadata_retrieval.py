# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

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
