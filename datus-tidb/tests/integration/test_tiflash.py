# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""TiFlash columnar-replica coverage: metadata, plan shape, and MPP push-down."""

import pytest

from datus_tidb import TiDBConnector


def _explain_tasks(connector: TiDBConnector, sql: str) -> list[dict]:
    result = connector.execute({"sql_query": f"EXPLAIN {sql}"}, result_format="list")
    assert result.success, f"EXPLAIN failed: {result.error}"
    return result.sql_return


def _rows(connector: TiDBConnector, sql: str) -> list[dict]:
    result = connector.execute({"sql_query": sql}, result_format="list")
    assert result.success, f"query failed: {result.error}"
    return result.sql_return


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_tiflash_replicas_reports_a_synced_replica(connector: TiDBConnector, columnar_table: str, config):
    replicas = connector.get_tiflash_replicas(database_name=config.database)
    by_table = {replica["table_name"]: replica for replica in replicas}

    assert columnar_table in by_table
    replica = by_table[columnar_table]
    assert replica["replica_count"] == 1
    assert replica["available"] is True
    assert replica["progress"] == pytest.approx(1.0)
    assert replica["database_name"] == config.database


@pytest.mark.integration
def test_tables_without_a_replica_are_absent(connector: TiDBConnector, temp_table: str, config):
    """`temp_table` never gets a replica, so it must not appear — otherwise the
    caller cannot tell columnar-ready tables from row-store-only ones."""
    replicated = {r["table_name"] for r in connector.get_tiflash_replicas(database_name=config.database)}

    assert temp_table not in replicated


@pytest.mark.integration
@pytest.mark.acceptance
def test_aggregation_scans_the_columnar_replica(connector: TiDBConnector, columnar_table: str):
    """With a replica present the optimizer reaches TiFlash on its own — no hint."""
    plan = _explain_tasks(connector, f"SELECT grp, SUM(amount) FROM `{columnar_table}` GROUP BY grp")

    tasks = " ".join(str(row["task"]) for row in plan)
    assert "mpp[tiflash]" in tasks, f"expected an MPP plan, got:\n{plan}"


@pytest.mark.integration
def test_columnar_and_row_store_agree(connector: TiDBConnector, columnar_table: str):
    """Same query, both engines, identical answers — the replica is consistent,
    not an eventually-consistent copy."""
    query = "SELECT /*+ read_from_storage({engine}[{table}]) */ grp, SUM(amount) AS total FROM `{table}` GROUP BY grp ORDER BY grp"

    columnar = _rows(connector, query.format(engine="tiflash", table=columnar_table))
    row_store = _rows(connector, query.format(engine="tikv", table=columnar_table))

    assert columnar == row_store
    assert len(columnar) == 4


@pytest.mark.integration
def test_ranking_window_functions_push_down_to_mpp(connector: TiDBConnector, columnar_table: str):
    """ROW_NUMBER is one of the seven window functions TiFlash implements in MPP.

    Pinned deliberately: the packaged SQL skill tells the model these push down,
    so a TiFlash regression that quietly moves them back to the TiDB layer must
    fail here rather than turn the skill into a lie.
    """
    connector.execute({"sql_query": "SET SESSION tidb_enforce_mpp=1"})
    plan = _explain_tasks(
        connector,
        f"SELECT ROW_NUMBER() OVER (PARTITION BY grp ORDER BY id) FROM `{columnar_table}`",
    )

    window_tasks = [str(row["task"]) for row in plan if "Window" in str(row["id"])]
    assert window_tasks, f"no window operator in plan:\n{plan}"
    assert all("mpp[tiflash]" in task for task in window_tasks), f"ROW_NUMBER left the MPP layer:\n{plan}"


@pytest.mark.integration
def test_aggregate_window_functions_stay_on_the_tidb_layer(connector: TiDBConnector, columnar_table: str):
    """The other half of the same contract: SUM over a window does NOT push down.

    This is why the skill steers the model towards GROUP BY. If TiFlash gains
    aggregate-window MPP support, this test fails and the skill's claim — and
    that steer — should be revisited.
    """
    connector.execute({"sql_query": "SET SESSION tidb_enforce_mpp=1"})
    plan = _explain_tasks(
        connector,
        f"SELECT SUM(amount) OVER (PARTITION BY grp) FROM `{columnar_table}`",
    )

    window_tasks = [str(row["task"]) for row in plan if "Window" in str(row["id"])]
    assert window_tasks, f"no window operator in plan:\n{plan}"
    assert all(task == "root" for task in window_tasks), f"aggregate window unexpectedly pushed down:\n{plan}"
    # The scan itself still benefits from columnar storage.
    assert any("mpp[tiflash]" in str(row["task"]) for row in plan), f"scan left TiFlash entirely:\n{plan}"


@pytest.mark.integration
def test_group_by_aggregation_pushes_down_where_its_window_form_cannot(
    connector: TiDBConnector,
    columnar_table: str,
):
    """The rewrite the skill recommends is measurably better placed."""
    connector.execute({"sql_query": "SET SESSION tidb_enforce_mpp=1"})
    plan = _explain_tasks(connector, f"SELECT grp, SUM(amount) FROM `{columnar_table}` GROUP BY grp")

    agg_tasks = [str(row["task"]) for row in plan if "Agg" in str(row["id"])]
    assert agg_tasks, f"no aggregation operator in plan:\n{plan}"
    assert any("mpp[tiflash]" in task for task in agg_tasks), f"GROUP BY aggregation did not push down:\n{plan}"
