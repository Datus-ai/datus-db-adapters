from importlib.metadata import entry_points

from datus_athena import AthenaConfig, AthenaConnector, register
from datus_athena.handlers import (
    build_athena_uri,
    get_athena_sql_generation_notes,
    parse_athena_identifier,
    resolve_athena_context,
)
from datus_db_core import connector_registry


def test_registration():
    attrs = ("_connectors", "_factories", "_metadata", "_capabilities", "_uri_builders", "_context_resolvers")
    saved = {name: dict(getattr(connector_registry, name)) for name in attrs}
    try:
        register()
        meta = connector_registry.get_metadata("athena")
        assert meta.connector_class is AthenaConnector
        assert meta.config_class is AthenaConfig
        assert connector_registry.get_capabilities("athena") == {"catalog", "database"}
        assert connector_registry.get_parser_dialect("athena") == "athena"
        assert connector_registry.get_uri_builder("athena") is build_athena_uri
        assert connector_registry.get_context_resolver("athena") is resolve_athena_context
        assert connector_registry.get_identifier_parser("athena") is parse_athena_identifier
        assert connector_registry.get_sql_generation_notes("athena") is get_athena_sql_generation_notes
        assert "LIMIT" in get_athena_sql_generation_notes()
        assert connector_registry.get_dialect_operations("athena") is None
    finally:
        for name, value in saved.items():
            getattr(connector_registry, name).clear()
            getattr(connector_registry, name).update(value)


def test_entry_point():
    points = list(entry_points(group="datus.adapters", name="athena"))
    assert len(points) == 1
    assert points[0].load() is register
