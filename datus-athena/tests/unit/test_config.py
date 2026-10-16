import pytest
from pydantic import ValidationError

from datus_athena import AthenaConfig


def test_defaults():
    assert AthenaConfig().model_dump() == dict(
        region=None,
        profile_name=None,
        ca_bundle=None,
        workgroup="primary",
        catalog="AwsDataCatalog",
        database=None,
        output_location=None,
        timeout_seconds=30,
        query_timeout_seconds=600,
        result_timeout_seconds=60,
        cancel_timeout_seconds=10,
        poll_interval_seconds=1,
        max_rows=10000,
        max_result_bytes=16777216,
        page_size=1000,
    )


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"region": 1}, "region"),
        ({"workgroup": " "}, "blank"),
        ({"catalog": ""}, "catalog"),
        ({"password": "secret"}, "extra_forbidden"),
        ({"max_rows": 0}, "max_rows"),
        ({"max_result_bytes": -1}, "max_result_bytes"),
        ({"page_size": 1001}, "page_size"),
        ({"timeout_seconds": 0}, "timeout_seconds"),
        ({"query_timeout_seconds": -1}, "query_timeout_seconds"),
        ({"poll_interval_seconds": 0}, "poll_interval_seconds"),
        ({"cancel_timeout_seconds": -1}, "cancel_timeout_seconds"),
        ({"output_location": "https://bucket/path"}, "s3://"),
        ({"output_location": "s3://"}, "s3://"),
        ({"output_location": "s3://user:pass@bucket/"}, "credentials"),
        ({"output_location": "s3://bucket/?token=secret"}, "query"),
    ],
)
def test_rejections(kwargs, match):
    with pytest.raises(ValidationError, match=match):
        AthenaConfig(**kwargs)


def test_string_normalization():
    config = AthenaConfig(
        region=" us-east-1 ",
        profile_name=" role ",
        workgroup=" wg ",
        database=" ",
        output_location=" s3://bucket/results/ ",
    )
    assert (config.region, config.profile_name, config.workgroup, config.database, config.output_location) == (
        "us-east-1",
        "role",
        "wg",
        None,
        "s3://bucket/results/",
    )


def test_shared_aws_identity_configuration():
    config = AthenaConfig(aws={"region": "us-east-1", "profile": "cross-account", "ca_bundle": "/ca.pem"})
    assert (config.region, config.profile_name, config.ca_bundle) == ("us-east-1", "cross-account", "/ca.pem")
    assert AthenaConfig(region="us-east-1", aws={"region": "us-east-1"}).region == "us-east-1"


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"aws": {"profile": "a"}, "profile_name": "b"}, "Conflicting"),
        ({"aws": {"access_key": "secret"}}, "only region"),
        ({"aws": "role"}, "mapping"),
        ({"query_timeout_seconds": float("inf")}, "finite"),
    ],
)
def test_shared_identity_rejections(kwargs, match):
    with pytest.raises(ValidationError, match=match):
        AthenaConfig(**kwargs)
