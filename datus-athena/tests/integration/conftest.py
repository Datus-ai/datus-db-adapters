"""Real AWS acceptance, separate from offline SDK tests.

Required environment:
  ATHENA_WORKGROUP: isolated Athena SQL Workgroup with a scan cutoff
  ATHENA_DATABASE: disposable Glue database with prepared tiny TPC-H tables (not required by EXPLAIN)
  AWS_REGION or AWS_DEFAULT_REGION: AWS region
Optional:
  AWS_PROFILE: standard SDK profile, including refreshable AssumeRole profiles
  ATHENA_CATALOG: Glue catalog (default AwsDataCatalog)
  ATHENA_OUTPUT_LOCATION: optional S3 result prefix; omit for Workgroup defaults
  ATHENA_WRITABLE_DATA_LOCATION: dedicated S3 prefix enabling the writable table contract
  ATHENA_EXPECT_MANAGED_RESULTS: true/false, assert the actual result mode
  ATHENA_DENIED_PROFILE: identity without query/data access, for policy acceptance
  ATHENA_EXPECT_FAILURE_SQL: environment-specific KMS/Lake Formation denied query
  ATHENA_FAILURE_PROFILE: optional SDK profile for that denied-data query
  ATHENA_ROLE_PROFILE: SDK AssumeRole profile for the isolated reader identity
  ATHENA_RESTRICTED_PROFILE: query identity without KMS/Lake Formation grants
  ATHENA_KMS_SQL: query over an isolated SSE-KMS encrypted fixture
  ATHENA_LAKEFORMATION_SQL: query over an isolated Lake Formation protected fixture
  ATHENA_RUN_ROLE_EXPIRY_TEST: set to 1 for actual STS expiry (at least 15 minutes)
  ATHENA_RUN_NETWORK_FAULT_TESTS: set to 1 for loopback TLS proxy fault injection

Prepare the fixtures with scripts/init_tpch_data.py before running. Missing
required configuration skips; any failure after configuration is present fails.
"""

import os

import pytest

from datus_athena import AthenaConfig, AthenaConnector

pytestmark = pytest.mark.integration


def required(name):
    value = os.getenv(name)
    if not value:
        pytest.skip(f"{name} is not set")
    return value


@pytest.fixture(scope="session")
def query_config():
    workgroup = required("ATHENA_WORKGROUP")
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
    if not region:
        pytest.skip("AWS_REGION or AWS_DEFAULT_REGION is not set")
    return AthenaConfig(
        region=region,
        workgroup=workgroup,
        database=os.getenv("ATHENA_DATABASE"),
        catalog=os.getenv("ATHENA_CATALOG", "AwsDataCatalog"),
        output_location=os.getenv("ATHENA_OUTPUT_LOCATION"),
    )


@pytest.fixture(scope="session")
def config(query_config):
    return AthenaConfig.model_validate(dict(query_config.model_dump(), database=required("ATHENA_DATABASE")))


@pytest.fixture
def query_connector(query_config):
    with AthenaConnector(query_config) as conn:
        yield conn


@pytest.fixture(scope="session")
def connector(config):
    with AthenaConnector(config) as conn:
        assert conn.test_connection()
        yield conn


@pytest.fixture
def denied_connector(config):
    profile = required("ATHENA_DENIED_PROFILE")
    with AthenaConnector(config.model_copy(update={"profile_name": profile})) as conn:
        yield conn


@pytest.fixture
def failure_sql():
    return required("ATHENA_EXPECT_FAILURE_SQL")


@pytest.fixture
def failure_connector(config):
    profile = os.getenv("ATHENA_FAILURE_PROFILE")
    values = config.model_copy(update={"profile_name": profile}) if profile else config
    with AthenaConnector(values) as conn:
        yield conn


@pytest.fixture
def role_config(config):
    return config.model_copy(update={"profile_name": required("ATHENA_ROLE_PROFILE")})


@pytest.fixture
def restricted_config(config):
    return config.model_copy(update={"profile_name": required("ATHENA_RESTRICTED_PROFILE")})


@pytest.fixture(params=["kms", "lakeformation"])
def protected_query(request):
    return request.param, required(f"ATHENA_{request.param.upper()}_SQL")


@pytest.fixture
def role_expiry_connector(role_config):
    if os.getenv("ATHENA_RUN_ROLE_EXPIRY_TEST") != "1":
        pytest.skip("Set ATHENA_RUN_ROLE_EXPIRY_TEST=1 for actual 15-minute STS expiry")
    with AthenaConnector(role_config) as conn:
        yield conn
