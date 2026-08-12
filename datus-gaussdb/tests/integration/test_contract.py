# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import uuid

import pytest

from datus_db_core.testing import contract
from datus_gaussdb import GaussDBConfig, GaussDBConnector


@pytest.mark.integration
@pytest.mark.acceptance
def test_deep_adapter_contract(connector: GaussDBConnector, config: GaussDBConfig):
    """Cover schema-qualified SELECT, quoted identifiers, typed values, and LIMIT."""
    suffix = uuid.uuid4().hex[:8]
    table_name = f"contract_{suffix}"
    q = connector.quote_identifier
    schema = config.schema_name or "public"
    table_ref = f"{q(schema)}.{q(table_name)}"

    case = contract.TableContractCase(
        adapter_name="gaussdb",
        table_name=table_name,
        drop_sql=f"DROP TABLE IF EXISTS {table_ref}",
        create_sql=f"""
            CREATE TABLE {table_ref} (
                {q("id")} INTEGER,
                {q("Mixed Case")} VARCHAR(64),
                {q("special-name")} VARCHAR(64),
                {q("nullable_text")} VARCHAR(64),
                {q("event_date")} DATE,
                {q("event_ts")} TIMESTAMP,
                {q("amount")} DECIMAL(10, 2),
                {q("bool_flag")} BOOLEAN
            )
        """,
        insert_sqls=[
            f"""
            INSERT INTO {table_ref}
                (
                    {q("id")},
                    {q("Mixed Case")},
                    {q("special-name")},
                    {q("nullable_text")},
                    {q("event_date")},
                    {q("event_ts")},
                    {q("amount")},
                    {q("bool_flag")}
                )
            VALUES
                (1, 'Alpha', 'S-1', NULL, DATE '2024-02-03', TIMESTAMP '2024-02-03 04:05:06', 123.45, TRUE),
                (2, 'Beta', 'S-2', 'present', DATE '2024-02-04', TIMESTAMP '2024-02-04 05:06:07', 67.89, FALSE)
            """
        ],
        qualified_select_sql=f"""
            SELECT
                {q("id")} AS id_value,
                {q("Mixed Case")} AS mixed_value,
                {q("special-name")} AS special_value,
                {q("nullable_text")} AS nullable_value,
                {q("event_date")} AS event_date_value,
                {q("event_ts")} AS event_ts_value,
                {q("amount")} AS amount_value,
                {q("bool_flag")} AS bool_value
            FROM {table_ref}
            ORDER BY {q("id")}
        """,
        limit_sql=f"SELECT {q('id')} AS id_value FROM {table_ref} ORDER BY {q('id')} LIMIT 1",
        schema_kwargs={"schema_name": schema},
        expected_columns=(
            "id",
            "Mixed Case",
            "special-name",
            "nullable_text",
            "event_date",
            "event_ts",
            "amount",
            "bool_flag",
        ),
        dialect_select_sqls=(
            f"SELECT 1 AS rows_seen FROM {table_ref} WHERE {q('bool_flag')} IS TRUE LIMIT 1",
            # 'A' mode maps '' to NULL, so IS NULL is the portable emptiness check.
            f"SELECT 1 AS rows_seen FROM {table_ref} WHERE {q('nullable_text')} IS NULL LIMIT 1",
        ),
    )

    contract.assert_table_contract(connector, case)
