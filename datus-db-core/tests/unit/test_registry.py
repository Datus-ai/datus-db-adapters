# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Unit tests for ConnectorRegistry."""

from unittest.mock import MagicMock, patch

import pytest

from datus_db_core.config import ConnectionConfig
from datus_db_core.exceptions import DatusDbException
from datus_db_core.registry import AdapterMetadata, ConnectorRegistry


class DummyConnector:
    def __init__(self, config):
        self.config = config


class DummyConfig(ConnectionConfig):
    host: str = "localhost"


@pytest.fixture(autouse=True)
def clean_registry():
    """Reset registry state before each test."""
    saved = {
        "_connectors": ConnectorRegistry._connectors.copy(),
        "_factories": ConnectorRegistry._factories.copy(),
        "_metadata": ConnectorRegistry._metadata.copy(),
        "_capabilities": ConnectorRegistry._capabilities.copy(),
        "_uri_builders": ConnectorRegistry._uri_builders.copy(),
        "_context_resolvers": ConnectorRegistry._context_resolvers.copy(),
        "_initialized": ConnectorRegistry._initialized,
    }
    yield
    ConnectorRegistry._connectors = saved["_connectors"]
    ConnectorRegistry._factories = saved["_factories"]
    ConnectorRegistry._metadata = saved["_metadata"]
    ConnectorRegistry._capabilities = saved["_capabilities"]
    ConnectorRegistry._uri_builders = saved["_uri_builders"]
    ConnectorRegistry._context_resolvers = saved["_context_resolvers"]
    ConnectorRegistry._initialized = saved["_initialized"]


class TestResolveKey:
    def test_lowercase(self):
        assert ConnectorRegistry._resolve_key("MySQL") == "mysql"
        assert ConnectorRegistry._resolve_key("SNOWFLAKE") == "snowflake"

    def test_alias_postgres(self):
        assert ConnectorRegistry._resolve_key("postgres") == "postgresql"
        assert ConnectorRegistry._resolve_key("Postgres") == "postgresql"

    def test_alias_sqlserver(self):
        assert ConnectorRegistry._resolve_key("sqlserver") == "mssql"

    def test_no_alias(self):
        assert ConnectorRegistry._resolve_key("clickhouse") == "clickhouse"


class TestRegister:
    def test_register_basic(self):
        ConnectorRegistry.register("testdb", DummyConnector)
        assert ConnectorRegistry.is_registered("testdb")
        assert ConnectorRegistry._connectors["testdb"] is DummyConnector

    def test_register_with_factory(self):
        factory = MagicMock()
        ConnectorRegistry.register("testdb", DummyConnector, factory=factory)
        assert ConnectorRegistry._factories["testdb"] is factory

    def test_register_with_capabilities(self):
        ConnectorRegistry.register("testdb", DummyConnector, capabilities={"catalog", "database", "schema"})
        assert ConnectorRegistry.support_catalog("testdb")
        assert ConnectorRegistry.support_database("testdb")
        assert ConnectorRegistry.support_schema("testdb")

    def test_register_with_config_class(self):
        ConnectorRegistry.register("testdb", DummyConnector, config_class=DummyConfig)
        meta = ConnectorRegistry.get_metadata("testdb")
        assert meta is not None
        assert meta.config_class is DummyConfig

    def test_register_with_display_name(self):
        ConnectorRegistry.register("testdb", DummyConnector, display_name="Test Database")
        meta = ConnectorRegistry.get_metadata("testdb")
        assert meta.display_name == "Test Database"

    def test_register_alias_resolves(self):
        ConnectorRegistry.register("postgres", DummyConnector)
        assert ConnectorRegistry.is_registered("postgresql")
        assert ConnectorRegistry.is_registered("postgres")

    def test_register_with_uri_builder(self):
        builder = MagicMock()
        ConnectorRegistry.register("testdb", DummyConnector, uri_builder=builder)
        assert ConnectorRegistry.get_uri_builder("testdb") is builder

    def test_register_with_context_resolver(self):
        resolver = MagicMock()
        ConnectorRegistry.register("testdb", DummyConnector, context_resolver=resolver)
        assert ConnectorRegistry.get_context_resolver("testdb") is resolver

    def test_register_with_optional_hooks(self):
        identifier_parser = MagicMock()
        notes = MagicMock()
        ConnectorRegistry.register(
            "testdb",
            DummyConnector,
            parser_dialect="hive",
            identifier_parser=identifier_parser,
            sql_generation_notes=notes,
        )
        assert ConnectorRegistry.get_parser_dialect("testdb") == "hive"
        assert ConnectorRegistry.get_identifier_parser("testdb") is identifier_parser
        assert ConnectorRegistry.get_sql_generation_notes("testdb") is notes


class TestCreateConnector:
    def test_create_with_class(self):
        ConnectorRegistry.register("testdb", DummyConnector)
        config = DummyConfig()
        connector = ConnectorRegistry.create_connector("testdb", config)
        assert isinstance(connector, DummyConnector)
        assert connector.config is config

    def test_create_with_factory(self):
        factory = MagicMock(return_value="mock_connector")
        ConnectorRegistry.register("testdb", DummyConnector, factory=factory)
        config = DummyConfig()
        result = ConnectorRegistry.create_connector("testdb", config)
        factory.assert_called_once_with(config)
        assert result == "mock_connector"

    def test_create_not_registered_raises(self):
        with pytest.raises(DatusDbException) as exc_info:
            ConnectorRegistry.create_connector("nonexistent", {})
        assert "not found" in str(exc_info.value).lower()

    def test_create_with_alias(self):
        ConnectorRegistry.register("postgres", DummyConnector)
        config = DummyConfig()
        connector = ConnectorRegistry.create_connector("postgres", config)
        assert isinstance(connector, DummyConnector)

    @patch.object(ConnectorRegistry, "_try_load_adapter")
    def test_create_triggers_lazy_load(self, mock_load):
        with pytest.raises(DatusDbException):
            ConnectorRegistry.create_connector("lazydb", {})
        mock_load.assert_called_once_with("lazydb")


class TestTryLoadAdapter:
    @patch("importlib.import_module")
    def test_loads_module_with_register(self, mock_import):
        mock_module = MagicMock()
        mock_module.register = MagicMock()
        mock_import.return_value = mock_module
        ConnectorRegistry._try_load_adapter("mydb")
        mock_import.assert_called_once_with("datus_mydb")
        mock_module.register.assert_called_once()

    @patch("importlib.import_module")
    def test_handles_import_error(self, mock_import):
        err = ImportError("No module")
        err.name = "datus_missing"
        mock_import.side_effect = err
        ConnectorRegistry._try_load_adapter("missing")  # Should not raise

    @patch("importlib.import_module")
    def test_nested_import_error_propagates(self, mock_import):
        err = ImportError("No module named 'some_dependency'")
        err.name = "some_dependency"
        mock_import.side_effect = err
        with pytest.raises(ImportError):
            ConnectorRegistry._try_load_adapter("broken_adapter")

    @patch("importlib.import_module")
    def test_handles_generic_error(self, mock_import):
        mock_import.side_effect = RuntimeError("Something bad")
        ConnectorRegistry._try_load_adapter("broken")  # Should not raise


class TestDiscoverAdapters:
    def test_discover_sets_initialized(self):
        ConnectorRegistry._initialized = False
        with patch("importlib.metadata.entry_points", return_value=[]):
            ConnectorRegistry.discover_adapters()
        assert ConnectorRegistry._initialized is True

    def test_discover_only_runs_once(self):
        ConnectorRegistry._initialized = True
        ConnectorRegistry.discover_adapters()  # Should return immediately

    def test_discover_loads_entry_points(self):
        ConnectorRegistry._initialized = False
        mock_ep = MagicMock()
        mock_ep.name = "testdb"
        mock_ep.load.return_value = MagicMock()
        with patch("importlib.metadata.entry_points", return_value=[mock_ep]):
            ConnectorRegistry.discover_adapters()
        mock_ep.load.assert_called_once()

    def test_discover_handles_entry_point_failure(self):
        ConnectorRegistry._initialized = False
        mock_ep = MagicMock()
        mock_ep.name = "broken"
        mock_ep.load.side_effect = RuntimeError("fail")
        with patch("importlib.metadata.entry_points", return_value=[mock_ep]):
            ConnectorRegistry.discover_adapters()  # Should not raise


class TestCapabilities:
    def test_no_capabilities_returns_false(self):
        ConnectorRegistry.register("testdb", DummyConnector)
        assert not ConnectorRegistry.support_catalog("testdb")
        assert not ConnectorRegistry.support_database("testdb")
        assert not ConnectorRegistry.support_schema("testdb")

    def test_partial_capabilities(self):
        ConnectorRegistry.register("testdb", DummyConnector, capabilities={"database"})
        assert not ConnectorRegistry.support_catalog("testdb")
        assert ConnectorRegistry.support_database("testdb")
        assert not ConnectorRegistry.support_schema("testdb")

    def test_register_handlers_updates_capabilities(self):
        ConnectorRegistry.register("testdb", DummyConnector)
        ConnectorRegistry.register_handlers("testdb", capabilities={"catalog", "schema"})
        assert ConnectorRegistry.support_catalog("testdb")
        assert ConnectorRegistry.support_schema("testdb")

    def test_capabilities_returns_copy(self):
        ConnectorRegistry.register("testdb", DummyConnector, capabilities={"database"})
        capabilities = ConnectorRegistry.get_capabilities("testdb")
        capabilities.add("schema")
        assert ConnectorRegistry.get_capabilities("testdb") == {"database"}

    def test_unregistered_db_no_capabilities(self):
        assert not ConnectorRegistry.support_catalog("unknown_db")
        assert not ConnectorRegistry.support_database("unknown_db")
        assert not ConnectorRegistry.support_schema("unknown_db")


class TestListAndQuery:
    def test_list_connectors(self):
        ConnectorRegistry.register("testdb", DummyConnector)
        connectors = ConnectorRegistry.list_connectors()
        assert "testdb" in connectors
        assert connectors["testdb"] is DummyConnector

    def test_list_connectors_returns_copy(self):
        ConnectorRegistry.register("testdb", DummyConnector)
        connectors = ConnectorRegistry.list_connectors()
        connectors["injected"] = "bad"
        assert "injected" not in ConnectorRegistry._connectors

    def test_is_registered_true(self):
        ConnectorRegistry.register("testdb", DummyConnector)
        assert ConnectorRegistry.is_registered("testdb")

    def test_is_registered_false(self):
        assert not ConnectorRegistry.is_registered("nonexistent")

    def test_is_registered_with_alias(self):
        ConnectorRegistry.register("postgres", DummyConnector)
        assert ConnectorRegistry.is_registered("postgres")
        assert ConnectorRegistry.is_registered("postgresql")

    def test_get_metadata_exists(self):
        ConnectorRegistry.register("testdb", DummyConnector, display_name="Test DB")
        meta = ConnectorRegistry.get_metadata("testdb")
        assert meta is not None
        assert meta.db_type == "testdb"
        assert meta.display_name == "Test DB"

    def test_get_metadata_missing(self):
        assert ConnectorRegistry.get_metadata("nonexistent") is None

    def test_get_uri_builder_missing(self):
        assert ConnectorRegistry.get_uri_builder("nonexistent") is None

    def test_get_context_resolver_missing(self):
        assert ConnectorRegistry.get_context_resolver("nonexistent") is None

    def test_optional_hooks_missing(self):
        assert ConnectorRegistry.get_parser_dialect("nonexistent") is None
        assert ConnectorRegistry.get_identifier_parser("nonexistent") is None
        assert ConnectorRegistry.get_sql_generation_notes("nonexistent") is None


class TestAdapterMetadata:
    def test_default_display_name(self):
        meta = AdapterMetadata(db_type="mysql", connector_class=DummyConnector)
        assert meta.display_name == "Mysql"

    def test_custom_display_name(self):
        meta = AdapterMetadata(db_type="mysql", connector_class=DummyConnector, display_name="MySQL")
        assert meta.display_name == "MySQL"

    def test_get_config_fields_with_pydantic(self):
        meta = AdapterMetadata(db_type="test", connector_class=DummyConnector, config_class=DummyConfig)
        fields = meta.get_config_fields()
        assert "host" in fields
        assert "timeout_seconds" in fields

    def test_get_config_fields_without_config(self):
        meta = AdapterMetadata(db_type="test", connector_class=DummyConnector)
        assert meta.get_config_fields() == {}

    def test_get_config_fields_non_pydantic(self):
        class PlainConfig:
            pass

        meta = AdapterMetadata(db_type="test", connector_class=DummyConnector, config_class=PlainConfig)
        assert meta.get_config_fields() == {}
