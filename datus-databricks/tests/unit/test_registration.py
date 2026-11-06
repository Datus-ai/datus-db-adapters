from datus_databricks import DatabricksConfig, DatabricksConnector, register
from datus_databricks.handlers import (
    build_databricks_uri,
    parse_databricks_identifier,
    resolve_databricks_context,
)
from datus_db_core import connector_registry


def test_registration_exposes_the_adapter_contract():
    registry_names = (
        "connectors",
        "factories",
        "metadata",
        "capabilities",
        "uri_builders",
        "context_resolvers",
    )
    saved = {name: getattr(connector_registry, f"_{name}").copy() for name in registry_names}
    try:
        register()

        metadata = connector_registry.get_metadata("databricks")
        assert metadata.connector_class is DatabricksConnector
        assert metadata.config_class is DatabricksConfig
        assert metadata.display_name == "Databricks SQL"
        assert metadata.parser_dialect == "databricks"
        assert metadata.get_config_fields()["access_token"]["input_type"] == "password"
        assert metadata.get_config_fields()["access_token"]["required"] is False
        assert connector_registry.get_capabilities("databricks") == {"catalog", "database"}
        assert connector_registry.get_uri_builder("databricks") is build_databricks_uri
        assert connector_registry.get_context_resolver("databricks") is resolve_databricks_context
        assert connector_registry.get_identifier_parser("databricks") is parse_databricks_identifier
    finally:
        for name, values in saved.items():
            target = getattr(connector_registry, f"_{name}")
            target.clear()
            target.update(values)
