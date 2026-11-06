import pytest
from pydantic import ValidationError

from datus_databricks import DatabricksConfig


def test_config_defaults():
    config = DatabricksConfig(
        host=" https://adb-1.19.azuredatabricks.net:443/ ",
        warehouse_id=" abc ",
        catalog=" main ",
        schema=" analytics ",
        profile=" dev ",
    )

    assert config.model_dump() == {
        "host": "adb-1.19.azuredatabricks.net",
        "warehouse_id": "abc",
        "http_path": None,
        "catalog": "main",
        "schema": "analytics",
        "auth_mode": "oauth_profile",
        "profile": "dev",
        "access_token": None,
        "timeout_seconds": 120,
    }
    assert config.resolved_http_path() == "/sql/1.0/warehouses/abc"


def test_database_alias_overrides_schema():
    config = DatabricksConfig(
        host="adb-1.azuredatabricks.net",
        warehouse_id="abc",
        catalog="main",
        schema="original",
        database="request_schema",
        profile="dev",
    )

    assert config.schema_name == "request_schema"


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"warehouse_id": "abc", "catalog": "main", "schema": "analytics", "profile": "dev"}, "host"),
        (
            {"host": "adb", "catalog": "main", "schema": "analytics", "profile": "dev"},
            "warehouse_id or http_path",
        ),
        ({"host": "adb", "warehouse_id": "abc", "schema": "analytics", "profile": "dev"}, "catalog"),
        ({"host": "adb", "warehouse_id": "abc", "catalog": "main", "profile": "dev"}, "schema"),
        (
            {"host": "adb", "warehouse_id": "abc", "catalog": "main", "schema": "analytics"},
            "profile is required",
        ),
        (
            {
                "host": "adb",
                "warehouse_id": "abc",
                "catalog": "main",
                "schema": "analytics",
                "auth_mode": "access_token",
            },
            "access_token is required",
        ),
        (
            {
                "host": "adb",
                "warehouse_id": "abc",
                "catalog": "main",
                "schema": "analytics",
                "profile": "dev",
                "access_token": "dapi-secret",
            },
            "only one of profile or access_token",
        ),
        (
            {
                "host": "adb",
                "warehouse_id": "abc",
                "http_path": "/sql/1.0/warehouses/other",
                "catalog": "main",
                "schema": "analytics",
                "profile": "dev",
            },
            "does not match",
        ),
        (
            {
                "host": "adb",
                "http_path": "sql/1.0/warehouses/abc",
                "catalog": "main",
                "schema": "analytics",
                "profile": "dev",
            },
            "http_path must start with /",
        ),
        (
            {
                "host": "adb",
                "warehouse_id": "abc",
                "catalog": "main",
                "schema": "analytics",
                "profile": "dev",
                "timeout_seconds": 0,
            },
            "greater than 0",
        ),
        (
            {
                "host": "adb",
                "warehouse_id": "abc",
                "catalog": "main",
                "schema": "analytics",
                "profile": "dev",
                "extra": "nope",
            },
            "extra",
        ),
    ],
)
def test_config_rejects_invalid_values(kwargs, match):
    with pytest.raises(ValidationError, match=match):
        DatabricksConfig(**kwargs)


def test_access_token_is_secret():
    config = DatabricksConfig(
        host="adb-1.azuredatabricks.net",
        http_path="/sql/protocolv1/o/1/2",
        catalog="main",
        schema="analytics",
        auth_mode="access_token",
        access_token="dapi-secret",
    )

    assert config.access_token.get_secret_value() == "dapi-secret"
    assert "dapi-secret" not in repr(config)
    assert "dapi-secret" not in str(config.model_dump())
