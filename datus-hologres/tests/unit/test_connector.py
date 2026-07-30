# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

from unittest.mock import patch

import pandas as pd
import pytest

from datus_db_core import DatusDbException
from datus_hologres import HologresConfig, HologresConnector
from datus_hologres.handlers import parse_hologres_identifier
from datus_postgresql import PostgreSQLConnector


@pytest.fixture
def config():
    return HologresConfig(
        host="example.hologres.aliyuncs.com",
        username="access-id",
        password="access-secret",
        database="analytics",
        schema="public",
        sslmode="disable",
    )


@pytest.fixture
def connector(config):
    return HologresConnector(config)


def test_connector_uses_hologres_dialect_and_postgresql_driver(connector):
    assert connector.dialect == "hologres"
    assert connector.connection_string.startswith("postgresql+psycopg2://")
    assert connector.connection_string.endswith("/analytics?sslmode=disable")


def test_postgresql_connector_keeps_existing_dialect():
    connector = PostgreSQLConnector(
        {
            "host": "localhost",
            "username": "postgres",
            "password": "secret",
            "database": "postgres",
        }
    )
    assert connector.dialect == "postgresql"


def test_system_resources_include_hologres_names(connector):
    assert "postgres" in connector._sys_databases()
    assert "hologres" in connector._sys_schemas()
    assert "hologres_streaming_mv" in connector._sys_schemas()


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ("public", False),
        ("reporting", False),
        ("information_schema", True),
        ("pg_catalog", True),
        ("pg_temp_12", True),
        ("hg_internal", True),
        ("hologres", True),
        ("hologres_sample", True),
    ],
)
def test_system_schema_prefix_filter(schema, expected):
    assert HologresConnector._is_system_schema(schema) is expected


def test_get_schemas_filters_hologres_internal_schemas(connector):
    schemas = ["public", "reporting", "pg_catalog", "hg_internal", "hologres_statistic"]
    with patch.object(PostgreSQLConnector, "get_schemas", return_value=schemas):
        assert connector.get_schemas() == ["public", "reporting"]
        assert connector.get_schemas(include_sys=True) == schemas


@pytest.mark.parametrize(
    ("database_arg", "schema_arg", "expected"),
    [
        ("", "", "analytics.reporting.orders"),
        ("analytics", "", "reporting.orders"),
        ("", "reporting", "analytics.reporting.orders"),
        ("analytics", "reporting", "orders"),
    ],
)
def test_qualify_name_is_unambiguous(database_arg, schema_arg, expected):
    metadata = {
        "database_name": "analytics",
        "schema_name": "reporting",
        "table_name": "orders",
    }

    assert HologresConnector._qualify_name(metadata, database_arg, schema_arg) == expected


def test_schema_scoped_listing_round_trips_through_identifier_parser(connector):
    metadata = [
        {
            "identifier": "analytics.reporting.orders",
            "catalog_name": "",
            "database_name": "analytics",
            "schema_name": "reporting",
            "table_name": "orders",
            "table_type": "table",
        }
    ]
    with patch.object(connector, "_get_metadata", return_value=metadata):
        listed = connector.get_tables(schema_name="reporting")

    assert listed == ["analytics.reporting.orders"]
    assert parse_hologres_identifier(listed[0]) == {
        "catalog_name": "",
        "database_name": "analytics",
        "schema_name": "reporting",
        "table_name": "orders",
    }


def test_listing_quotes_special_identifier_parts_for_parser_round_trip(connector):
    metadata = {
        "database_name": "analytics",
        "schema_name": 'Mixed"Schema',
        "table_name": "Order.Items",
    }

    listed = connector._qualify_name(metadata, "", "reporting")

    assert listed == 'analytics."Mixed""Schema"."Order.Items"'
    assert parse_hologres_identifier(listed) == {
        "catalog_name": "",
        "database_name": "analytics",
        "schema_name": 'Mixed"Schema',
        "table_name": "Order.Items",
    }


def test_identifier_quotes_special_parts_for_parser_round_trip(connector):
    identifier = connector.identifier(
        database_name="analytics",
        schema_name='Mixed"Schema',
        table_name="Order.Items",
    )

    assert identifier == 'analytics."Mixed""Schema"."Order.Items"'
    assert parse_hologres_identifier(identifier)["table_name"] == "Order.Items"


def test_table_properties_are_whitelisted(connector):
    properties = pd.DataFrame(
        [
            {"property_key": "orientation", "property_value": "column"},
            {"property_key": "distribution_key", "property_value": "id"},
            {"property_key": "segment_key", "property_value": "occurred_at"},
            {"property_key": "binlog.level", "property_value": "replica"},
            {"property_key": "binlog.ttl", "property_value": "86400"},
            {"property_key": "table_group", "property_value": "database_specific_group"},
            {"property_key": "time_to_live_in_seconds", "property_value": "3153600000"},
            {"property_key": "internal_only", "property_value": "hidden"},
        ]
    )
    with patch.object(connector, "_execute_pandas", return_value=properties):
        assert connector._get_table_properties("public", "orders", database_name="warehouse") == {
            "orientation": "column",
            "distribution_key": "id",
            "event_time_column": "occurred_at",
            "binlog_level": "replica",
            "binlog_ttl": "86400",
        }


def test_metadata_includes_foreign_tables(connector):
    base_metadata = [
        {
            "identifier": "warehouse.public.orders",
            "catalog_name": "",
            "database_name": "warehouse",
            "schema_name": "public",
            "table_name": "orders",
            "table_type": "table",
        }
    ]
    foreign_tables = pd.DataFrame([{"table_schema": "public", "table_name": "maxcompute_orders"}])
    with (
        patch.object(PostgreSQLConnector, "_get_metadata", return_value=base_metadata),
        patch.object(connector, "_execute_pandas", return_value=foreign_tables),
    ):
        metadata = connector._get_metadata(
            "table",
            database_name="warehouse",
            schema_name="public",
        )

    assert [item["table_name"] for item in metadata] == ["orders", "maxcompute_orders"]
    assert metadata[1]["identifier"] == "warehouse.public.maxcompute_orders"


@pytest.mark.parametrize(
    "method_name",
    ["_get_table_properties", "_is_foreign_table"],
)
def test_hologres_metadata_helpers_use_requested_database(connector, method_name):
    with patch.object(connector, "_execute_pandas", return_value=pd.DataFrame()) as execute:
        getattr(connector, method_name)("public", "orders", database_name="warehouse")

    assert execute.call_args.kwargs["database_name"] == "warehouse"


def test_table_ddl_includes_hologres_properties(connector):
    with (
        patch.object(connector, "_is_foreign_table", return_value=False) as is_foreign,
        patch.object(
            PostgreSQLConnector,
            "_get_ddl",
            return_value='CREATE TABLE "analytics"."public"."orders" (\n    "id" bigint NOT NULL\n);',
        ) as get_postgresql_ddl,
        patch.object(
            connector,
            "_get_table_properties",
            return_value={
                "orientation": "column",
                "distribution_key": "id",
                "binlog_level": "replica",
                "binlog_ttl": "86400",
            },
        ) as get_properties,
    ):
        ddl = connector._get_ddl("public", "orders", database_name="warehouse")

    assert "WITH (" in ddl
    assert "orientation = 'column'" in ddl
    assert "distribution_key = 'id'" in ddl
    assert "binlog_level = 'replica'" in ddl
    assert "binlog_ttl = '86400'" in ddl
    assert "binlog.level" not in ddl
    assert "binlog.ttl" not in ddl
    assert ddl.endswith(");")
    is_foreign.assert_called_once_with("public", "orders", database_name="warehouse")
    get_postgresql_ddl.assert_called_once_with(
        "public",
        "orders",
        "TABLE",
        database_name="warehouse",
    )
    get_properties.assert_called_once_with("public", "orders", database_name="warehouse")


def test_foreign_table_ddl_includes_server_and_options(connector):
    details = pd.DataFrame(
        [
            {
                "server_name": "odps_server",
                "table_options": ["project_name=MAXCOMPUTE_PUBLIC_DATA", "table_name=orders"],
            }
        ]
    )
    columns = [
        {"name": "order_id", "type": "bigint", "nullable": False, "default_value": None},
        {"name": "amount", "type": "numeric", "nullable": True, "default_value": None},
    ]
    with (
        patch.object(connector, "_execute_pandas", return_value=details) as execute,
        patch.object(connector, "get_schema", return_value=columns) as get_schema,
    ):
        ddl = connector._get_foreign_table_ddl("public", "orders_ext", database_name="warehouse")

    assert ddl.startswith('CREATE FOREIGN TABLE "warehouse"."public"."orders_ext"')
    assert 'SERVER "odps_server"' in ddl
    assert "\"project_name\" 'MAXCOMPUTE_PUBLIC_DATA'" in ddl
    assert "\"table_name\" 'orders'" in ddl
    assert execute.call_args.kwargs["database_name"] == "warehouse"
    get_schema.assert_called_once_with(
        database_name="warehouse",
        schema_name="public",
        table_name="orders_ext",
    )


def test_foreign_table_ddl_accepts_array_like_options(connector):
    details = pd.DataFrame(
        [
            {
                "server_name": "odps_server",
                "table_options": pd.Series(
                    ["project_name=MAXCOMPUTE_PUBLIC_DATA", "table_name=orders"],
                    dtype=object,
                ),
            }
        ]
    )
    columns = [{"name": "order_id", "type": "bigint", "nullable": False, "default_value": None}]
    with (
        patch.object(connector, "_execute_pandas", return_value=details),
        patch.object(connector, "get_schema", return_value=columns),
    ):
        ddl = connector._get_foreign_table_ddl("public", "orders_ext")

    assert "\"project_name\" 'MAXCOMPUTE_PUBLIC_DATA'" in ddl
    assert "\"table_name\" 'orders'" in ddl


def test_bulk_table_ddl_batches_foreign_table_lookup(connector):
    base_metadata = [
        {
            "identifier": "analytics.public.orders",
            "catalog_name": "",
            "database_name": "analytics",
            "schema_name": "public",
            "table_name": "orders",
            "table_type": "table",
        }
    ]
    with (
        patch.object(PostgreSQLConnector, "_get_metadata", return_value=base_metadata),
        patch.object(
            connector,
            "_query_foreign_tables",
            return_value=(("public", "external_orders"),),
        ) as query_foreign_tables,
        patch.object(
            PostgreSQLConnector,
            "_get_ddl",
            return_value='CREATE TABLE "analytics"."public"."orders" ("id" bigint);',
        ),
        patch.object(
            connector,
            "_get_foreign_table_ddl",
            return_value='CREATE FOREIGN TABLE "analytics"."public"."external_orders" ("id" bigint);',
        ),
        patch.object(connector, "_get_table_properties", return_value={}),
    ):
        metadata = connector.get_tables_with_ddl(
            database_name="analytics",
            schema_name="public",
        )

    assert query_foreign_tables.call_count == 1
    assert [item["table_name"] for item in metadata] == ["orders", "external_orders"]
    assert metadata[1]["definition"].startswith("CREATE FOREIGN TABLE")


def test_view_ddl_does_not_query_table_properties(connector):
    with (
        patch.object(PostgreSQLConnector, "_get_ddl", return_value="CREATE VIEW public.v AS SELECT 1"),
        patch.object(connector, "_get_table_properties") as get_properties,
    ):
        assert connector._get_ddl("public", "v", "VIEW") == "CREATE VIEW public.v AS SELECT 1"
    get_properties.assert_not_called()


def test_execute_queries_allows_ddl_batch(connector):
    with patch.object(PostgreSQLConnector, "execute_queries", return_value=[None, None]) as execute:
        result = connector.execute_queries(["CREATE TABLE t (id INT)", "DROP TABLE t"])

    assert result == [None, None]
    execute.assert_called_once()


@pytest.mark.parametrize(
    "queries",
    [
        ["CREATE TABLE t (id INT)", "INSERT INTO t VALUES (1)"],
        ["INSERT INTO t VALUES (1)", "UPDATE t SET id = 2"],
        ["SELECT * FROM t", "DELETE FROM t"],
    ],
)
def test_execute_queries_rejects_unsupported_transactions(connector, queries):
    with pytest.raises(DatusDbException):
        connector.execute_queries(queries)


def test_migration_capabilities_are_hologres_specific(connector):
    capabilities = connector.describe_migration_capabilities()

    assert capabilities["supported"] is True
    assert any("distribution key" in item.lower() for item in capabilities["requires"])
    assert "orientation" in capabilities["layout_hints"]


def test_suggest_layout_prefers_primary_key_and_non_null_time(connector):
    layout = connector.suggest_table_layout(
        [
            {"name": "event_id", "type": "BIGINT", "nullable": False, "pk": True},
            {"name": "occurred_at", "type": "TIMESTAMPTZ", "nullable": False},
            {"name": "payload", "type": "JSONB", "nullable": True},
        ]
    )

    assert layout == {
        "orientation": "column",
        "distribution_key": ["event_id"],
        "event_time_column": ["occurred_at"],
    }


def test_validate_ddl_reports_unsupported_constraints(connector):
    errors = connector.validate_ddl(
        """
        CREATE TABLE t (
            id BIGINT UNIQUE,
            amount INT CHECK (amount > 0),
            parent_id BIGINT,
            FOREIGN KEY (parent_id) REFERENCES parent(id)
        )
        """
    )

    assert any("UNIQUE" in error for error in errors)
    assert any("CHECK" in error for error in errors)
    assert any("FOREIGN KEY" in error for error in errors)


@pytest.mark.parametrize(
    ("source_type", "expected"),
    [
        ("STRING", "TEXT"),
        ("HUGEINT", "NUMERIC(38,0)"),
        ("DATETIME", "TIMESTAMP"),
        ("VARCHAR(255)", None),
    ],
)
def test_map_source_type(connector, source_type, expected):
    assert connector.map_source_type("source", source_type) == expected
