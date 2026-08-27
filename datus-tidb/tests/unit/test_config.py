# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest
from pydantic import ValidationError

from datus_tidb import TiDBConfig


@pytest.mark.acceptance
def test_defaults_use_the_tidb_port_not_the_mysql_one():
    """TiDB's own binary defaults to 4000 (`tidb-server -P`); 3306 would reach a
    different server entirely."""
    config = TiDBConfig(username="root")

    assert config.port == 4000
    assert config.host == "127.0.0.1"
    assert config.database is None
    assert config.charset == "utf8mb4"
    assert config.autocommit is True
    assert config.timeout_seconds == 30


def test_username_is_required():
    with pytest.raises(ValidationError):
        TiDBConfig()


def test_explicit_values_override_defaults():
    config = TiDBConfig(
        host="tidb.internal",
        port=4001,
        username="analyst",
        password="secret",
        database="analytics",
        charset="utf8",
        autocommit=False,
        timeout_seconds=5,
    )

    assert (config.host, config.port, config.database) == ("tidb.internal", 4001, "analytics")
    assert (config.username, config.password) == ("analyst", "secret")
    assert (config.charset, config.autocommit, config.timeout_seconds) == ("utf8", False, 5)


def test_password_is_marked_as_a_password_input():
    field = TiDBConfig.model_fields["password"]

    assert field.json_schema_extra == {"input_type": "password"}


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        TiDBConfig(username="root", unknown_field="value")


def test_tls_keys_are_rejected_until_the_engine_supports_them():
    """The dosi MySQL-wire executor TiDB shares has no TLS, so accepting these
    here would let a datasource connect for metadata and fail at query time."""
    for key in ("sslmode", "ssl_ca", "ssl_verify_cert"):
        with pytest.raises(ValidationError):
            TiDBConfig(username="root", **{key: "value"})
