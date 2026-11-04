import pytest
from pydantic import ValidationError

from datus_duckdb import DuckDBConfig


def test_defaults_and_path_alias():
    cfg = DuckDBConfig(uri="duckdb:////tmp/retail.duckdb")
    assert cfg.model_dump() == {
        "timeout_seconds": 30,
        "uri": "duckdb:////tmp/retail.duckdb",
        "read_only": False,
        "enable_external_access": True,
        "memory_limit": None,
        "database_name": None,
        "iceberg": None,
    }
    assert cfg.db_path == "/tmp/retail.duckdb"
    assert DuckDBConfig(db_path=":memory:").uri == ":memory:"


@pytest.mark.parametrize(
    "values, error",
    [
        ({}, "missing"),
        ({"uri": 12}, "string_type"),
        ({"uri": "x", "read_only": "invalid"}, "bool_parsing"),
        ({"uri": "x", "unknown": 1}, "extra_forbidden"),
        ({"uri": "x", "db_path": "y"}, "extra_forbidden"),
    ],
)
def test_invalid_config(values, error):
    with pytest.raises(ValidationError) as caught:
        DuckDBConfig.model_validate(values)
    assert caught.value.errors()[0]["type"] == error


@pytest.mark.parametrize(
    "values, valid",
    [
        ({"uri": ":memory:"}, True),
        ({"db_path": "data.db"}, True),
        ({"uri": "x", "db_path": "x"}, False),
        ({}, False),
        ({"db_path": None}, False),
        ({"uri": "x", "unknown": 1}, False),
    ],
)
def test_exported_input_aliases_match_real_config(values, valid):
    from jsonschema import Draft202012Validator

    from datus_db_core.registry import AdapterMetadata

    meta = AdapterMetadata("duckdb", object, config_class=DuckDBConfig)
    schema = meta.get_config_schema()
    Draft202012Validator.check_schema(schema)
    assert Draft202012Validator(schema).is_valid(values) is valid
    if valid:
        assert meta.validate_config(values).uri == values.get("uri", values.get("db_path"))
    else:
        with pytest.raises(ValidationError):
            meta.validate_config(values)
