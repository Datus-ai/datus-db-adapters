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
from sqlalchemy.dialects.postgresql.psycopg2 import PGDialect_psycopg2

from datus_gaussdb import sa_dialect
from datus_gaussdb.sa_dialect import GaussDBDialect, GaussDBPsycopg2Dialect, _GaussDBDbapiProxy


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


@pytest.mark.acceptance
def test_psycopg2_server_version_info_uses_gaussdb_parser():
    """The macOS-compatible path does not invoke PostgreSQL's version regex."""
    dialect = GaussDBPsycopg2Dialect.__new__(GaussDBPsycopg2Dialect)
    connection = MagicMock()
    connection.exec_driver_sql.return_value.scalar.return_value = "9.2.4-openGauss"

    result = dialect._get_server_version_info(connection)

    assert result == (9, 2, 4)
    connection.exec_driver_sql.assert_called_once_with("SHOW server_version")


# ==================== create_connect_args ====================


@pytest.mark.acceptance
def test_create_connect_args_injects_client_cursor(monkeypatch):
    """Binary-format bound parameters come back NULL, so a ClientCursor is forced."""
    module = _stub_gaussdb_module()
    monkeypatch.setitem(sys.modules, "gaussdb", module)
    dialect = GaussDBDialect.__new__(GaussDBDialect)

    with patch.object(
        PGDialect_psycopg,
        "create_connect_args",
        return_value=([], {"dbname": "postgres"}),
    ):
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

    with patch.object(
        PGDialect_psycopg,
        "create_connect_args",
        return_value=([], {"cursor_factory": custom_cursor}),
    ):
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

    assert GaussDBPsycopg2Dialect.name == "gaussdb"
    assert GaussDBPsycopg2Dialect.driver == "psycopg2"
    assert GaussDBPsycopg2Dialect.supports_statement_cache is True
    assert issubclass(GaussDBPsycopg2Dialect, PGDialect_psycopg2)


@pytest.mark.acceptance
def test_dialect_registered_in_sqlalchemy_registry():
    """All public registry names resolve without touching a native driver."""
    assert registry.load("gaussdb") is GaussDBDialect
    assert registry.load("gaussdb.psycopg") is GaussDBDialect
    assert registry.load("gaussdb.psycopg2") is GaussDBPsycopg2Dialect


@pytest.mark.acceptance
def test_stock_postgresql_psycopg2_dialect_is_unchanged():
    """Registering GaussDB's dialect does not replace PostgreSQL behavior."""
    assert registry.load("postgresql.psycopg2") is PGDialect_psycopg2


# ==================== pg8000 dialect ====================


def test_pg8000_dialect_identity_and_registration():
    from sqlalchemy.dialects.postgresql.pg8000 import PGDialect_pg8000

    from datus_gaussdb.sa_dialect import GaussDBPg8000Dialect

    assert GaussDBPg8000Dialect.name == "gaussdb"
    assert GaussDBPg8000Dialect.driver == "pg8000"
    assert GaussDBPg8000Dialect.supports_statement_cache is True
    assert issubclass(GaussDBPg8000Dialect, PGDialect_pg8000)
    assert registry.load("gaussdb.pg8000") is GaussDBPg8000Dialect


def test_pg8000_import_dbapi_is_gauss_module():
    from datus_gaussdb import _pg8000_gauss
    from datus_gaussdb.sa_dialect import GaussDBPg8000Dialect

    assert GaussDBPg8000Dialect.import_dbapi() is _pg8000_gauss


@pytest.mark.parametrize(
    ("sslmode", "expected"),
    # `allow` is deliberately treated as `prefer` (TLS-first with plaintext
    # fallback) — the same approximation the Rust executor makes.
    [
        ("disable", False),
        ("allow", None),
        ("prefer", None),
        (None, None),
        ("require", True),
    ],
)
def test_pg8000_ssl_context_simple_modes(sslmode, expected):
    from datus_gaussdb.sa_dialect import _build_pg8000_ssl_context

    assert _build_pg8000_ssl_context(sslmode, None) is expected


# A real (EC self-signed, 10-year) certificate so ssl actually loads it and
# the CERT_REQUIRED assertions below genuinely execute; a truncated stub gets
# rejected by some OpenSSL builds, silently skipping the tests.
_STUB_CERT = (
    "-----BEGIN CERTIFICATE-----\n"
    "MIIBhjCCASugAwIBAgIUKYy4YU1TuNflu3n4/hhvaCxV+dswCgYIKoZIzj0EAwIw\n"
    "GDEWMBQGA1UEAwwNZGF0dXMtdGVzdC1jYTAeFw0yNjA4MTUwODMyMThaFw0zNjA4\n"
    "MTIwODMyMThaMBgxFjAUBgNVBAMMDWRhdHVzLXRlc3QtY2EwWTATBgcqhkjOPQIB\n"
    "BggqhkjOPQMBBwNCAAQgcvgmKbaVP7SSMOn580Uv1Jy5GEoaVsFjdzF5wX1o+jPy\n"
    "6D3hUeLoB95uA3Po1sOpp0xr6AcBwrZXvwU6ngOEo1MwUTAdBgNVHQ4EFgQU/8U6\n"
    "FzlhBpXz2fMHuCl02FR9wtkwHwYDVR0jBBgwFoAU/8U6FzlhBpXz2fMHuCl02FR9\n"
    "wtkwDwYDVR0TAQH/BAUwAwEB/zAKBggqhkjOPQQDAgNJADBGAiEAqLXrwfHKkwR3\n"
    "8QaKlb+w/uMxfVr9wBQ6i4wZjk+tT70CIQDzLUGn2oBEo9QSqt9G8JLkBZUfa8Lv\n"
    "sRlAkeTWs5ocPg==\n"
    "-----END CERTIFICATE-----\n"
)


def test_pg8000_ssl_context_verify_modes(tmp_path):
    import ssl

    from datus_gaussdb.sa_dialect import _build_pg8000_ssl_context

    # A self-signed cert generated on the fly is overkill; a stub CA file is
    # enough for context construction (load failure = ssl.SSLError).
    ca = tmp_path / "ca.pem"
    ca.write_text(_STUB_CERT)
    try:
        ctx = _build_pg8000_ssl_context("verify-ca", str(ca))
    except ssl.SSLError:
        pytest.skip("stub certificate rejected by this OpenSSL build")
    assert ctx.check_hostname is False
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_pg8000_require_with_rootcert_verifies_like_verify_ca(tmp_path):
    """libpq back-compat: `require` + a CA file validates the chain."""
    import ssl

    from datus_gaussdb.sa_dialect import _build_pg8000_ssl_context

    ca = tmp_path / "ca.pem"
    ca.write_text(_STUB_CERT)
    try:
        ctx = _build_pg8000_ssl_context("require", str(ca))
    except ssl.SSLError:
        pytest.skip("stub certificate rejected by this OpenSSL build")
    assert ctx is not True
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is False


def test_pg8000_ssl_context_verify_requires_rootcert():
    from datus_gaussdb.sa_dialect import _build_pg8000_ssl_context

    for mode in ("verify-ca", "verify-full"):
        with pytest.raises(ValueError, match="sslrootcert"):
            _build_pg8000_ssl_context(mode, None)


def test_pg8000_ssl_context_unknown_mode_rejected():
    from datus_gaussdb.sa_dialect import _build_pg8000_ssl_context

    with pytest.raises(ValueError, match="unknown sslmode"):
        _build_pg8000_ssl_context("mystery", None)


def test_pg8000_create_connect_args_pins_utf8_client_encoding():
    """GBK-encoded databases default client_encoding to GBK; pg8000 has no
    GBK codec mapping and would silently mis-decode, so UTF8 is pinned."""
    from sqlalchemy.engine import make_url

    from datus_gaussdb.sa_dialect import GaussDBPg8000Dialect

    dialect = GaussDBPg8000Dialect()
    url = make_url("gaussdb+pg8000://u:p@h:25434/db")
    _, opts = dialect.create_connect_args(url)
    assert opts["startup_params"]["client_encoding"] == "UTF8"


def test_pg8000_create_connect_args_moves_ssl_params():
    from sqlalchemy.engine import make_url

    from datus_gaussdb.sa_dialect import GaussDBPg8000Dialect

    dialect = GaussDBPg8000Dialect()
    url = make_url("gaussdb+pg8000://u:p@h:25434/db?sslmode=disable")
    _, opts = dialect.create_connect_args(url)

    assert opts["ssl_context"] is False
    assert "sslmode" not in opts
    assert "sslrootcert" not in opts
    assert opts["port"] == 25434


# ==================== compatibility-mode bool decoding ====================


def test_tolerant_bool_parser_covers_all_compat_modes():
    """'t'/'f' (PG and A modes) and '1'/'0' (B mode) must both decode
    correctly; B mode's '1' silently reads as False with a strict parser."""
    from datus_gaussdb.sa_dialect import _gaussdb_bool_in

    assert _gaussdb_bool_in("t") is True
    assert _gaussdb_bool_in("true") is True
    assert _gaussdb_bool_in("1") is True
    assert _gaussdb_bool_in("f") is False
    assert _gaussdb_bool_in("false") is False
    assert _gaussdb_bool_in("0") is False


def test_pg8000_on_connect_registers_bool_adapter():
    from datus_gaussdb.sa_dialect import GaussDBPg8000Dialect, _gaussdb_bool_in

    dialect = GaussDBPg8000Dialect()
    conn = MagicMock()
    hook = dialect.on_connect()
    hook(conn)
    conn.register_in_adapter.assert_called_once_with(16, _gaussdb_bool_in)


def test_psycopg2_on_connect_registers_bool_caster():
    from datus_gaussdb.sa_dialect import GaussDBPsycopg2Dialect

    dialect = GaussDBPsycopg2Dialect()
    conn = MagicMock()
    with (
        patch("psycopg2.extensions.new_type") as new_type,
        patch("psycopg2.extensions.register_type") as reg,
    ):
        new_type.return_value = "caster"
        hook = dialect.on_connect()
        hook(conn)

    (oids, name, parse), _ = new_type.call_args
    assert oids == (16,)
    assert parse("1", None) is True
    assert parse("t", None) is True
    assert parse("0", None) is False
    assert parse(None, None) is None
    reg.assert_called_once_with("caster", conn)
