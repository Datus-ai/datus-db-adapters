from datus_bigquery import BigQueryConfig, BigQueryConnector, register
from datus_bigquery.handlers import build_bigquery_uri, parse_bigquery_identifier, resolve_bigquery_context
from datus_db_core import connector_registry


def test_registration_exposes_current_adapter_contract():
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

        metadata = connector_registry.get_metadata("bigquery")
        assert metadata is not None
        assert metadata.connector_class is BigQueryConnector
        assert metadata.config_class is BigQueryConfig
        assert metadata.display_name == "Google BigQuery"
        assert metadata.parser_dialect == "bigquery"
        assert metadata.get_config_fields()["credentials_info"] == {
            "required": False,
            "default": None,
            "description": "Service-account JSON object",
            "type": "Optional",
            "input_type": "password",
            "value_type": "json_object",
        }
        assert connector_registry.get_capabilities("bigquery") == {"catalog", "database"}
        assert connector_registry.get_uri_builder("bigquery") is build_bigquery_uri
        assert connector_registry.get_context_resolver("bigquery") is resolve_bigquery_context
        assert connector_registry.get_identifier_parser("bigquery") is parse_bigquery_identifier
        assert callable(connector_registry.get_sql_generation_notes("bigquery"))
    finally:
        for name, values in saved.items():
            target = getattr(connector_registry, f"_{name}")
            target.clear()
            target.update(values)
