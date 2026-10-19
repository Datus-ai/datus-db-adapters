# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest
from pydantic import ValidationError

from datus_hive import HiveConfig


def test_config_defaults_and_required_fields():
    config = HiveConfig(username="hue")

    assert config.model_dump() == {
        "host": "127.0.0.1",
        "port": 10000,
        "database": None,
        "username": "hue",
        "password": "",
        "auth": None,
        "kerberos_service_name": None,
        "configuration": {},
        "timeout_seconds": 30,
    }


def test_config_custom_values():
    config = HiveConfig(
        host="10.0.0.10",
        port=10009,
        database="test",
        username="hue",
        password="secret",
        auth="CUSTOM",
        configuration={"spark.app.name": "app"},
        timeout_seconds=60,
    )

    assert config.host == "10.0.0.10"
    assert config.port == 10009
    assert config.database == "test"
    assert config.username == "hue"
    assert config.password == "secret"
    assert config.auth == "CUSTOM"
    assert config.configuration == {"spark.app.name": "app"}
    assert config.timeout_seconds == 60


def test_config_requires_username():
    with pytest.raises(ValidationError):
        HiveConfig()


def test_config_forbids_extra_fields():
    with pytest.raises(ValidationError):
        HiveConfig(username="hue", extra_field="not_allowed")


def test_config_from_carrier_map_prefix():
    carrier_map = {
        "my_hive.host": "10.85.24.173",
        "my_hive.port": "10009",
        "my_hive.database": "test",
        "my_hive.username": "hue",
        "my_hive.password": "pass",
        "my_hive.auth": "CUSTOM",
        "my_hive.configuration.spark.app.name": "datacenter_carrier",
        "my_hive.configuration.spark.executor.instances": "1",
    }

    config = HiveConfig.from_config_map(carrier_map, prefix="my_hive.")

    assert config.host == "10.85.24.173"
    assert config.port == 10009
    assert config.database == "test"
    assert config.username == "hue"
    assert config.password == "pass"
    assert config.auth == "CUSTOM"
    assert config.configuration == {
        "spark.app.name": "datacenter_carrier",
        "spark.executor.instances": "1",
    }


def test_kerberos_from_carrier_map():
    config = HiveConfig.from_config_map(
        {
            "hive.username": "alice",
            "hive.auth": "KERBEROS",
            "hive.kerberos_service_name": "hive",
        },
        prefix="hive.",
    )
    assert config.auth == "KERBEROS"
    assert config.kerberos_service_name == "hive"
    assert config.password == ""


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"auth": "KERBEROS"}, "kerberos_service_name is required"),
        ({"auth": "KERBEROS", "kerberos_service_name": ""}, "kerberos_service_name is required"),
        ({"auth": "KERBEROS", "kerberos_service_name": "  "}, "kerberos_service_name is required"),
        (
            {"auth": "KERBEROS", "kerberos_service_name": "hive", "password": "secret"},
            "KERBEROS uses a ticket cache",
        ),
        ({"kerberos_service_name": "hive"}, "kerberos_service_name requires auth=KERBEROS"),
        ({"auth": "LDAP", "kerberos_service_name": "hive"}, "kerberos_service_name requires auth=KERBEROS"),
    ],
)
def test_kerberos_config_rejections(kwargs, message):
    with pytest.raises(ValidationError, match=message):
        HiveConfig(username="alice", **kwargs)
