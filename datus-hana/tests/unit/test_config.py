import pytest
from pydantic import ValidationError

from datus_hana import HanaConfig


def test_config_defaults():
    config = HanaConfig(host="h", username="U", password="secret")
    assert config.model_dump() == {
        "host": "h",
        "port": 39041,
        "username": "U",
        "password": "secret",
        "userkey": None,
        "database": None,
        "schema_name": None,
        "encrypt": True,
        "ssl_validate_certificate": True,
        "ssl_trust_store": None,
        "ssl_host_name_in_certificate": None,
        "timeout_seconds": 30,
        "communication_timeout_seconds": 60,
        "max_result_rows": 10000,
        "max_result_bytes": 16777216,
    }
    assert "secret" not in repr(config)


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"host": "h"},
        {"userkey": "K", "username": "U"},
        {"userkey": "K", "password": "p"},
        {"userkey": "K", "port": 0},
        {"userkey": "K", "port": 65536},
        {"userkey": "K", "max_result_rows": 0},
        {"userkey": "K", "max_result_bytes": -1},
        {"userkey": "K", "timeout_seconds": 0},
        {"userkey": "K", "communication_timeout_seconds": -1},
        {"userkey": "K", "unknown": True},
    ],
)
def test_invalid_config(kwargs):
    with pytest.raises(ValidationError):
        HanaConfig(**kwargs)


def test_userkey_and_schema_alias():
    config = HanaConfig(userkey="KEY", schema="Mixed Schema")
    assert config.host is None
    assert config.schema_name == "Mixed Schema"
