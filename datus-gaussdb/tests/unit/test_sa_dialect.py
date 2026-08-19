# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""SQLAlchemy dialect tests that never touch the native ``gaussdb`` driver."""

import ast
import inspect
import sys
import textwrap
import types
from unittest.mock import MagicMock, call, patch

import pytest
from sqlalchemy.dialects import registry
from sqlalchemy.dialects.postgresql.base import PGDialect
from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg
from sqlalchemy.dialects.postgresql.psycopg2 import PGDialect_psycopg2

from datus_gaussdb import sa_dialect
from datus_gaussdb.sa_dialect import GaussDBDialect, GaussDBPsycopg2Dialect


def _stub_gaussdb_module() -> types.ModuleType:
    """Minimal stand-in for the gaussdb driver module."""
    module = types.ModuleType("gaussdb")
    module.__version__ = "1.0.4"
    module.adapters = object()
    module.paramstyle = "pyformat"
    module.ClientCursor = type("ClientCursor", (), {})
    module.Error = type("Error", (Exception,), {})
    return module


def _stub_gaussdb_submodules(monkeypatch):
    """Install API-shaped driver submodules without importing native libpq."""

    class AdaptersMap:
        def __init__(self, adapters):
            self.source = adapters
            self.loaders = {}

        def register_loader(self, name, loader):
            self.loaders[name] = loader

    class Loader:
        pass

    modules = {
        "adapt": types.SimpleNamespace(AdaptersMap=AdaptersMap, Loader=Loader),
        "types": types.SimpleNamespace(TypeInfo=type("TypeInfo", (), {})),
        "types.string": types.SimpleNamespace(TextLoader=type("TextLoader", (), {})),
        "types.json": types.SimpleNamespace(
            Json=type("Json", (), {}),
            Jsonb=type("Jsonb", (), {}),
            set_json_loads=MagicMock(),
            set_json_dumps=MagicMock(),
        ),
        "types.hstore": types.SimpleNamespace(register_hstore=MagicMock()),
        "types.range": types.SimpleNamespace(Range=type("Range", (), {})),
        "types.multirange": types.SimpleNamespace(Multirange=type("Multirange", (), {})),
        "pq": types.SimpleNamespace(TransactionStatus=type("TransactionStatus", (), {"IDLE": 0})),
    }
    monkeypatch.setattr(sa_dialect, "_import_gaussdb_submodule", modules.__getitem__)
    return modules


# ==================== Driver coexistence ====================


@pytest.mark.acceptance
def test_import_dbapi_does_not_install_a_psycopg_alias(monkeypatch):
    """GaussDB-first processes leave the real driver's namespace untouched."""
    gaussdb = _stub_gaussdb_module()
    monkeypatch.delitem(sys.modules, "psycopg", raising=False)
    monkeypatch.setattr(sa_dialect, "import_gaussdb", lambda: gaussdb)

    dbapi = GaussDBDialect.import_dbapi()

    assert dbapi is gaussdb
    assert "psycopg" not in sys.modules


@pytest.mark.acceptance
def test_import_dbapi_preserves_an_already_loaded_psycopg(monkeypatch):
    """SaaS can load PostgreSQL storage before the official GaussDB driver."""
    gaussdb = _stub_gaussdb_module()
    psycopg = types.ModuleType("psycopg")
    psycopg_rows = types.ModuleType("psycopg.rows")
    monkeypatch.setitem(sys.modules, "psycopg", psycopg)
    monkeypatch.setitem(sys.modules, "psycopg.rows", psycopg_rows)
    monkeypatch.setattr(sa_dialect, "import_gaussdb", lambda: gaussdb)

    dbapi = GaussDBDialect.import_dbapi()

    assert dbapi is gaussdb
    assert sys.modules["psycopg"] is psycopg
    assert sys.modules["psycopg.rows"] is psycopg_rows


@pytest.mark.acceptance
def test_dialect_initialization_uses_gaussdb_adapters_without_touching_psycopg(monkeypatch):
    gaussdb = _stub_gaussdb_module()
    psycopg = types.ModuleType("psycopg")
    monkeypatch.setitem(sys.modules, "psycopg", psycopg)
    _stub_gaussdb_submodules(monkeypatch)

    dialect = GaussDBDialect(dbapi=gaussdb)

    assert dialect.dbapi is gaussdb
    assert dialect._psycopg_adapters_map.source is gaussdb.adapters
    assert sys.modules["psycopg"] is psycopg


@pytest.mark.acceptance
def test_driver_specific_type_hooks_resolve_from_gaussdb(monkeypatch):
    gaussdb = _stub_gaussdb_module()
    modules = _stub_gaussdb_submodules(monkeypatch)
    dialect = GaussDBDialect(dbapi=gaussdb)

    assert dialect._psycopg_Json is modules["types.json"].Json
    assert dialect._psycopg_Jsonb is modules["types.json"].Jsonb
    assert dialect._psycopg_TransactionStatus is modules["pq"].TransactionStatus
    assert dialect._psycopg_Range is modules["types.range"].Range
    assert dialect._psycopg_Multirange is modules["types.multirange"].Multirange


@pytest.mark.acceptance
def test_dialect_initialization_configures_gaussdb_json_and_inet(monkeypatch):
    gaussdb = _stub_gaussdb_module()
    modules = _stub_gaussdb_submodules(monkeypatch)
    loads = MagicMock()
    dumps = MagicMock()

    dialect = GaussDBDialect(
        dbapi=gaussdb,
        native_inet_types=False,
        json_deserializer=loads,
        json_serializer=dumps,
    )

    adapters = dialect._psycopg_adapters_map
    assert adapters.loaders == {
        "inet": modules["types.string"].TextLoader,
        "cidr": modules["types.string"].TextLoader,
    }
    modules["types.json"].set_json_loads.assert_called_once_with(loads, adapters)
    modules["types.json"].set_json_dumps.assert_called_once_with(dumps, adapters)


@pytest.mark.acceptance
def test_initialize_registers_hstore_through_gaussdb(monkeypatch):
    modules = _stub_gaussdb_submodules(monkeypatch)
    dialect = GaussDBDialect.__new__(GaussDBDialect)
    dialect.insert_returning = True
    dialect.use_native_hstore = True
    dialect._psycopg_adapters_map = object()
    dialect._type_info_fetch = MagicMock(return_value="hstore-info")
    connection = MagicMock()

    with patch.object(PGDialect, "initialize", return_value=None):
        dialect.initialize(connection)

    register = modules["types.hstore"].register_hstore
    assert register.call_args_list == [
        call("hstore-info", dialect._psycopg_adapters_map),
        call("hstore-info", connection.connection.driver_connection),
    ]


@pytest.mark.acceptance
def test_official_driver_async_dialect_is_explicitly_unsupported():
    with pytest.raises(NotImplementedError, match="synchronous engines only"):
        GaussDBDialect.get_async_dialect_cls(MagicMock())


@pytest.mark.acceptance
def test_all_sqlalchemy_psycopg_import_hooks_are_overridden():
    """Fail loudly if a SQLAlchemy upgrade adds a new concrete-driver import."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(PGDialect_psycopg)))
    class_node = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    hard_import_hooks = set()

    for node in class_node.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Import) and any(alias.name.startswith("psycopg") for alias in child.names):
                hard_import_hooks.add(node.name)
            if isinstance(child, ast.ImportFrom) and (child.module or "").startswith("psycopg"):
                hard_import_hooks.add(node.name)

    missing = hard_import_hooks - GaussDBDialect.__dict__.keys()
    assert not missing, f"GaussDBDialect must override new psycopg-bound hooks: {sorted(missing)}"


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
def test_create_connect_args_injects_client_cursor():
    """Binary-format bound parameters come back NULL, so a ClientCursor is forced."""
    module = _stub_gaussdb_module()
    dialect = GaussDBDialect.__new__(GaussDBDialect)
    dialect.dbapi = module

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
def test_create_connect_args_keeps_explicit_cursor_factory():
    """An explicitly configured cursor_factory is not overwritten."""
    module = _stub_gaussdb_module()
    custom_cursor = type("CustomCursor", (), {})
    dialect = GaussDBDialect.__new__(GaussDBDialect)
    dialect.dbapi = module

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


@pytest.mark.acceptance
def test_stock_postgresql_psycopg_dialect_is_unchanged():
    """The official GaussDB dialect does not replace PostgreSQL's psycopg3 dialect."""
    assert registry.load("postgresql.psycopg") is PGDialect_psycopg


# ==================== pg8000 dialect ====================


def test_pg8000_dialect_identity_and_registration():
    from sqlalchemy.dialects.postgresql.pg8000 import PGDialect_pg8000

    from datus_gaussdb.sa_dialect import GaussDBPg8000Dialect

    assert GaussDBPg8000Dialect.name == "gaussdb"
    assert GaussDBPg8000Dialect.driver == "pg8000"
    assert GaussDBPg8000Dialect.supports_statement_cache is True
    assert issubclass(GaussDBPg8000Dialect, PGDialect_pg8000)
    assert registry.load("gaussdb.pg8000") is GaussDBPg8000Dialect


def test_pg8000_import_dbapi_is_gauss_module_and_preserves_psycopg(monkeypatch):
    from datus_gaussdb import _pg8000_gauss
    from datus_gaussdb.sa_dialect import GaussDBPg8000Dialect

    psycopg = types.ModuleType("psycopg")
    monkeypatch.setitem(sys.modules, "psycopg", psycopg)

    assert GaussDBPg8000Dialect.import_dbapi() is _pg8000_gauss
    assert sys.modules["psycopg"] is psycopg


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


def test_official_driver_on_connect_registers_gaussdb_bool_loader(monkeypatch):
    modules = _stub_gaussdb_submodules(monkeypatch)
    dialect = GaussDBDialect.__new__(GaussDBDialect)
    conn = MagicMock()

    with patch.object(PGDialect_psycopg, "on_connect", return_value=None):
        hook = dialect.on_connect()
    hook(conn)

    name, loader = conn.adapters.register_loader.call_args.args
    assert name == "bool"
    assert issubclass(loader, modules["adapt"].Loader)
    assert loader.load(object(), b"1") is True
    assert loader.load(object(), b"0") is False


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


# ==================== Inline CA certificate ====================


def test_pg8000_ssl_context_accepts_inline_pem():
    """An uploaded certificate verifies from memory — no file is written."""
    import ssl

    from datus_gaussdb.sa_dialect import _build_pg8000_ssl_context

    try:
        ctx = _build_pg8000_ssl_context("verify-ca", _STUB_CERT)
    except ssl.SSLError:
        pytest.skip("stub certificate rejected by this OpenSSL build")

    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.get_ca_certs(), "the inline certificate should be loaded into the trust store"


def test_inline_pem_is_materialized_once_and_kept_private():
    """libpq drivers only read a filename, so inline PEM has to hit the disk."""
    import os

    from datus_gaussdb import _ca_cert

    path = _ca_cert.as_path(_STUB_CERT)

    assert path != _STUB_CERT
    assert open(path, encoding="utf-8").read() == _STUB_CERT
    # A trust anchor other users can rewrite is worse than no verification.
    assert os.stat(path).st_mode & 0o077 == 0
    # Reused rather than re-written on every connection.
    assert _ca_cert.as_path(_STUB_CERT) == path


def test_ca_path_passes_through_untouched(tmp_path):
    """A self-hosted deployment that mounts its CA file keeps working."""
    from datus_gaussdb import _ca_cert

    ca = tmp_path / "ca.pem"
    ca.write_text(_STUB_CERT)

    assert _ca_cert.as_path(str(ca)) == str(ca)
    assert _ca_cert.as_path(None) is None
    assert _ca_cert.is_inline_pem(str(ca)) is False


def test_libpq_dialects_swap_inline_pem_for_a_path():
    """Both libpq-based dialects hand the driver a filename, never the PEM."""
    from datus_gaussdb.sa_dialect import _resolve_ca_kwarg

    kwargs = {"sslrootcert": _STUB_CERT}
    _resolve_ca_kwarg(kwargs)

    assert kwargs["sslrootcert"] != _STUB_CERT
    assert open(kwargs["sslrootcert"], encoding="utf-8").read() == _STUB_CERT
