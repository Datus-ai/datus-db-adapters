# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

import uuid

import pytest

from datus_postgresql import PostgreSQLConnector

pytestmark = pytest.mark.integration


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
