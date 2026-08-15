# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""SQLAlchemy dialects for GaussDB/openGauss client drivers.

The official ``gaussdb`` driver is a psycopg3 fork with the module tree renamed
psycopg->gaussdb. SQLAlchemy's built-in ``postgresql+psycopg`` dialect
hard-imports ``psycopg`` submodules in ~15 places; rather than reimplementing
the dialect, the (API-identical) ``gaussdb`` module tree is aliased into
``sys.modules`` under the ``psycopg`` names before the dialect first touches
them.

The optional psycopg2 path uses its own GaussDB-named dialect. This keeps the
stock PostgreSQL dialect untouched while allowing PostgreSQL's macOS libpq to
connect to servers that expose the compatible wire protocol.

The pure-Python pg8000 path (``datus_gaussdb._pg8000_gauss``) speaks the
GaussDB SHA256 handshake natively and therefore works on every platform,
macOS included; it is selected by default there.

URL forms::

    gaussdb+psycopg://user:password@host:port/database
    gaussdb+psycopg2://user:password@host:port/database
    gaussdb+pg8000://user:password@host:port/database
"""

import sys
import types

from sqlalchemy.dialects import registry
from sqlalchemy.dialects.postgresql.pg8000 import PGDialect_pg8000
from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg
from sqlalchemy.dialects.postgresql.psycopg2 import PGDialect_psycopg2

from ._libpq import import_gaussdb

# GaussDB databases in 'B' (MySQL) compatibility mode render booleans as
# '1'/'0' instead of PostgreSQL's 't'/'f' — the type OID stays BOOL, only
# the text changes, so strict parsers silently read every True as False.
# A tolerant parser is correct in every compatibility mode ('t' never means
# False and '1' never means True elsewhere), so it is registered
# unconditionally instead of per-mode.
_BOOL_TRUE_TEXTS = ("t", "true", "1", "y", "yes", "on")


def _gaussdb_bool_in(data):
    return data in _BOOL_TRUE_TEXTS


_PSYCOPG_SUBMODULES = (
    "adapt",
    "pq",
    "rows",
    "sql",
    "types",
    "types.array",
    "types.datetime",
    "types.hstore",
    "types.json",
    "types.multirange",
    "types.range",
    "types.string",
)


def _alias_gaussdb_as_psycopg():
    """Alias the gaussdb module tree under the ``psycopg`` names.

    The parent dialect hard-imports ``psycopg`` submodules from roughly a
    dozen call sites, so the fork is published under those names instead of
    the dialect being reimplemented. Since a process cannot hold two
    different modules under one name, real psycopg and this dialect are
    mutually exclusive within a process; the conflicting case raises instead
    of silently mixing the two drivers' adapter registries.
    """
    import_gaussdb()
    aliased = sys.modules.get("psycopg")
    if aliased is not None:
        if aliased is not sys.modules["gaussdb"]:
            raise ImportError(
                "psycopg is already imported in this process, so the GaussDB "
                "dialect cannot alias the gaussdb driver onto it. Use the "
                "GaussDB datasource in a process that does not import psycopg "
                "(psycopg2 is unaffected), or set driver='psycopg2' on the "
                "GaussDB datasource."
            )
        return
    import importlib

    sys.modules["psycopg"] = sys.modules["gaussdb"]
    for name in _PSYCOPG_SUBMODULES:
        try:
            sys.modules[f"psycopg.{name}"] = importlib.import_module(f"gaussdb.{name}")
        except ImportError:
            pass


class _GaussDBDbapiProxy(types.ModuleType):
    """Delegate to the gaussdb module while reporting a psycopg-3.x version.

    The fork versions itself 1.x; PGDialect_psycopg refuses anything below
    psycopg 3.0.2.
    """

    def __init__(self, module):
        super().__init__(module.__name__)
        self._module = module

    def __getattr__(self, name):
        return getattr(self._module, name)

    @property
    def __version__(self):
        return "3.2.0"


class GaussDBDialect(PGDialect_psycopg):
    name = "gaussdb"
    driver = "psycopg"
    supports_statement_cache = True

    @classmethod
    def import_dbapi(cls):
        _alias_gaussdb_as_psycopg()
        return _GaussDBDbapiProxy(sys.modules["gaussdb"])

    def create_connect_args(self, url):
        args, kwargs = super().create_connect_args(url)
        # GaussDB/openGauss silently turns binary-format bound parameters
        # (int, date, ...) into NULL. ClientCursor interpolates parameters
        # client-side (psycopg2 semantics), which is fully correct against
        # this server family.
        kwargs.setdefault("cursor_factory", sys.modules["gaussdb"].ClientCursor)
        return args, kwargs

    def _get_server_version_info(self, connection):
        return _get_server_version_info(connection)

    def on_connect(self):
        parent = super().on_connect()
        gaussdb_mod = sys.modules["gaussdb"]

        class _TolerantBoolLoader(gaussdb_mod.adapt.Loader):
            def load(self, data):
                return bytes(data).decode("ascii") in _BOOL_TRUE_TEXTS

        def connect(conn):
            if parent is not None:
                parent(conn)
            conn.adapters.register_loader("bool", _TolerantBoolLoader)

        return connect


class GaussDBPsycopg2Dialect(PGDialect_psycopg2):
    """GaussDB/openGauss dialect using PostgreSQL's psycopg2 client.

    Linux continues to use the official ``gaussdb`` driver unless
    ``driver: psycopg2`` is configured explicitly. macOS selects this path by
    default because no compatible native GaussDB/openGauss libpq is available.
    """

    name = "gaussdb"
    driver = "psycopg2"
    supports_statement_cache = True

    def _get_server_version_info(self, connection):
        return _get_server_version_info(connection)

    def on_connect(self):
        parent = super().on_connect()

        def connect(conn):
            if parent is not None:
                parent(conn)
            import psycopg2.extensions as ext

            tolerant_bool = ext.new_type(
                (16,),
                "GAUSSDB_BOOLEAN",
                lambda value, _cur: None if value is None else value in _BOOL_TRUE_TEXTS,
            )
            ext.register_type(tolerant_bool, conn)

        return connect


def _build_pg8000_ssl_context(sslmode, sslrootcert):
    """Map the libpq ``sslmode`` vocabulary onto pg8000's ``ssl_context``.

    pg8000 semantics: ``False`` skips the SSLRequest entirely, ``None``
    negotiates but falls back to plaintext (libpq's prefer), ``True``
    requires TLS without verification, and a custom ``SSLContext`` requires
    TLS with that context's verification rules.

    Two libpq subtleties carried over deliberately:

    * ``allow`` is treated as ``prefer`` (TLS-first with plaintext fallback)
      rather than libpq's plaintext-first order — the same approximation the
      Rust executor and most non-libpq drivers make; both spellings end up
      connected either way.
    * ``require`` **with** ``sslrootcert`` verifies the chain like
      ``verify-ca`` — libpq's documented backwards-compatibility behavior.
      Without a CA file it only encrypts.
    """
    import ssl as ssl_module

    mode = (sslmode or "prefer").strip().lower()
    if mode == "disable":
        return False
    if mode in ("allow", "prefer"):
        return None
    if mode == "require":
        if not sslrootcert:
            return True
        context = ssl_module.create_default_context(cafile=sslrootcert)
        context.check_hostname = False
        return context
    if mode in ("verify-ca", "verify-full"):
        if not sslrootcert:
            raise ValueError(f"sslmode={mode} requires sslrootcert to point at the CA certificate")
        context = ssl_module.create_default_context(cafile=sslrootcert)
        context.check_hostname = mode == "verify-full"
        return context
    raise ValueError(
        f"unknown sslmode {mode!r}: expected one of disable, allow, prefer, require, verify-ca, verify-full"
    )


class GaussDBPg8000Dialect(PGDialect_pg8000):
    """GaussDB/openGauss dialect over the pure-Python pg8000 driver.

    The DB-API module is :mod:`datus_gaussdb._pg8000_gauss`, which extends
    pg8000 with the GaussDB SHA256 handshake (startup protocol 3.51). Pure
    Python end to end — no libpq — so this path works on every platform and
    is the macOS default.
    """

    name = "gaussdb"
    driver = "pg8000"
    supports_statement_cache = True

    @classmethod
    def import_dbapi(cls):
        from . import _pg8000_gauss

        return _pg8000_gauss

    def create_connect_args(self, url):
        args, opts = super().create_connect_args(url)
        sslmode = opts.pop("sslmode", None)
        sslrootcert = opts.pop("sslrootcert", None)
        opts["ssl_context"] = _build_pg8000_ssl_context(sslmode, sslrootcert)
        # Chinese GaussDB deployments often run server_encoding=GBK, and the
        # server then defaults client_encoding to GBK too. pg8000 does not
        # negotiate an encoding at startup — it decodes with whatever the
        # server reports — so pin UTF8 and let the server transcode.
        startup = dict(opts.get("startup_params") or {})
        startup.setdefault("client_encoding", "UTF8")
        opts["startup_params"] = startup
        return args, opts

    def on_connect(self):
        parent = super().on_connect()

        def connect(conn):
            if parent is not None:
                parent(conn)
            conn.register_in_adapter(16, _gaussdb_bool_in)

        return connect

    def _get_server_version_info(self, connection):
        return _get_server_version_info(connection)


def _get_server_version_info(connection):
    """Read the PostgreSQL compatibility level without parsing ``version()``.

    GaussDB's ``version()`` returns ``gaussdb (GaussDB Kernel 503...)`` and
    openGauss returns ``(openGauss 7.0...)``. Neither matches SQLAlchemy's
    PostgreSQL-only regular expression. Both server families report their
    PostgreSQL compatibility level through ``server_version`` instead.
    """
    val = connection.exec_driver_sql("SHOW server_version").scalar()
    parts = []
    for token in str(val).replace("-", ".").split("."):
        if token.isdigit():
            parts.append(int(token))
        else:
            break
    return tuple(parts) if parts else (9, 2)


registry.register("gaussdb", "datus_gaussdb.sa_dialect", "GaussDBDialect")
registry.register("gaussdb.psycopg", "datus_gaussdb.sa_dialect", "GaussDBDialect")
registry.register("gaussdb.psycopg2", "datus_gaussdb.sa_dialect", "GaussDBPsycopg2Dialect")
registry.register("gaussdb.pg8000", "datus_gaussdb.sa_dialect", "GaussDBPg8000Dialect")
