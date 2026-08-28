import uuid

import pytest

from datus_bigquery import BigQueryConfig, BigQueryConnector
from datus_db_core.testing.contract import TableContractCase, assert_table_contract


@pytest.mark.integration
@pytest.mark.acceptance
def test_bigquery_adapter_contract(connector: BigQueryConnector, config: BigQueryConfig):
    table_name = f"datus_contract_{uuid.uuid4().hex[:8]}"
    full_name = connector.full_name(
        catalog_name=config.project,
        database_name=config.dataset,
        table_name=table_name,
    )
    case = TableContractCase(
        adapter_name="BigQuery",
        table_name=table_name,
        drop_sql=f"DROP TABLE IF EXISTS {full_name}",
        create_sql=(
            f"CREATE TABLE {full_name} ("
            "id INT64, mixed_name STRING, special_name STRING, nullable_col STRING, "
            "event_date DATE, event_ts TIMESTAMP, amount NUMERIC, enabled BOOL)"
        ),
        insert_sqls=(
            f"INSERT INTO {full_name} VALUES "
            "(1, 'Alpha', 'S-1', NULL, DATE '2024-02-03', "
            "TIMESTAMP '2024-02-03 04:05:06+00', NUMERIC '123.45', TRUE)",
            f"INSERT INTO {full_name} VALUES "
            "(2, 'Beta', 'S-2', 'present', DATE '2024-02-04', "
            "TIMESTAMP '2024-02-04 04:05:06+00', NUMERIC '5.00', FALSE)",
        ),
        qualified_select_sql=(
            "SELECT id AS id_value, mixed_name AS mixed_value, special_name AS special_value, "
            "nullable_col AS nullable_value, event_date AS event_date_value, event_ts AS event_ts_value, "
            f"amount AS amount_value, enabled AS bool_value FROM {full_name} WHERE id = 1"
        ),
        limit_sql=f"SELECT id FROM {full_name} ORDER BY id LIMIT 1",
        schema_kwargs={"catalog_name": config.project, "database_name": config.dataset},
        expected_columns=(
            "id",
            "mixed_name",
            "special_name",
            "nullable_col",
            "event_date",
            "event_ts",
            "amount",
            "enabled",
        ),
        dialect_select_sqls=(
            "SELECT [1, 2, 3][OFFSET(1)] AS second_value",
            "SELECT SAFE_DIVIDE(1, 0) AS safe_value",
        ),
    )

    assert_table_contract(connector, case)
