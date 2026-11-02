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
