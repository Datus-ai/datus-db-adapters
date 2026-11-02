from importlib.metadata import entry_points

from datus_db_core import connector_registry
from datus_sqlite import SQLiteConfig, SQLiteConnector, register


def test_registered_model_is_the_constructor_model():
    attributes = ("_connectors", "_metadata", "_capabilities")
    saved = {name: dict(getattr(connector_registry, name)) for name in attributes}
    try:
        register()
        metadata = connector_registry.get_metadata("sqlite")
        assert metadata.connector_class is SQLiteConnector
        assert metadata.config_class is SQLiteConfig
        assert connector_registry.get_capabilities("sqlite") == {"database"}
        schema = connector_registry.get_config_schema("sqlite")
        assert schema["required"] == ["uri"]
        assert schema["additionalProperties"] is False
        config = connector_registry.validate_config("sqlite", {"uri": ":memory:"})
        connector = connector_registry.create_connector("sqlite", config)
        assert connector.config is config
        connector.close()
        matches = [ep for ep in entry_points(group="datus.adapters") if ep.name == "sqlite"]
        assert len(matches) == 1
        assert matches[0].load() is register
    finally:
        for name, value in saved.items():
            setattr(connector_registry, name, value)
