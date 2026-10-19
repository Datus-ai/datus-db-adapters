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
from datus_maxcompute.connector import (
    _PARTITION_SCAN_LIMIT,
    _coerce_config,
    _declares_partitions,
    _matches_ignore_patterns,
    _TimeoutRestClient,
)


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


def make_unpartitioned_table(name, table_type="MANAGED_TABLE"):
    """A listed table that explicitly declares no partitions.

    ``table_schema`` has to be there: without it the connector cannot tell
    "not partitioned" from "metadata unreadable" and skips the table entirely.
    """
    return SimpleNamespace(
        name=name,
        type=SimpleNamespace(value=table_type),
        table_schema=SimpleNamespace(partitions=None),
    )


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


def make_listed_table(name, table_type="MANAGED_TABLE"):
    """Build an object returned by ``odps.list_tables()``."""
    table = MagicMock()
    table.name = name
    table.type = SimpleNamespace(value=table_type)
    table.get_ddl.return_value = f"CREATE TABLE {name} (id BIGINT)"
    return table


@pytest.fixture
def ignored_config(config):
    """Configure a datasource to ignore tables matching ``tmp_*``."""
    return config.model_copy(update={"ignore_table_patterns": ["tmp_*"]})


def test_ignore_patterns_are_empty_by_default(config):
    """Leave table filtering disabled by default."""
    assert config.ignore_table_patterns == []


@pytest.mark.parametrize(
    ("name", "patterns", "expected"),
    [
        ("tmp_0826", ["tmp_*"], True),
        ("tmp_26061ecd_89f6_4010_88bd_4ea6d95abc22", ["tmp_*"], True),
        ("TMP_0826", ["tmp_*"], True),  # Case-insensitive matching.
        ("orders_bak", ["*_bak"], True),  # Suffix pattern.
        ("orders", ["tmp_*"], False),
        ("temp_orders", ["tmp_*"], False),  # Keep unrelated prefixes.
        ("tmp_0826", [], False),  # An empty list disables filtering.
        ("tmp_0826", None, False),
    ],
)
def test_matches_ignore_patterns(name, patterns, expected):
    assert _matches_ignore_patterns(name, patterns) is expected


def test_get_tables_with_ddl_skips_ignored_tables(ignored_config):
    """Skip DDL reads for ignored tables, including transient scratch tables."""
    connector, odps = make_connector(ignored_config)
    scratch = make_listed_table("tmp_26061ecd_89f6_4010_88bd_4ea6d95abc22")
    orders = make_listed_table("orders")
    odps.list_tables.return_value = [scratch, orders]

    result = connector.get_tables_with_ddl(database_name="project_a", schema_name="default")

    assert [entry["table_name"] for entry in result] == ["orders"]
    scratch.get_ddl.assert_not_called()


def test_get_views_with_ddl_skips_ignored_tables(ignored_config):
    """Apply ignore patterns before fetching view definitions."""
    connector, odps = make_connector(ignored_config)
    scratch = make_listed_table("tmp_0826", "VIRTUAL_VIEW")
    view = make_listed_table("orders_view", "VIRTUAL_VIEW")
    odps.list_tables.return_value = [scratch, view]

    result = connector.get_views_with_ddl(database_name="project_a", schema_name="default")

    assert [entry["table_name"] for entry in result] == ["orders_view"]
    scratch.get_ddl.assert_not_called()


def test_get_materialized_views_with_ddl_skips_ignored_tables(ignored_config):
    """Apply the shared ignore filter to materialized view definitions."""
    connector, odps = make_connector(ignored_config)
    scratch = make_listed_table("tmp_0826", "MATERIALIZED_VIEW")
    materialized = make_listed_table("orders_mv", "MATERIALIZED_VIEW")
    odps.list_tables.return_value = [scratch, materialized]

    result = connector.get_materialized_views_with_ddl(database_name="project_a", schema_name="default")

    assert [entry["table_name"] for entry in result] == ["orders_mv"]
    scratch.get_ddl.assert_not_called()


def test_name_only_listing_skips_ignored_tables(ignored_config):
    """Keep name-only listings consistent with DDL listings."""
    connector, odps = make_connector(ignored_config)
    odps.list_tables.return_value = [make_listed_table("tmp_0826"), make_listed_table("orders")]

    assert connector.get_tables(database_name="project_a", schema_name="default") == ["orders"]


def test_ignore_patterns_accept_multiple_globs(config):
    """Apply every glob in the configured pattern list."""
    connector, odps = make_connector(config.model_copy(update={"ignore_table_patterns": ["tmp_*", "*_bak"]}))
    odps.list_tables.return_value = [
        make_listed_table("tmp_0826"),
        make_listed_table("orders_bak"),
        make_listed_table("orders"),
    ]

    result = connector.get_tables_with_ddl(database_name="project_a", schema_name="default")

    assert [entry["table_name"] for entry in result] == ["orders"]


def test_tables_are_all_listed_when_no_pattern_configured(config):
    """List every table and fetch its DDL when no patterns are configured."""
    connector, odps = make_connector(config)
    scratch = make_listed_table("tmp_0826")
    orders = make_listed_table("orders")
    odps.list_tables.return_value = [scratch, orders]

    result = connector.get_tables_with_ddl(database_name="project_a", schema_name="default")

    assert sorted(entry["table_name"] for entry in result) == ["orders", "tmp_0826"]
    scratch.get_ddl.assert_called_once()


def test_get_sample_rows_routes_implicit_view_requests(config):
    connector, odps = make_connector(config)
    odps.list_tables.return_value = [
        make_unpartitioned_table("orders"),
        make_unpartitioned_table("orders_view", "VIRTUAL_VIEW"),
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
        make_unpartitioned_table("orders"),
        make_unpartitioned_table("orders_view", "VIRTUAL_VIEW"),
        make_unpartitioned_table("orders_mv", "MATERIALIZED_VIEW"),
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


@pytest.mark.parametrize("table_type", ["full", "view"])
def test_get_sample_rows_resolves_explicit_object_type(config, table_type):
    connector, odps = make_connector(config)
    odps.get_table.return_value = make_unpartitioned_table("orders_view", "VIRTUAL_VIEW")
    query_result = SimpleNamespace(
        success=True,
        sql_return=pd.DataFrame({"id": [1]}),
        error=None,
    )

    with patch.object(connector, "execute_query", return_value=query_result):
        result = connector.get_sample_rows(
            tables=["project_a.default.orders_view"],
            table_type=table_type,
        )

    assert result[0]["table_type"] == "view"
    odps.get_table.assert_called_once_with(
        "orders_view",
        project="project_a",
        schema="default",
    )


def test_get_sample_rows_skips_explicit_target_with_mismatched_type(config):
    connector, odps = make_connector(config)
    odps.get_table.return_value = SimpleNamespace(
        name="orders",
        type=SimpleNamespace(value="MANAGED_TABLE"),
    )

    with patch.object(connector, "execute_query") as execute_query:
        result = connector.get_sample_rows(
            tables=["project_a.default.orders"],
            table_type="view",
        )

    assert result == []
    execute_query.assert_not_called()


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


def make_listed_partition(keys, values, size=100):
    """A partition as ``iterate_partitions()`` yields it."""
    return SimpleNamespace(
        partition_spec=SimpleNamespace(keys=list(keys), values=list(values)),
        size=size,
    )


def make_partitioned_table(name="orders", partitions=("pt",), values=("20260911",), listed=None):
    """A listing object shaped like the one ``list_tables()`` returns.

    ``iterate_partitions(reverse=True)`` is served from ``listed`` -- newest first, the
    order the service returns -- defaulting to one partition that holds data. It stays a
    plain iterator so a test can hand it a long list and observe how much is consumed.
    """
    if listed is None:
        listed = [make_listed_partition(partitions, values)]
    return SimpleNamespace(
        name=name,
        type=SimpleNamespace(value="MANAGED_TABLE"),
        table_schema=SimpleNamespace(partitions=[SimpleNamespace(name=key) for key in partitions]),
        iterate_partitions=lambda **kwargs: iter(list(listed)),
    )


def test_get_sample_rows_pins_max_partition(config):
    connector, odps = make_connector(config)
    odps.list_tables.return_value = [make_partitioned_table()]
    query_result = SimpleNamespace(success=True, sql_return=pd.DataFrame({"id": [1]}), error=None)

    with patch.object(connector, "execute_query", return_value=query_result) as execute_query:
        result = connector.get_sample_rows(top_n=2)

    assert len(result) == 1
    execute_query.assert_called_once_with(
        "SELECT * FROM `project_a`.`default`.`orders` WHERE `pt`='20260911' LIMIT 2",
        result_format="pandas",
        database_name="project_a",
        schema_name="default",
    )


def test_get_sample_rows_pins_every_partition_key(config):
    connector, odps = make_connector(config)
    odps.list_tables.return_value = [
        make_partitioned_table(partitions=("pt", "region"), values=("20260911", "east")),
    ]
    query_result = SimpleNamespace(success=True, sql_return=pd.DataFrame({"id": [1]}), error=None)

    with patch.object(connector, "execute_query", return_value=query_result) as execute_query:
        connector.get_sample_rows(top_n=2)

    execute_query.assert_called_once_with(
        "SELECT * FROM `project_a`.`default`.`orders` WHERE `pt`='20260911' AND `region`='east' LIMIT 2",
        result_format="pandas",
        database_name="project_a",
        schema_name="default",
    )


def test_get_sample_rows_omits_predicate_for_unpartitioned_table(config):
    connector, odps = make_connector(config)
    odps.list_tables.return_value = [
        SimpleNamespace(
            name="orders",
            type=SimpleNamespace(value="MANAGED_TABLE"),
            table_schema=SimpleNamespace(partitions=None),
        ),
    ]
    query_result = SimpleNamespace(success=True, sql_return=pd.DataFrame({"id": [1]}), error=None)

    with patch.object(connector, "execute_query", return_value=query_result) as execute_query:
        connector.get_sample_rows(top_n=2)

    execute_query.assert_called_once_with(
        "SELECT * FROM `project_a`.`default`.`orders` LIMIT 2",
        result_format="pandas",
        database_name="project_a",
        schema_name="default",
    )


def test_get_sample_rows_skips_table_with_unreadable_metadata(config):
    """Skip unreadable schemas instead of assuming the table is unpartitioned."""
    connector, odps = make_connector(config)
    # A table object without a table_schema stands in for metadata the driver cannot read.
    odps.list_tables.return_value = [
        SimpleNamespace(name="orders", type=SimpleNamespace(value="MANAGED_TABLE")),
    ]

    with patch.object(connector, "execute_query") as execute_query:
        result = connector.get_sample_rows(top_n=2)

    assert result == []
    execute_query.assert_not_called()


def test_sql_string_literal_escapes_quotes_and_backslashes(config):
    """Escape quotes and backslashes using the MaxCompute string literal rules."""
    connector, _ = make_connector(config)

    assert connector._sql_string_literal("a'b") == "'a\\'b'"
    assert connector._sql_string_literal("a\\b") == "'a\\\\b'"


def test_sample_partition_predicate_prefers_partition_with_data(config):
    """Prefer a populated partition when the newest partition is still empty."""
    connector, _ = make_connector(config)
    table = make_partitioned_table(
        listed=[
            make_listed_partition(["pt"], ["20260912"], size=0),
            make_listed_partition(["pt"], ["20260911"], size=1024),
        ]
    )

    assert connector._sample_partition_predicate(table) == " WHERE `pt`='20260911'"


def test_sample_partition_predicate_falls_back_to_newest_when_page_is_empty(config):
    """Use the newest partition when the page contains no known populated partition."""
    connector, _ = make_connector(config)
    table = make_partitioned_table(
        listed=[
            make_listed_partition(["pt"], ["20260912"], size=None),
            make_listed_partition(["pt"], ["20260911"], size=None),
        ]
    )

    assert connector._sample_partition_predicate(table) == " WHERE `pt`='20260912'"


def test_sample_partition_predicate_reads_one_page_only(config):
    """Bound iteration to one page instead of materializing every partition."""
    connector, _ = make_connector(config)
    consumed = []

    def iterate_partitions(**kwargs):
        def partitions():
            for index in range(1000):
                consumed.append(index)
                yield make_listed_partition(["pt"], [f"pt_{index:04d}"], size=0)

        return partitions()

    table = make_partitioned_table()
    table.iterate_partitions = iterate_partitions

    assert connector._sample_partition_predicate(table) == " WHERE `pt`='pt_0000'"
    assert len(consumed) == _PARTITION_SCAN_LIMIT


def test_sample_partition_predicate_returns_none_when_partition_unresolvable(config):
    """Return no predicate when partition metadata cannot be resolved."""
    connector, _ = make_connector(config)
    table = make_partitioned_table()

    def boom(**kwargs):
        raise ODPSError("partition metadata unavailable")

    table.iterate_partitions = boom

    assert connector._sample_partition_predicate(table) is None


def test_get_sample_rows_skips_partitioned_table_without_predicate(config):
    """Skip unresolved partitions instead of submitting an unfiltered sampling job."""
    connector, odps = make_connector(config)
    table = make_partitioned_table()

    def boom(**kwargs):
        raise ODPSError("partition metadata unavailable")

    table.iterate_partitions = boom
    odps.list_tables.return_value = [table]

    with patch.object(connector, "execute_query") as execute_query:
        result = connector.get_sample_rows(top_n=2)

    assert result == []
    execute_query.assert_not_called()


def test_sample_partition_predicate_quotes_reserved_partition_key(config):
    """Quote reserved words used as partition column names."""
    connector, _ = make_connector(config)
    table = make_partitioned_table(partitions=("select",), values=("20260911",))

    assert connector._sample_partition_predicate(table) == " WHERE `select`='20260911'"


def test_declares_partitions_reports_unknown_when_schema_is_unreadable():
    """Report unknown partition state when schema metadata cannot be read."""
    table_without_schema = SimpleNamespace()

    assert _declares_partitions(table_without_schema) is None


def test_sample_partition_predicate_skips_table_with_unreadable_schema(config):
    """Skip sampling when the schema cannot establish a safe partition predicate."""
    connector, _ = make_connector(config)
    table = SimpleNamespace(name="orders")

    assert connector._sample_partition_predicate(table) is None
