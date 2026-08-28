# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pyarrow as pa
import pytest
from odps.errors import InternalServerError, ODPSError, WaitTimeoutError
from odps.rest import RestClient
from pydantic import BaseModel

from datus_db_core import DatusDbException
from datus_maxcompute import MaxComputeConfig, MaxComputeConnector
from datus_maxcompute.connector import _coerce_config, _TimeoutRestClient


@pytest.fixture
def config():
    return MaxComputeConfig(
        project="project_a",
        endpoint="https://service.example/api",
        access_key_id="id",
        access_key_secret="secret",
        query_timeout_seconds=17,
    )


def make_connector(config, list_schemas=None):
    odps = MagicMock()
    odps.list_schemas.return_value = [SimpleNamespace(name="default")] if list_schemas is None else list_schemas
    with patch("datus_maxcompute.connector.ODPS", return_value=odps):
        connector = MaxComputeConnector(config)
    return connector, odps


def make_instance(table=None):
    instance = MagicMock()
    instance.id = "instance-123"
    reader = MagicMock()
    reader.__enter__.return_value = reader
    reader.__exit__.return_value = False
    if table is None:
        table = pa.table({"id": [1, 2], "name": ["a", "b"]})
    reader.read_all.return_value = table
    instance.open_reader.return_value = reader
    return instance, reader


def test_execute_query_logs_original_exception(config, caplog):
    connector, _ = make_connector(config)
    error = RuntimeError("query failed")

    with patch.object(connector, "_query_arrow", side_effect=error):
        result = connector.execute_query("SELECT bad")

    assert result.success is False
    assert "MaxCompute query execution failed; sql_preview='SELECT bad'; sql_chars=10" in caplog.text
    assert "query failed" in caplog.text
    assert caplog.records[-1].exc_info[2] is error.__traceback__


def test_auto_detects_and_caches_three_level(config):
    connector, odps = make_connector(config)

    assert connector.namespace_mode == "three_level"
    assert connector.get_effective_capabilities() == {"database", "schema"}
    assert connector.schema_name == "default"
    assert connector.namespace_mode == "three_level"
    odps.list_schemas.assert_called_once_with(project="project_a")


def test_auto_detects_two_level_only_for_exact_service_error(config):
    error = InternalServerError("Project project_a is not 3-tier model project.")
    connector, odps = make_connector(config, list_schemas=error)
    odps.list_schemas.side_effect = error

    assert connector.namespace_mode == "two_level"
    assert connector.get_effective_capabilities() == {"database"}
    assert connector.schema_name == ""


def test_auto_detects_two_level_for_current_odps_error(config):
    error = ODPSError(
        "Invalid database operations on two-tier model",
        code="ODPS-0110061",
    )
    connector, odps = make_connector(config)
    odps.list_schemas.side_effect = error

    assert connector.namespace_mode == "two_level"
    assert connector.get_effective_capabilities() == {"database"}


def test_auto_detection_propagates_same_code_with_unrelated_message(config):
    error = ODPSError("unrelated project failure", code="ODPS-0110061")
    connector, odps = make_connector(config)
    odps.list_schemas.side_effect = error

    with pytest.raises(ODPSError, match="unrelated project failure"):
        _ = connector.namespace_mode


def test_auto_detection_propagates_other_internal_errors(config):
    connector, odps = make_connector(config)
    odps.list_schemas.side_effect = InternalServerError("temporary internal failure")

    with pytest.raises(InternalServerError, match="temporary"):
        _ = connector.namespace_mode


def test_explicit_mode_does_not_probe_schema_api(config):
    explicit = config.model_copy(update={"namespace_mode": "two_level"})
    connector, odps = make_connector(explicit)

    assert connector.namespace_mode == "two_level"
    odps.list_schemas.assert_not_called()


def test_query_uses_schema_hint_and_unlimited_arrow_tunnel(config):
    connector, odps = make_connector(config)
    instance, reader = make_instance()
    odps.run_sql.return_value = instance

    result = connector.execute_query("SELECT 1", result_format="list")

    assert result.success
    assert result.sql_return == [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
    assert result.row_count == 2
    odps.run_sql.assert_called_once_with(
        "SELECT 1",
        project="project_a",
        default_schema="default",
        hints={"odps.namespace.schema": "true"},
    )
    instance.wait_for_success.assert_called_once_with(timeout=17)
    reader_call = instance.open_reader.call_args.kwargs
    assert reader_call == {"tunnel": True, "arrow": True, "limit": False, "timeout": 30}
    reader.read_all.assert_called_once_with(count=None)


def test_connector_configures_local_rest_request_timeout(config):
    with patch("datus_maxcompute.connector.ODPS") as odps_constructor:
        MaxComputeConnector(config)

    kwargs = odps_constructor.call_args.kwargs
    assert kwargs["rest_client_cls"] is _TimeoutRestClient
    assert kwargs["rest_client_kwargs"] == {"timeout_seconds": 30}


def test_timeout_rest_client_injects_default_and_preserves_explicit_timeout():
    client = object.__new__(_TimeoutRestClient)
    client._request_timeout = (30, 30)
    with patch.object(RestClient, "request", return_value="ok") as request:
        assert client.request("https://service.example", "GET") == "ok"
        request.assert_called_once_with(
            "https://service.example",
            "GET",
            stream=False,
            timeout=(30, 30),
        )

        request.reset_mock()
        client.request("https://service.example", "POST", timeout=(5, 5))
        request.assert_called_once_with(
            "https://service.example",
            "POST",
            stream=False,
            timeout=(5, 5),
        )


def test_generic_pydantic_config_ignores_inherited_schema_method():
    class GenericConfig(BaseModel):
        project: str
        endpoint: str
        access_key_id: str
        access_key_secret: str

    parsed = _coerce_config(
        GenericConfig(
            project="project_a",
            endpoint="https://service.example/api",
            access_key_id="id",
            access_key_secret="secret",
        )
    )

    assert parsed.schema_name is None


def test_csv_iterator_limits_tunnel_download(config):
    connector, odps = make_connector(config)
    instance, reader = make_instance(pa.table({"id": [1, 2]}))
    reader.read_all.return_value = pa.table({"id": [1]})
    odps.run_sql.return_value = instance

    rows = list(connector.execute_csv_iterator("SELECT id FROM orders", max_rows=1))

    assert rows == [("id",), ("1",)]
    reader.read_all.assert_called_once_with(count=1)


def test_metadata_show_reads_task_result_without_tunnel(config):
    connector, odps = make_connector(config)
    instance, _ = make_instance()
    instance.get_task_result.return_value = "\norders\ncustomers\n\n"
    odps.run_sql.return_value = instance

    result = connector.execute({"sql_query": "SHOW TABLES", "result_format": "list"})

    assert result.success
    assert result.sql_return == [{"result": "orders"}, {"result": "customers"}]
    assert result.row_count == 2
    instance.get_task_result.assert_called_once_with()
    instance.open_reader.assert_not_called()


def test_empty_metadata_show_returns_typed_empty_result(config):
    connector, odps = make_connector(config)
    instance, _ = make_instance()
    instance.get_task_result.return_value = "\n"
    odps.run_sql.return_value = instance

    result = connector.execute_query("SHOW TABLES", result_format="arrow")

    assert result.success
    assert result.sql_return.num_rows == 0
    assert result.sql_return.schema.field("result").type == pa.string()
    instance.open_reader.assert_not_called()


def test_explain_preserves_multiline_task_result(config):
    connector, odps = make_connector(config)
    instance, _ = make_instance()
    instance.get_task_result.return_value = "\njob0 is root job\n\n  VALUES: _c0 : {1}\n"
    odps.run_sql.return_value = instance

    result = connector.execute({"sql_query": "EXPLAIN SELECT 1", "result_format": "list"})

    assert result.success
    assert result.sql_return == [{"result": "job0 is root job\n\n  VALUES: _c0 : {1}"}]
    assert result.row_count == 1
    instance.open_reader.assert_not_called()


def test_two_level_query_uses_false_hint_and_no_schema(config):
    explicit = config.model_copy(update={"namespace_mode": "two_level"})
    connector, odps = make_connector(explicit)
    instance, _ = make_instance()
    odps.run_sql.return_value = instance

    result = connector.execute_query("SELECT 1", result_format="arrow")

    assert result.success
    odps.run_sql.assert_called_once_with(
        "SELECT 1",
        project="project_a",
        hints={"odps.namespace.schema": "false"},
    )


def test_timeout_stops_instance(config):
    connector, odps = make_connector(config)
    instance, _ = make_instance()
    instance.wait_for_success.side_effect = WaitTimeoutError("too slow")
    odps.run_sql.return_value = instance

    result = connector.execute_ddl("CREATE TABLE t (id BIGINT)")

    assert not result.success
    assert "timed out" in result.error.lower()
    instance.stop.assert_called_once()


def test_non_query_returns_instance_id_without_fake_row_count(config):
    connector, odps = make_connector(config)
    instance, _ = make_instance()
    odps.run_sql.return_value = instance

    result = connector.execute_insert("INSERT INTO t VALUES (1)")

    assert result.success
    assert result.sql_return == "instance-123"
    assert result.row_count is None


def test_rejects_cross_project_and_schema_on_two_level(config):
    explicit = config.model_copy(update={"namespace_mode": "two_level"})
    connector, _ = make_connector(explicit)

    with pytest.raises(DatusDbException, match="cross-project"):
        connector.get_tables(database_name="other_project")
    with pytest.raises(DatusDbException, match="has no schema"):
        connector.get_tables(schema_name="analytics")


def test_full_name_follows_detected_namespace(config):
    three_level, _ = make_connector(config)
    two_level, _ = make_connector(config.model_copy(update={"namespace_mode": "two_level"}))

    assert three_level.full_name(table_name="orders") == "`project_a`.`default`.`orders`"
    assert two_level.full_name(table_name="orders") == "`project_a`.`orders`"
    assert three_level.full_name(table_name="project_a.analytics.orders") == "`project_a`.`analytics`.`orders`"


def test_lists_objects_by_type(config):
    connector, odps = make_connector(config)
    odps.list_tables.return_value = [
        SimpleNamespace(name="orders", type=SimpleNamespace(value="MANAGED_TABLE")),
        SimpleNamespace(name="orders_view", type=SimpleNamespace(value="VIRTUAL_VIEW")),
        SimpleNamespace(name="orders_mv", type=SimpleNamespace(value="MATERIALIZED_VIEW")),
    ]

    assert connector.get_tables(schema_name="analytics") == ["project_a.orders"]
    assert connector.get_views(database_name="project_a") == ["project_a.default.orders_view"]
    assert connector.get_materialized_views() == ["project_a.default.orders_mv"]


def test_listed_names_round_trip_across_context_shapes(config):
    connector, odps = make_connector(config)
    odps.list_tables.return_value = [
        SimpleNamespace(name="orders", type=SimpleNamespace(value="MANAGED_TABLE")),
    ]

    assert connector.get_tables() == ["project_a.default.orders"]
    assert connector.get_tables(database_name="project_a") == ["project_a.default.orders"]
    assert connector.get_tables(schema_name="analytics") == ["project_a.orders"]
    assert connector.get_tables(database_name="project_a", schema_name="analytics") == ["orders"]
    assert connector.full_name(table_name="project_a.default.orders") == "`project_a`.`default`.`orders`"
    assert (
        connector.full_name(database_name="project_a", table_name="project_a.default.orders")
        == "`project_a`.`default`.`orders`"
    )
    assert (
        connector.full_name(schema_name="analytics", table_name="project_a.orders")
        == "`project_a`.`analytics`.`orders`"
    )
    assert (
        connector.full_name(database_name="project_a", schema_name="analytics", table_name="orders")
        == "`project_a`.`analytics`.`orders`"
    )


def test_get_tables_with_ddl_matches_the_complete_requested_scope(config):
    connector, odps = make_connector(config)
    orders = MagicMock()
    orders.name = "orders"
    orders.type = SimpleNamespace(value="MANAGED_TABLE")
    orders.get_ddl.return_value = "CREATE TABLE orders (id BIGINT)"
    customers = MagicMock()
    customers.name = "customers"
    customers.type = SimpleNamespace(value="MANAGED_TABLE")
    customers.get_ddl.return_value = "CREATE TABLE customers (id BIGINT)"
    odps.list_tables.return_value = [orders, customers]

    result = connector.get_tables_with_ddl(
        database_name="project_a",
        schema_name="analytics",
        tables=["project_a.analytics.orders"],
    )

    assert [entry["table_name"] for entry in result] == ["orders"]
    orders.get_ddl.assert_called_once_with()
    customers.get_ddl.assert_not_called()


def test_get_tables_with_ddl_rejects_table_from_another_schema(config):
    connector, odps = make_connector(config)
    odps.list_tables.return_value = []

    with pytest.raises(DatusDbException, match="outside requested scope"):
        connector.get_tables_with_ddl(
            database_name="project_a",
            schema_name="analytics",
            tables=["project_a.default.orders"],
        )


def test_get_sample_rows_routes_implicit_view_requests(config):
    connector, odps = make_connector(config)
    odps.list_tables.return_value = [
        SimpleNamespace(name="orders", type=SimpleNamespace(value="MANAGED_TABLE")),
        SimpleNamespace(name="orders_view", type=SimpleNamespace(value="VIRTUAL_VIEW")),
    ]
    query_result = SimpleNamespace(
        success=True,
        sql_return=pd.DataFrame({"id": [1]}),
        error=None,
    )

    with patch.object(connector, "execute_query", return_value=query_result) as execute_query:
        result = connector.get_sample_rows(top_n=2, table_type="view")

    assert [entry["table_name"] for entry in result] == ["orders_view"]
    assert result[0]["table_type"] == "view"
    execute_query.assert_called_once_with(
        "SELECT * FROM `project_a`.`default`.`orders_view` LIMIT 2",
        result_format="pandas",
        database_name="project_a",
        schema_name="default",
    )


def test_get_sample_rows_full_preserves_actual_object_types(config):
    connector, odps = make_connector(config)
    odps.list_tables.return_value = [
        SimpleNamespace(name="orders", type=SimpleNamespace(value="MANAGED_TABLE")),
        SimpleNamespace(name="orders_view", type=SimpleNamespace(value="VIRTUAL_VIEW")),
        SimpleNamespace(name="orders_mv", type=SimpleNamespace(value="MATERIALIZED_VIEW")),
    ]
    query_result = SimpleNamespace(
        success=True,
        sql_return=pd.DataFrame({"id": [1]}),
        error=None,
    )

    with patch.object(connector, "execute_query", return_value=query_result):
        result = connector.get_sample_rows(table_type="full")

    assert [(entry["table_name"], entry["table_type"]) for entry in result] == [
        ("orders", "table"),
        ("orders_view", "view"),
        ("orders_mv", "mv"),
    ]


def test_get_sample_rows_full_resolves_explicit_object_type(config):
    connector, odps = make_connector(config)
    odps.get_table.return_value = SimpleNamespace(
        name="orders_view",
        type=SimpleNamespace(value="VIRTUAL_VIEW"),
    )
    query_result = SimpleNamespace(
        success=True,
        sql_return=pd.DataFrame({"id": [1]}),
        error=None,
    )

    with patch.object(connector, "execute_query", return_value=query_result):
        result = connector.get_sample_rows(
            tables=["project_a.default.orders_view"],
            table_type="full",
        )

    assert result[0]["table_type"] == "view"
    odps.get_table.assert_called_once_with(
        "orders_view",
        project="project_a",
        schema="default",
    )


def test_get_schema_includes_partition_columns(config):
    connector, odps = make_connector(config)
    column = SimpleNamespace(name="id", type="bigint", comment="identifier", nullable=False)
    partition = SimpleNamespace(name="ds", type="string", comment=None, nullable=True)
    table = MagicMock()
    table.table_schema.columns = [column, partition]
    table.table_schema.partitions = [partition]
    odps.get_table.return_value = table

    result = connector.get_schema(schema_name="analytics", table_name="orders")

    assert result[0]["name"] == "id"
    assert result[0]["nullable"] is False
    assert result[1]["is_partition"] is True
    table.reload.assert_called_once()


def test_rejects_transactions_before_submission(config):
    connector, odps = make_connector(config)

    result = connector.execute_ddl("BEGIN TRANSACTION")

    assert not result.success
    assert "does not support transactions" in result.error
    odps.run_sql.assert_not_called()


@pytest.mark.parametrize("sql", ["BEGIN", "START TRANSACTION", "COMMIT", "ROLLBACK"])
def test_execute_routes_transaction_control_to_specific_rejection(config, sql):
    connector, odps = make_connector(config)

    result = connector.execute({"sql_query": sql})

    assert not result.success
    assert "does not support transactions" in result.error
    odps.run_sql.assert_not_called()
