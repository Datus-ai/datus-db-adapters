# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""SQLAlchemy dialect tests that never touch the real ``gaussdb`` driver.

The driver binds a GaussDB-specific libpq at import time and is unavailable on
macOS, so every test here works against a stub module installed in
``sys.modules`` for the duration of the test.
"""

import sys
import types
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.dialects import registry
from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg

from datus_gaussdb import sa_dialect
from datus_gaussdb.sa_dialect import GaussDBDialect, _GaussDBDbapiProxy


def _stub_gaussdb_module() -> types.ModuleType:
    """Minimal stand-in for the gaussdb driver module."""
    module = types.ModuleType("gaussdb")
    module.__version__ = "1.0.4"
    module.ClientCursor = type("ClientCursor", (), {})
    module.Error = type("Error", (Exception,), {})
    return module


# ==================== _GaussDBDbapiProxy ====================


@pytest.mark.acceptance
def test_dbapi_proxy_reports_psycopg3_version():
    """PGDialect_psycopg refuses anything below psycopg 3.0.2, so the 1.x fork lies."""
    module = _stub_gaussdb_module()

    proxy = _GaussDBDbapiProxy(module)

    assert module.__version__ == "1.0.4"
    assert proxy.__version__ == "3.2.0"


@pytest.mark.acceptance
def test_dbapi_proxy_delegates_attributes():
    """Everything other than the version comes straight from the driver module."""
    module = _stub_gaussdb_module()

    proxy = _GaussDBDbapiProxy(module)

    assert proxy.ClientCursor is module.ClientCursor
    assert proxy.Error is module.Error
    assert proxy.__name__ == "gaussdb"


@pytest.mark.acceptance
def test_dbapi_proxy_raises_for_unknown_attribute():
    """Missing driver attributes surface as AttributeError, not None."""
    proxy = _GaussDBDbapiProxy(_stub_gaussdb_module())
    missing = "no_such_attribute"

    with pytest.raises(AttributeError):
        getattr(proxy, missing)


# ==================== Driver aliasing ====================


@pytest.mark.acceptance
def test_import_dbapi_aliases_gaussdb_when_psycopg_is_not_loaded(monkeypatch):
    """GaussDB-first processes install the compatible driver under psycopg."""
    gaussdb = _stub_gaussdb_module()
    monkeypatch.setitem(sys.modules, "gaussdb", gaussdb)
    monkeypatch.delitem(sys.modules, "psycopg", raising=False)
    monkeypatch.setattr(sa_dialect, "import_gaussdb", lambda: gaussdb)

    dbapi = GaussDBDialect.import_dbapi()

    assert sys.modules["psycopg"] is gaussdb
    assert dbapi._module is gaussdb


@pytest.mark.acceptance
def test_import_dbapi_rejects_an_already_loaded_psycopg(monkeypatch):
    """Psycopg-first processes fail clearly instead of mixing registries."""
    gaussdb = _stub_gaussdb_module()
    psycopg = types.ModuleType("psycopg")
    monkeypatch.setitem(sys.modules, "gaussdb", gaussdb)
    monkeypatch.setitem(sys.modules, "psycopg", psycopg)
    monkeypatch.setattr(sa_dialect, "import_gaussdb", lambda: gaussdb)

    with pytest.raises(ImportError, match="psycopg is already imported"):
        GaussDBDialect.import_dbapi()


# ==================== _get_server_version_info ====================


def _version_info(server_version: str):
    dialect = GaussDBDialect.__new__(GaussDBDialect)
    connection = MagicMock()
    connection.exec_driver_sql.return_value.scalar.return_value = server_version
    result = dialect._get_server_version_info(connection)
    connection.exec_driver_sql.assert_called_once_with("SHOW server_version")
    return result


@pytest.mark.acceptance
def test_server_version_info_parses_three_parts():
    """GaussDB reports its PostgreSQL compatibility level via server_version."""
    assert _version_info("9.2.4") == (9, 2, 4)


@pytest.mark.acceptance
def test_server_version_info_parses_two_parts():
    """A two-component version is returned as-is."""
    assert _version_info("9.2") == (9, 2)


@pytest.mark.acceptance
def test_server_version_info_stops_at_non_numeric_suffix():
    """Build suffixes such as '9.2.4-openGauss' are truncated, not rejected."""
    assert _version_info("9.2.4-openGauss") == (9, 2, 4)


@pytest.mark.acceptance
def test_server_version_info_falls_back_when_unparseable():
    """An unparseable version degrades to the documented 9.2 baseline."""
    assert _version_info("GaussDB Kernel V500R002") == (9, 2)


# ==================== create_connect_args ====================


@pytest.mark.acceptance
def test_create_connect_args_injects_client_cursor(monkeypatch):
    """Binary-format bound parameters come back NULL, so a ClientCursor is forced."""
    module = _stub_gaussdb_module()
    monkeypatch.setitem(sys.modules, "gaussdb", module)
    dialect = GaussDBDialect.__new__(GaussDBDialect)

    with patch.object(PGDialect_psycopg, "create_connect_args", return_value=([], {"dbname": "postgres"})):
        args, kwargs = dialect.create_connect_args(MagicMock())

    assert args == []
    assert kwargs["dbname"] == "postgres"
    assert kwargs["cursor_factory"] is module.ClientCursor


@pytest.mark.acceptance
def test_create_connect_args_keeps_explicit_cursor_factory(monkeypatch):
    """An explicitly configured cursor_factory is not overwritten."""
    module = _stub_gaussdb_module()
    monkeypatch.setitem(sys.modules, "gaussdb", module)
    custom_cursor = type("CustomCursor", (), {})
    dialect = GaussDBDialect.__new__(GaussDBDialect)

    with patch.object(PGDialect_psycopg, "create_connect_args", return_value=([], {"cursor_factory": custom_cursor})):
        _, kwargs = dialect.create_connect_args(MagicMock())

    assert kwargs["cursor_factory"] is custom_cursor


# ==================== Dialect identity and registration ====================


@pytest.mark.acceptance
def test_dialect_identity():
    """The dialect names decide which URL prefixes resolve here."""
    assert GaussDBDialect.name == "gaussdb"
    assert GaussDBDialect.driver == "psycopg"
    assert GaussDBDialect.supports_statement_cache is True
    assert issubclass(GaussDBDialect, PGDialect_psycopg)


@pytest.mark.acceptance
def test_dialect_registered_in_sqlalchemy_registry():
    """Importing the module registers both the bare and driver-qualified names.

    ``registry.impls`` is checked directly instead of ``registry.load`` because
    loading would import the real gaussdb driver.
    """
    assert "gaussdb" in registry.impls
    assert "gaussdb.psycopg" in registry.impls
