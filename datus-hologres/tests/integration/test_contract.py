# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

import uuid

import pytest

from datus_db_core.testing import contract
from datus_hologres import HologresConnector


@pytest.mark.integration
@pytest.mark.acceptance
def test_deep_adapter_contract(connector: HologresConnector):
    suffix = uuid.uuid4().hex[:8]
    table_name = f"contract_{suffix}"
    q = connector.quote_identifier
    schema = connector.schema_name
    table_ref = f"{q(schema)}.{q(table_name)}"

    case = contract.TableContractCase(
        adapter_name="hologres",
        table_name=table_name,
        drop_sql=f"DROP TABLE IF EXISTS {table_ref}",
        create_sql=f"""
            CREATE TABLE {table_ref} (
                {q("id")} INTEGER NOT NULL,
                {q("Mixed Case")} TEXT,
                {q("special-name")} TEXT,
                {q("nullable_text")} TEXT,
                {q("event_date")} DATE,
                {q("event_ts")} TIMESTAMPTZ NOT NULL,
                {q("amount")} DECIMAL(10, 2),
                {q("bool_flag")} BOOLEAN,
                PRIMARY KEY ({q("id")})
            )
            WITH (
                orientation = 'column',
                distribution_key = 'id',
                event_time_column = 'event_ts'
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
                (1, 'Alpha', 'S-1', NULL, DATE '2024-02-03',
                 TIMESTAMPTZ '2024-02-03 04:05:06+00', 123.45, TRUE),
                (2, 'Beta', 'S-2', 'present', DATE '2024-02-04',
                 TIMESTAMPTZ '2024-02-04 05:06:07+00', 67.89, FALSE)
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
        dialect_select_sqls=(f"SELECT 1 AS rows_seen FROM {table_ref} WHERE {q('bool_flag')} IS TRUE LIMIT 1",),
    )

    contract.assert_table_contract(connector, case)
