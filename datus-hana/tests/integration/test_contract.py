import uuid

import pytest

from datus_db_core.testing import contract

pytestmark = pytest.mark.integration


def test_deep_adapter_contract(connector, config):
    table = "contract_" + uuid.uuid4().hex[:10]
    q = connector.quote_identifier
    ref = connector.full_name(table_name=table)
    columns = ("id", "Mixed Case", "special-name", "nullable_text", "event_date", "event_ts", "amount", "bool_flag")
    types = ("INTEGER", "NVARCHAR(64)", "NVARCHAR(64)", "NVARCHAR(64)", "DATE", "TIMESTAMP", "DECIMAL(10,2)", "BOOLEAN")
    aliases = (
        "id_value",
        "mixed_value",
        "special_value",
        "nullable_value",
        "event_date_value",
        "event_ts_value",
        "amount_value",
        "bool_value",
    )
    case = contract.TableContractCase(
        adapter_name="hana",
        table_name=table,
        drop_sql=f"DROP TABLE {ref}",
        create_sql=f"CREATE COLUMN TABLE {ref} ("
        + ", ".join(f"{q(col)} {kind}" for col, kind in zip(columns, types, strict=True))
        + ")",
        insert_sqls=(
            f"INSERT INTO {ref} VALUES (1, 'Alpha', 'S-1', NULL, DATE '2024-02-03', TIMESTAMP '2024-02-03 04:05:06', 123.45, TRUE)",
            f"INSERT INTO {ref} VALUES (2, 'Beta', 'S-2', 'present', DATE '2024-02-04', TIMESTAMP '2024-02-04 05:06:07', 67.89, FALSE)",
        ),
        qualified_select_sql="SELECT "
        + ", ".join(f"{q(col)} AS {q(alias)}" for col, alias in zip(columns, aliases, strict=True))
        + f" FROM {ref} ORDER BY {q('id')}",
        limit_sql=f"SELECT * FROM {ref} ORDER BY {q('id')} LIMIT 1",
        schema_kwargs={"schema_name": config.schema_name},
        expected_columns=columns,
    )
    contract.assert_table_contract(connector, case)
