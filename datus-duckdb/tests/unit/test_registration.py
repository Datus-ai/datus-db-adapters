from importlib.metadata import entry_points

from jsonschema import Draft202012Validator

from datus_db_core import connector_registry
from datus_duckdb import DuckDBConfig, DuckdbConnector, register


def test_registered_model_is_the_constructor_model():
    attributes = ("_connectors", "_metadata", "_capabilities")
    saved = {name: dict(getattr(connector_registry, name)) for name in attributes}
    try:
        register()
        metadata = connector_registry.get_metadata("duckdb")
        assert metadata.connector_class is DuckdbConnector
        assert metadata.config_class is DuckDBConfig
        assert connector_registry.get_capabilities("duckdb") == {"database", "schema"}
        schema = connector_registry.get_config_schema("duckdb")
        validator = Draft202012Validator(schema)
        assert validator.is_valid({"uri": ":memory:"})
        assert validator.is_valid({"db_path": ":memory:"})
        assert not validator.is_valid({})
        assert not validator.is_valid({"uri": "a", "db_path": "b"})
        assert schema["additionalProperties"] is False
        config = connector_registry.validate_config("duckdb", {"uri": ":memory:"})
        connector = connector_registry.create_connector("duckdb", config)
        assert connector.config is config
        connector.close()
        matches = [ep for ep in entry_points(group="datus.adapters") if ep.name == "duckdb"]
        assert len(matches) == 1
        assert matches[0].load() is register
    finally:
        for name, value in saved.items():
            setattr(connector_registry, name, value)
