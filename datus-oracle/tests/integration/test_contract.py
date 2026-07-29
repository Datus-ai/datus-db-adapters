# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import uuid

import pytest
from datus_oracle import OracleConfig, OracleConnector

from datus_db_core.testing import contract

from .conftest import drop_table_sql


@pytest.mark.integration
@pytest.mark.acceptance
def test_deep_adapter_contract(connector: OracleConnector, config: OracleConfig):
    """Cover schema-qualified SELECT, quoted identifiers, typed values, and FETCH FIRST.

    Oracle deltas from the shared template: no BOOLEAN column type
    (NUMBER(1) with 1/0), no multi-row INSERT VALUES (one INSERT per row),
    no LIMIT clause (FETCH FIRST), and DROP TABLE IF EXISTS emulated with a
    PL/SQL block. quote_identifier upper-cases, so mixed-case source names
    fold to upper case.
    """
    suffix = uuid.uuid4().hex[:8]
    table_name = f"CONTRACT_{suffix.upper()}"
    q = connector.quote_identifier
    schema = config.schema_name
    table_ref = f"{q(schema)}.{q(table_name)}"

    case = contract.TableContractCase(
        adapter_name="oracle",
        table_name=table_name,
        drop_sql=drop_table_sql(table_ref),
        create_sql=f"""
            CREATE TABLE {table_ref} (
                {q("id")} NUMBER(10),
                {q("Mixed Case")} VARCHAR2(64),
                {q("special-name")} VARCHAR2(64),
                {q("nullable_text")} VARCHAR2(64),
                {q("event_date")} DATE,
                {q("event_ts")} TIMESTAMP,
                {q("amount")} NUMBER(10, 2),
                {q("bool_flag")} NUMBER(1)
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
                (1, 'Alpha', 'S-1', NULL, DATE '2024-02-03', TIMESTAMP '2024-02-03 04:05:06', 123.45, 1)
            """,
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
                (2, 'Beta', 'S-2', 'present', DATE '2024-02-04', TIMESTAMP '2024-02-04 05:06:07', 67.89, 0)
            """,
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
        limit_sql=f"SELECT {q('id')} AS id_value FROM {table_ref} ORDER BY {q('id')} FETCH FIRST 1 ROWS ONLY",
        schema_kwargs={"schema_name": schema},
        expected_columns=(
            "ID",
            "MIXED CASE",
            "SPECIAL-NAME",
            "NULLABLE_TEXT",
            "EVENT_DATE",
            "EVENT_TS",
            "AMOUNT",
            "BOOL_FLAG",
        ),
        dialect_select_sqls=(
            f"SELECT 1 AS rows_seen FROM {table_ref} WHERE {q('bool_flag')} = 1 FETCH FIRST 1 ROWS ONLY",
        ),
    )

    contract.assert_table_contract(connector, case)
