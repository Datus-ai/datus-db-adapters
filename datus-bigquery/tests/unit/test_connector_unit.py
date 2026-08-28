from unittest.mock import MagicMock, call, patch

import pandas as pd
import pytest
from sqlalchemy.pool import NullPool

from datus_bigquery import BigQueryConfig, BigQueryConnector
from datus_db_core import DatusDbException


@pytest.fixture
def connector() -> BigQueryConnector:
    return BigQueryConnector(
        BigQueryConfig(
            project="my-project",
            dataset="analytics",
            credentials_info={"type": "service_account", "private_key": "secret"},
            billing_project_id="billing-project",
            location="US",
        )
    )


def test_connector_uses_current_core_context_defaults(connector):
    assert connector.catalog_name == "my-project"
    assert connector.database_name == "analytics"
    assert connector.schema_name == ""
    assert connector.connection_string == "bigquery://my-project/analytics"


def test_to_dict_reports_only_non_secret_connection_identity(connector):
    assert connector.to_dict() == {
        "db_type": "bigquery",
        "project": "my-project",
        "dataset": "analytics",
        "location": "US",
        "billing_project_id": "billing-project",
    }


def test_engine_creation_uses_null_pool_and_unwraps_credentials_only_for_driver(connector):
    engine = MagicMock()
    with patch("datus_bigquery.connector.create_engine", return_value=engine) as create:
        assert connector._ensure_engine() is engine

    create.assert_called_once_with(
        "bigquery://my-project/analytics",
        poolclass=NullPool,
        credentials_info={"type": "service_account", "private_key": "secret"},
        billing_project_id="billing-project",
        location="US",
    )
    assert "secret" not in connector.connection_string
    assert "credentials" not in str(connector.to_dict()).lower()


def test_per_call_context_uses_matching_engine_without_old_signature_type_error(connector):
    connection = MagicMock()
    engine = MagicMock()
    engine.connect.return_value = connection

    with patch.object(connector, "_get_engine", return_value=engine) as get_engine:
        with connector._conn(catalog_name="other-project", database_name="other_dataset") as actual:
            assert actual is connection

    get_engine.assert_called_once_with("other-project", "other_dataset")
    connection.close.assert_called_once_with()


def test_engine_cache_is_keyed_by_project_and_dataset(connector):
    first = MagicMock()
    second = MagicMock()
    with patch("datus_bigquery.connector.create_engine", side_effect=[first, second]) as create:
        assert connector._get_engine("p1", "d1") is first
        assert connector._get_engine("p1", "d1") is first
        assert connector._get_engine("p1", "d2") is second

    assert create.call_count == 2


def test_close_disposes_every_cached_engine_once(connector):
    first = MagicMock()
    second = MagicMock()
    connector._engines[("p1", "d1")] = first
    connector._engines[("p2", "d2")] = second
    connector.engine = first

    connector.close()

    first.dispose.assert_called_once_with()
    second.dispose.assert_called_once_with()
    assert connector._engines == {}
    assert connector.engine is None


@pytest.mark.acceptance
def test_namespace_and_identifier_format(connector):
    assert connector.quote_identifier("a`b") == "`a\\`b`"
    assert connector.full_name(table_name="events") == "`my-project`.`analytics`.`events`"
    assert connector.identifier(table_name="events") == "my-project.analytics.events"
    assert connector.get_schemas() == []


def test_get_tables_excludes_views_and_passes_context(connector):
    frame = pd.DataFrame({"table_name": ["external_events", "orders"]})
    with patch.object(connector, "_execute_pandas", return_value=frame) as execute:
        assert connector.get_tables(catalog_name="my-project", database_name="analytics") == [
            "external_events",
            "orders",
        ]

    sql = execute.call_args.args[0]
    assert "BASE TABLE" in sql
    assert "EXTERNAL" in sql
    assert "'VIEW'" not in sql
    assert execute.call_args.kwargs == {"catalog_name": "my-project", "database_name": "analytics"}


@pytest.mark.parametrize(
    ("context", "expected"),
    [
        ({}, "my-project.analytics.orders"),
        ({"catalog_name": "my-project"}, "analytics.orders"),
        ({"database_name": "analytics"}, "my-project.orders"),
        ({"catalog_name": "my-project", "database_name": "analytics"}, "orders"),
    ],
)
def test_listed_names_include_only_omitted_context(connector, context, expected):
    frame = pd.DataFrame({"table_name": ["orders"]})
    with patch.object(connector, "_execute_pandas", return_value=frame):
        assert connector.get_tables(**context) == [expected]
        assert connector.full_name(table_name=expected, **context) == "`my-project`.`analytics`.`orders`"


@pytest.mark.parametrize(
    ("method", "bigquery_type"),
    [("get_views", "VIEW"), ("get_materialized_views", "MATERIALIZED VIEW")],
)
def test_view_lists_are_separate(connector, method, bigquery_type):
    with patch.object(connector, "_execute_pandas", return_value=pd.DataFrame({"table_name": ["v"]})) as execute:
        assert getattr(connector, method)(catalog_name="my-project", database_name="analytics") == ["v"]

    sql = execute.call_args.args[0]
    assert f"'{bigquery_type}'" in sql
    if bigquery_type == "VIEW":
        assert "MATERIALIZED VIEW" not in sql


@pytest.mark.parametrize(
    "requested",
    ["orders", "my-project.analytics.orders", "`my-project`.`analytics`.`orders`"],
)
def test_ddl_methods_preserve_table_type_and_filter_tables(connector, requested):
    frame = pd.DataFrame(
        {
            "table_name": ["orders", "customers"],
            "ddl": ["CREATE TABLE orders (id INT64)", None],
        }
    )
    with patch.object(connector, "_execute_pandas", return_value=frame):
        tables = connector.get_tables_with_ddl(tables=[requested])

    assert tables == [
        {
            "identifier": "my-project.analytics.orders",
            "catalog_name": "my-project",
            "database_name": "analytics",
            "schema_name": "",
            "table_name": "orders",
            "table_type": "table",
            "definition": "CREATE TABLE orders (id INT64)",
        }
    ]


def test_view_and_materialized_view_ddl_are_not_returned_as_tables(connector):
    frame = pd.DataFrame({"table_name": ["summary"], "ddl": ["CREATE VIEW summary AS SELECT 1"]})
    with patch.object(connector, "_execute_pandas", return_value=frame) as execute:
        assert connector.get_views_with_ddl()[0]["table_type"] == "view"
        assert connector.get_materialized_views_with_ddl()[0]["table_type"] == "mv"

    assert execute.call_args_list[0].args[0].count("'VIEW'") == 1
    assert "MATERIALIZED VIEW" in execute.call_args_list[1].args[0]


def test_get_schema_escapes_table_literal_and_handles_null_default(connector):
    frame = pd.DataFrame(
        {
            "column_name": ["id"],
            "data_type": ["INT64"],
            "is_nullable": ["NO"],
            "column_default": [None],
            "ordinal_position": [1],
        }
    )
    with patch.object(connector, "_execute_pandas", return_value=frame) as execute:
        schema = connector.get_schema(table_name="owner's_table")

    assert "owner''s_table" in execute.call_args.args[0]
    assert schema == [
        {
            "cid": 1,
            "name": "id",
            "type": "INT64",
            "nullable": False,
            "default_value": "",
            "pk": False,
        }
    ]


def test_get_databases_uses_dialect_inspector_for_cross_region_listing(connector):
    inspector = MagicMock()
    inspector.get_schema_names.return_value = ["analytics", "INFORMATION_SCHEMA"]
    with (
        patch.object(connector, "_get_engine", return_value=MagicMock()) as get_engine,
        patch("datus_bigquery.connector.inspect", return_value=inspector),
    ):
        assert connector.get_databases() == ["analytics"]

    get_engine.assert_called_once_with("my-project")


@pytest.mark.parametrize(
    ("table_type", "expected_calls", "expected_types"),
    [
        ("table", ["get_tables"], ["table"]),
        ("view", ["get_views"], ["view"]),
        ("mv", ["get_materialized_views"], ["mv"]),
        ("full", ["get_tables", "get_views", "get_materialized_views"], ["table", "view", "mv"]),
    ],
)
def test_sample_rows_honors_table_type(connector, table_type, expected_calls, expected_types):
    methods = {
        "get_tables": patch.object(connector, "get_tables", return_value=["t"]),
        "get_views": patch.object(connector, "get_views", return_value=["v"]),
        "get_materialized_views": patch.object(connector, "get_materialized_views", return_value=["mv"]),
    }
    mocks = {name: context.start() for name, context in methods.items()}
    try:
        with patch.object(connector, "_execute_pandas", return_value=pd.DataFrame({"id": [1]})) as execute:
            samples = connector.get_sample_rows(table_type=table_type, top_n=1)
    finally:
        for context in methods.values():
            context.stop()

    assert [name for name, mock in mocks.items() if mock.called] == expected_calls
    assert [sample["table_type"] for sample in samples] == expected_types
    assert execute.call_args_list == [
        call(
            f"SELECT * FROM `my-project`.`analytics`.`{name}` LIMIT 1",
            catalog_name="my-project",
            database_name="analytics",
        )
        for name in ("t", "v", "mv")
        if name in {sample["table_name"] for sample in samples}
    ]


@pytest.mark.parametrize("top_n", [0, -1, "bad"])
def test_sample_rows_rejects_invalid_limit(connector, top_n):
    with pytest.raises(DatusDbException, match="positive integer"):
        connector.get_sample_rows(tables=["orders"], top_n=top_n)


def test_metadata_operations_require_a_dataset():
    connector = BigQueryConnector({"project": "my-project"})

    with pytest.raises(DatusDbException, match="requires a dataset"):
        connector.get_tables()
