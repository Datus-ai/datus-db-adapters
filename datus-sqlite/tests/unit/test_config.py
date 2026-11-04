import pytest
from pydantic import ValidationError

from datus_sqlite import SQLiteConfig


def test_defaults_and_alias():
    config = SQLiteConfig(db_path="sqlite:////tmp/data.db")
    assert config.model_dump() == {
        "timeout_seconds": 30,
        "uri": "sqlite:////tmp/data.db",
        "read_only": False,
        "check_same_thread": False,
        "database_name": None,
    }
    assert config.db_path == "/tmp/data.db"


@pytest.mark.parametrize(
    "values,kind",
    [
        ({}, "missing"),
        ({"uri": 1}, "string_type"),
        ({"uri": "x", "unknown": True}, "extra_forbidden"),
        ({"uri": "x", "db_path": "y"}, "extra_forbidden"),
    ],
)
def test_invalid(values, kind):
    with pytest.raises(ValidationError) as caught:
        SQLiteConfig.model_validate(values)
    assert caught.value.errors()[0]["type"] == kind


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

    meta = AdapterMetadata("sqlite", object, config_class=SQLiteConfig)
    schema = meta.get_config_schema()
    Draft202012Validator.check_schema(schema)
    assert Draft202012Validator(schema).is_valid(values) is valid
    if valid:
        assert meta.validate_config(values).uri == values.get("uri", values.get("db_path"))
    else:
        with pytest.raises(ValidationError):
            meta.validate_config(values)


@pytest.mark.parametrize("key", ["uri", "db_path"])
@pytest.mark.parametrize("value", ["", "sqlite:///"])
def test_empty_normalized_database_path_is_rejected(key, value):
    with pytest.raises(ValidationError, match="database path must not be empty"):
        SQLiteConfig.model_validate({key: value})


@pytest.mark.parametrize("value", [":memory:", "sqlite:///:memory:", "data.db", "sqlite:////tmp/data.db"])
def test_explicit_memory_and_file_paths_remain_supported(value):
    assert SQLiteConfig(uri=value).db_path == value.removeprefix("sqlite:///")
