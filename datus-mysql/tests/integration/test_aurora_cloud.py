"""DATUS_AURORA_MYSQL_CONFIG contains a complete JSON MySQLConfig for a test cluster."""

import pytest

from datus_db_core.testing import aurora
from datus_mysql import MySQLConfig, MySQLConnector

from .test_contract import test_deep_adapter_contract as assert_deep_contract

pytestmark = pytest.mark.integration


@pytest.fixture
def cloud():
    with aurora.cloud_connector(MySQLConnector, MySQLConfig, "DATUS_AURORA_MYSQL") as pair:
        yield pair


def test_real_aurora_query_and_metadata(cloud):
    aurora.assert_query_and_metadata(*cloud)


def test_real_aurora_deep_contract(cloud):
    assert_deep_contract(*cloud)


def test_real_iam_token_lifetime(cloud):
    aurora.assert_token_lifetime(*cloud, "SELECT CONNECTION_ID() AS session_id")


def test_invalid_iam_token_is_rejected(cloud):
    aurora.assert_invalid_iam_token(*cloud)


@pytest.mark.parametrize("invalid", ["ca", "hostname"])
def test_real_tls_rejection(cloud, invalid, tmp_path):
    connector, config = cloud
    assert connector.execute_query("SELECT 1", result_format="list").success
    aurora.assert_tls_rejection(MySQLConnector, config, invalid, tmp_path)


def test_real_iam_permission_denied(cloud):
    aurora.assert_iam_permission_denied(MySQLConnector, *cloud, "DATUS_AURORA_MYSQL")


def test_real_reader_endpoint(cloud):
    aurora.assert_reader_endpoint(
        MySQLConnector, cloud[1], "DATUS_AURORA_MYSQL", "SELECT @@innodb_read_only = 0 AS is_writer"
    )


def test_real_secret_rotation(cloud):
    aurora.assert_secret_rotation(*cloud)


def test_real_writer_failover(cloud):
    aurora.assert_failover(*cloud, "DATUS_AURORA_MYSQL", "SELECT @@innodb_read_only = 0 AS is_writer")
