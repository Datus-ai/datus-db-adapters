"""Unit tests for the DWS compatibility-database provisioner.

The script sends an admin password across the public internet to an ephemeral
cluster's EIP, and creates databases whose dialect cannot be changed later, so
the TLS decision and the compatibility check are both exercised here.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("dws_databases", REPO_ROOT / "ci" / "cloud" / "dws" / "databases.py")
databases = importlib.util.module_from_spec(_SPEC)
sys.modules["dws_databases"] = databases
_SPEC.loader.exec_module(databases)

# create_databases catches the driver's own exception type, so it needs the
# driver imported. CI installs it (see .github/workflows/test.yml); without it
# these skip rather than error, matching how the cluster tests gate the cloud
# SDKs.
requires_driver = pytest.mark.skipif(
    importlib.util.find_spec("psycopg2") is None,
    reason="psycopg2 is not installed",
)


@pytest.fixture
def env(monkeypatch):
    for name in ["DWS_SSLMODE", "DWS_SSLROOTCERT", "DWS_PORT", "DWS_CONNECT_TIMEOUT"]:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


class FakeCursor:
    def __init__(self, existing):
        self.existing = dict(existing)
        self.executed = []
        self._result = None

    def execute(self, sql, params=None):
        self.executed.append(sql)
        if "pg_database" in sql:
            self._result = (self.existing.get(params[0]),) if params[0] in self.existing else None

    def fetchone(self):
        return self._result

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, existing=()):
        self.cursor_obj = FakeCursor(existing)

    def cursor(self):
        return self.cursor_obj


# ==================== TLS ====================


def test_ssl_never_falls_back_to_plaintext(env):
    """libpq's `prefer` would, and an attacker can provoke that fallback."""
    assert databases.ssl_settings() == {"sslmode": "require"}


def test_ssl_verifies_the_server_once_a_ca_is_available(env):
    """Configuring the CA is all it takes; no second switch to remember."""
    env.setenv("DWS_SSLROOTCERT", "/tmp/ca.pem")

    assert databases.ssl_settings() == {"sslmode": "verify-ca", "sslrootcert": "/tmp/ca.pem"}


def test_ssl_rejects_verification_it_cannot_perform(env):
    """verify-ca with no CA must fail loudly, not silently drop to require."""
    env.setenv("DWS_SSLMODE", "verify-ca")

    with pytest.raises(databases.DatabaseError, match="DWS_SSLROOTCERT"):
        databases.ssl_settings()


def test_ssl_mode_can_be_overridden(env):
    env.setenv("DWS_SSLMODE", "require")
    env.setenv("DWS_SSLROOTCERT", "/tmp/ca.pem")

    assert databases.ssl_settings()["sslmode"] == "require"


# ==================== configuration ====================


def test_missing_host_names_itself(env, monkeypatch):
    monkeypatch.delenv("DWS_HOST", raising=False)

    with pytest.raises(databases.DatabaseError, match="DWS_HOST"):
        databases.connect()


def test_non_integer_port_is_a_configuration_error(env, monkeypatch):
    monkeypatch.setenv("DWS_HOST", "h")
    monkeypatch.setenv("DWS_USERNAME", "u")
    monkeypatch.setenv("DWS_PASSWORD", "p")
    monkeypatch.setenv("DWS_PORT", "eight-thousand")

    with pytest.raises(databases.DatabaseError, match="must be an integer"):
        databases.connect()


# ==================== database creation ====================


@requires_driver
def test_creates_both_non_ora_databases_in_their_modes():
    connection = FakeConnection()

    assert databases.create_databases(connection) == 2

    created = [s for s in connection.cursor_obj.executed if s.startswith("CREATE DATABASE")]
    assert created == [
        "CREATE DATABASE \"datus_ci_td\" DBCOMPATIBILITY 'TD'",
        "CREATE DATABASE \"datus_ci_mysql\" DBCOMPATIBILITY 'MYSQL'",
    ]


@requires_driver
def test_leaves_existing_databases_alone():
    connection = FakeConnection({"datus_ci_td": "TD", "datus_ci_mysql": "MYSQL"})

    assert databases.create_databases(connection) == 0
    assert [s for s in connection.cursor_obj.executed if s.startswith("CREATE DATABASE")] == []


@requires_driver
def test_refuses_a_database_whose_mode_is_wrong():
    """DBCOMPATIBILITY is fixed at creation, so a mismatch cannot be repaired.

    Reported here rather than as a dialect failure deep in the test run.
    """
    connection = FakeConnection({"datus_ci_td": "ORA"})

    with pytest.raises(databases.DatabaseError, match="already exists with DBCOMPATIBILITY 'ORA'"):
        databases.create_databases(connection)


def test_ora_is_not_created_separately():
    """ORA reuses the cluster's default database; a fourth would be a copy."""
    assert "ORA" not in databases.COMPATIBILITY_MODES.values()
