from unittest.mock import patch

import pytest

from datus_clickzetta import ClickZettaConnector


def test_network_and_job_timeout_defaults_reach_sdk():
    with patch("datus_clickzetta.connector.Session") as session:
        connector = ClickZettaConnector(service="test", username="u", password="p", instance="i", workspace="w")
        connector.connect()
    options = session.builder.configs.call_args.args[0]
    assert options.get("http_connect_timeout") == 30
    assert options.get("http_read_timeout") == 30
    assert options["hints"]["sdk.job.timeout"] == 30
    connector.close()


def test_sdk_client_uses_timeout_values_at_http_boundary():
    from clickzetta.connector.v0.client import Client

    with patch("datus_clickzetta.connector.Session") as session:
        connector = ClickZettaConnector(
            service="test.invalid", username="u", password="p", instance="i", workspace="w", timeout_seconds=7
        )
        connector.connect()
    options = dict(session.builder.configs.call_args.args[0])
    options["magic_token"] = "test-token"
    client = Client(**options)
    try:
        assert client.http_timeout == (7, 7)
    finally:
        client.close()
        connector.close()


def test_extra_hints_preserve_deadlines_and_allow_explicit_override():
    connector = ClickZettaConnector(
        service="test",
        username="u",
        password="p",
        instance="i",
        workspace="w",
        timeout_seconds=7,
        extra={"hints": {"sdk.job.timeout": 11, "custom.hint": "value"}},
    )
    assert connector._connection_config["hints"]["sdk.job.timeout"] == 11
    assert connector._connection_config["hints"]["sdk.job.polling.timeout"] == 7
    assert connector._connection_config["hints"]["sdk.query.max.retries"] == 1
    assert connector._connection_config["hints"]["custom.hint"] == "value"


@pytest.mark.parametrize("use_model", [False, True])
def test_registry_factory_forwards_timeout_configuration(use_model):
    from datus_clickzetta import ClickZettaConfig, register

    settings = dict(
        service="test",
        username="u",
        password="p",
        instance="i",
        workspace="w",
        timeout_seconds=7,
        query_timeout_seconds=11,
    )
    config = ClickZettaConfig(**settings) if use_model else settings
    with patch("datus_db_core.connector_registry.register") as registration:
        register()
    factory = registration.call_args.args[2]
    connector = factory(config)
    assert connector.timeout_seconds == 7
    assert connector._connection_config["http_read_timeout"] == 7
    assert connector._connection_config["hints"]["sdk.job.timeout"] == 11
