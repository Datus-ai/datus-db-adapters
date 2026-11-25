import pytest

from datus_sqlalchemy.testing import assert_query_timeout_override, assert_stalled_response_timeout
from datus_tidb import TiDBConnector

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("operation", ["query", "metadata", "test_connection"])
def test_real_server_silent_response_is_bounded(config, operation, connector):
    assert_stalled_response_timeout(
        config,
        TiDBConnector,
        operation,
    )


def test_query_timeout_override_is_scoped_to_one_select(config):
    assert_query_timeout_override(
        config, TiDBConnector, "SELECT @@SESSION.max_execution_time AS deadline", units_per_second=1000
    )
