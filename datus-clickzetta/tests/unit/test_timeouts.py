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


@pytest.mark.parametrize(
    "options, message",
    [
        ({"timeout_seconds": 0}, "positive integer seconds"),
        ({"timeout_seconds": True}, "positive integer seconds"),
        ({"query_timeout_seconds": -1}, "positive integer seconds"),
        ({"extra": {"http_read_timeout": 0}}, "http_read_timeout must be positive"),
        ({"extra": {"http_connect_timeout": -1}}, "http_connect_timeout must be positive"),
        ({"hints": {"sdk.job.timeout": 0}}, "sdk.job.timeout must be positive"),
        ({"extra": {"hints": {"sdk.job.polling.timeout": 0}}}, "sdk.job.polling.timeout must be positive"),
    ],
)
def test_constructor_rejects_values_that_disable_timeouts(options, message):
    with pytest.raises(ValueError, match=message):
        ClickZettaConnector(service="test", username="u", password="p", instance="i", workspace="w", **options)


@pytest.mark.parametrize("key", ["sdk.job.timeout", "sdk.job.polling.timeout", "sdk.query.timeout.ms", "querytimeout"])
@pytest.mark.parametrize("value", [0.5, float("nan"), float("inf"), True, "0.5"])
@pytest.mark.parametrize("carrier", ["hints", "extra"])
def test_sdk_timeout_hint_cannot_truncate_to_zero(key, value, carrier):
    options = {"hints": {key: value}} if carrier == "hints" else {"extra": {"hints": {key: value}}}
    with pytest.raises(ValueError, match="positive integer"):
        ClickZettaConnector(service="test", username="u", password="p", instance="i", workspace="w", **options)


@pytest.mark.parametrize(
    "key, raw, milliseconds",
    [("sdk.job.timeout", "2", 2000), ("sdk.query.timeout.ms", 1500.5, 1500), ("querytimeout", 1, 1)],
)
def test_normalized_hint_reaches_real_sdk_job_request(key, raw, milliseconds):
    import json

    from clickzetta.connector.v0.client import Client
    from clickzetta.connector.v0.enums import JobID

    connector = ClickZettaConnector(
        service="test.invalid", username="u", password="p", instance="i", workspace="w", hints={key: raw}
    )
    sdk = Client(**dict(connector._connection_config, magic_token="test-token"))
    try:
        with patch("clickzetta.connector.v0.client.execute_with_retrying", return_value="submitted") as submit:
            assert (
                sdk.submit_sql_job("token", "SELECT 1", JobID("job", "w", 1), processed_sql="SELECT 1") == "submitted"
            )
        request = json.loads(submit.call_args.args[0].keywords["data"])
        assert request["jobDesc"]["jobTimeoutMs"] == milliseconds
        assert request["jobDesc"]["hybridPollingTimeout"] > 0
        assert isinstance(connector._connection_config["hints"][key], int)
    finally:
        sdk.close()
        connector.close()
