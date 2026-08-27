# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest

from datus_tidb import TiDBConnector


@pytest.mark.integration
@pytest.mark.acceptance
def test_select_returns_typed_rows(connector: TiDBConnector):
    result = connector.execute(
        {"sql_query": "SELECT 1 AS n, 'text' AS s, 1.5 AS d"},
        result_format="list",
    )

    assert result.success, result.error
    row = result.sql_return[0]
    assert int(row["n"]) == 1
    assert row["s"] == "text"
    assert float(row["d"]) == pytest.approx(1.5)


@pytest.mark.integration
def test_insert_update_delete_round_trip(connector: TiDBConnector, temp_table: str):
    assert connector.execute_insert(f"INSERT INTO `{temp_table}` VALUES (1, 'a'), (2, 'b')").success

    updated = connector.execute({"sql_query": f"UPDATE `{temp_table}` SET name='z' WHERE id=1"}, result_format="list")
    assert updated.success, updated.error

    rows = connector.execute(
        {"sql_query": f"SELECT name FROM `{temp_table}` WHERE id=1"}, result_format="list"
    ).sql_return
    assert rows[0]["name"] == "z"

    assert connector.execute({"sql_query": f"DELETE FROM `{temp_table}` WHERE id=2"}, result_format="list").success
    remaining = connector.execute(
        {"sql_query": f"SELECT COUNT(*) AS c FROM `{temp_table}`"}, result_format="list"
    ).sql_return
    assert int(remaining[0]["c"]) == 1


@pytest.mark.integration
def test_invalid_sql_reports_an_error(connector: TiDBConnector):
    result = connector.execute({"sql_query": "SELECT * FROM no_such_table_xyz"}, result_format="list")

    assert not result.success
    assert result.error


@pytest.mark.integration
@pytest.mark.parametrize("result_format", ["list", "csv", "pandas"])
def test_result_formats(connector: TiDBConnector, result_format: str):
    result = connector.execute({"sql_query": "SELECT 1 AS n"}, result_format=result_format)

    assert result.success, result.error
    assert result.sql_return is not None


@pytest.mark.integration
def test_analytical_sql_surface(connector: TiDBConnector, temp_table: str):
    """Window functions, CTEs and set operations all exist in TiDB; the skill
    promises them, so a regression here would make it wrong."""
    connector.execute_insert(f"INSERT INTO `{temp_table}` VALUES (1, 'a'), (2, 'b'), (3, 'a')")

    result = connector.execute(
        {
            "sql_query": f"""
            WITH ranked AS (
                SELECT name, ROW_NUMBER() OVER (PARTITION BY name ORDER BY id) AS rn
                FROM `{temp_table}`
            )
            SELECT name FROM ranked WHERE rn = 1
            EXCEPT
            SELECT 'b'
            """
        },
        result_format="list",
    )

    assert result.success, result.error
    assert [row["name"] for row in result.sql_return] == ["a"]


@pytest.mark.integration
def test_full_outer_join_is_rejected(connector: TiDBConnector, temp_table: str):
    """Pinned so the skill's "no FULL OUTER JOIN" claim stays honest."""
    result = connector.execute(
        {"sql_query": f"SELECT a.id FROM `{temp_table}` a FULL OUTER JOIN `{temp_table}` b ON a.id = b.id"},
        result_format="list",
    )

    assert not result.success


@pytest.mark.integration
def test_check_constraints_are_not_enforced(connector: TiDBConnector):
    """The failure mode validate_ddl() warns about: TiDB accepts the constraint
    and then lets violating rows in, with no error anywhere."""
    table = "datus_check_probe"
    connector.execute_ddl(f"DROP TABLE IF EXISTS `{table}`")
    connector.execute_ddl(f"CREATE TABLE `{table}` (id INT PRIMARY KEY, qty INT CHECK (qty > 0))")
    try:
        inserted = connector.execute_insert(f"INSERT INTO `{table}` VALUES (1, -5)")
        assert inserted.success, "TiDB unexpectedly enforced the CHECK constraint — revisit validate_ddl()"

        rows = connector.execute(
            {"sql_query": f"SELECT qty FROM `{table}` WHERE id = 1"}, result_format="list"
        ).sql_return
        assert int(rows[0]["qty"]) == -5
    finally:
        connector.execute_ddl(f"DROP TABLE IF EXISTS `{table}`")
