import pytest

from datus_databricks import DatabricksConfig
from datus_databricks.handlers import (
    build_databricks_uri,
    parse_databricks_identifier,
    resolve_databricks_context,
)


def _config(**overrides):
    values = {
        "host": "adb-1.azuredatabricks.net",
        "warehouse_id": "abc",
        "catalog": "main",
        "schema": "analytics",
        "profile": "dev",
    }
    values.update(overrides)
    return DatabricksConfig(**values)


def test_uri_omits_credentials():
    config = _config(auth_mode="access_token", profile=None, access_token="dapi-secret")

    uri = build_databricks_uri(config)

    assert uri.startswith("databricks://adb-1.azuredatabricks.net/main/analytics?")
    assert "http_path=%2Fsql%2F1.0%2Fwarehouses%2Fabc" in uri
    assert "auth_mode=access_token" in uri
    assert "dapi-secret" not in uri
    assert "access_token=" not in uri
    assert resolve_databricks_context(config, uri) == ("databricks", "main", "analytics", "")


def test_uri_requires_a_warehouse():
    config = _config()
    object.__setattr__(config, "warehouse_id", None)
    object.__setattr__(config, "http_path", None)

    with pytest.raises(ValueError, match="warehouse_id or http_path"):
        build_databricks_uri(config)


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        ("orders", {"catalog_name": "", "database_name": "", "schema_name": "", "table_name": "orders"}),
        (
            "analytics.orders",
            {"catalog_name": "", "database_name": "analytics", "schema_name": "", "table_name": "orders"},
        ),
        (
            "`main`.`analytics`.`orders`",
            {"catalog_name": "main", "database_name": "analytics", "schema_name": "", "table_name": "orders"},
        ),
    ],
)
def test_parse_identifier(identifier, expected):
    assert parse_databricks_identifier(identifier) == expected


def test_parse_identifier_rejects_too_many_parts():
    with pytest.raises(ValueError, match="Invalid Databricks"):
        parse_databricks_identifier("a.b.c.d")
