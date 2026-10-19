from importlib.metadata import entry_points
from types import SimpleNamespace

import pytest
from sqlalchemy.engine import make_url

from datus_db_core import connector_registry
from datus_trino import TrinoConfig, TrinoConnector, register
from datus_trino.handlers import build_trino_uri, connection_url, parse_trino_identifier, resolve_trino_context


@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("port", [None, "19443"])
def test_context_uri_matches_execution(scheme, port):
    config = SimpleNamespace(
        host="trino.example.com",
        port=port,
        username="u+中文",
        password="private-password",
        catalog="hive+catalog",
        extra={"http_scheme": scheme, "schema_name": "analytics/schema", "ca_cert": "/private/ca.pem"},
    )
    uri = build_trino_uri(config)
    parsed = make_url(uri)
    execution = TrinoConfig(
        host=config.host,
        port=int(port) if port else 8080,
        username=config.username,
        password=config.password,
        catalog=config.catalog,
        schema_name="analytics/schema",
        http_scheme=scheme,
    )
    assert uri == str(connection_url(execution))
    assert parsed.port == (int(port) if port else 8080)
    assert parsed.query == {"http_scheme": scheme}
    assert "private-password" not in uri
    assert "ca.pem" not in uri
    assert resolve_trino_context(config, uri) == ("trino", "hive+catalog", "analytics/schema", "analytics/schema")


def test_registration_and_entrypoint(monkeypatch):
    for name in ("_connectors", "_factories", "_metadata", "_capabilities", "_uri_builders", "_context_resolvers"):
        monkeypatch.setattr(type(connector_registry), name, getattr(connector_registry, name).copy())
    register()
    assert connector_registry.get_uri_builder("trino") is build_trino_uri
    assert connector_registry.get_context_resolver("trino") is resolve_trino_context
    assert connector_registry.get_identifier_parser("trino") is parse_trino_identifier
    metadata = connector_registry.get_metadata("trino")
    assert metadata.config_class is TrinoConfig
    assert metadata.connector_class is TrinoConnector
    points = list(entry_points(group="datus.adapters", name="trino"))
    assert len(points) == 1
    assert points[0].load() is register


def test_context_uri_rejects_unknown_scheme():
    config = SimpleNamespace(extra={"http_scheme": "native"})
    with pytest.raises(ValueError, match="http_scheme"):
        connection_url(config)


@pytest.mark.parametrize(
    "value,catalog,schema,table",
    [
        ("", "", "", ""),
        ("   ", "", "", ""),
        ("orders", "", "", "orders"),
        ("analytics.orders", "", "analytics", "orders"),
        ("hive.analytics.orders", "hive", "analytics", "orders"),
        (' "My.Catalog" . "Mixed.Schema" . "a""b" ', "My.Catalog", "Mixed.Schema", 'a"b'),
        ("..orders", "", "", "orders"),
        (".analytics.orders", "", "analytics", "orders"),
        ("hive.analytics.*", "hive", "analytics", "*"),
        ("_catalog.数据.订单", "_catalog", "数据", "订单"),
    ],
)
def test_identifier(value, catalog, schema, table):
    assert parse_trino_identifier(value) == {
        "catalog_name": catalog,
        "database_name": "",
        "schema_name": schema,
        "table_name": table,
    }


@pytest.mark.parametrize(
    "value",
    [
        "a.b.c.d",
        "a..b",
        "a.",
        ".",
        "..",
        "...t",
        ".a.b.c",
        "t alias",
        '"unterminated',
        '""',
        "`backtick`",
        "[bracket]",
        "table; SELECT 1",
        "a.(b)",
    ],
)
def test_invalid_identifier(value):
    with pytest.raises(ValueError):
        parse_trino_identifier(value)
