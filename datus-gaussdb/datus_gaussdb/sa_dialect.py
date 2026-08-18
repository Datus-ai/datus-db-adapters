# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""SQLAlchemy dialects for GaussDB/openGauss client drivers.

The official ``gaussdb`` driver is a psycopg3 fork with the module tree renamed
psycopg->gaussdb. SQLAlchemy's built-in ``postgresql+psycopg`` dialect imports
the concrete ``psycopg`` package from several driver-specific hooks. This
module overrides those hooks to use ``gaussdb`` directly, so PostgreSQL's real
``psycopg`` package and the GaussDB driver can coexist in one process.

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

import importlib

from sqlalchemy import util
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


def _import_gaussdb_submodule(name: str):
    """Import a driver submodule lazily without touching ``psycopg``."""
    return importlib.import_module(f"gaussdb.{name}")


class GaussDBDialect(PGDialect_psycopg):
    """SQLAlchemy's psycopg dialect bound directly to the GaussDB fork.

    ``PGDialect_psycopg`` is still the correct behavioral base because the
    official driver preserves psycopg3's DB-API and adaptation interfaces.
    Its driver-specific hooks import the concrete ``psycopg`` package,
    though. Each such hook is overridden here so this dialect never mutates
    or consumes the real PostgreSQL driver's module namespace.
    """

    name = "gaussdb"
    driver = "psycopg"
    supports_statement_cache = True

    def __init__(self, **kwargs):
        # PGDialect_psycopg only performs its hard-coded ``psycopg`` imports
        # when a DB-API module is supplied. Let it initialize the common
        # PostgreSQL state without a driver, then install the real GaussDB
        # module and the equivalent adapter map ourselves.
        dbapi = kwargs.pop("dbapi", None)
        super().__init__(dbapi=None, **kwargs)
        self.dbapi = dbapi

        if dbapi is None:
            return

        adapt = _import_gaussdb_submodule("adapt")
        adapters_map = adapt.AdaptersMap(dbapi.adapters)
        self._psycopg_adapters_map = adapters_map

        if self._native_inet_types is False:
            string_types = _import_gaussdb_submodule("types.string")
            adapters_map.register_loader("inet", string_types.TextLoader)
            adapters_map.register_loader("cidr", string_types.TextLoader)

        json_types = _import_gaussdb_submodule("types.json")
        if self._json_deserializer:
            json_types.set_json_loads(self._json_deserializer, adapters_map)
        if self._json_serializer:
            json_types.set_json_dumps(self._json_serializer, adapters_map)

    @classmethod
    def import_dbapi(cls):
        return import_gaussdb()

    def create_connect_args(self, url):
        args, kwargs = super().create_connect_args(url)
        _resolve_ca_kwarg(kwargs)
        # GaussDB/openGauss silently turns binary-format bound parameters
        # (int, date, ...) into NULL. ClientCursor interpolates parameters
        # client-side (psycopg2 semantics), which is fully correct against
        # this server family.
        kwargs.setdefault("cursor_factory", self.dbapi.ClientCursor)
        return args, kwargs

    def _type_info_fetch(self, connection, name):
        types = _import_gaussdb_submodule("types")
        return types.TypeInfo.fetch(connection.connection.driver_connection, name)

    def initialize(self, connection):
        # Skip PGDialect_psycopg.initialize(), whose HSTORE registration
        # imports psycopg directly, while preserving its behavior with the
        # corresponding GaussDB module.
        super(PGDialect_psycopg, self).initialize(connection)

        if not self.insert_returning:
            self.insert_executemany_returning = False

        if self.use_native_hstore:
            info = self._type_info_fetch(connection, "hstore")
            self._has_native_hstore = info is not None
            if self._has_native_hstore:
                hstore = _import_gaussdb_submodule("types.hstore")
                hstore.register_hstore(info, self._psycopg_adapters_map)
                hstore.register_hstore(info, connection.connection.driver_connection)

    @classmethod
    def get_async_dialect_cls(cls, url):
        raise NotImplementedError("The official GaussDB SQLAlchemy dialect currently supports synchronous engines only")

    @util.memoized_property
    def _psycopg_Json(self):
        return _import_gaussdb_submodule("types.json").Json

    @util.memoized_property
    def _psycopg_Jsonb(self):
        return _import_gaussdb_submodule("types.json").Jsonb

    @util.memoized_property
    def _psycopg_TransactionStatus(self):
        return _import_gaussdb_submodule("pq").TransactionStatus

    @util.memoized_property
    def _psycopg_Range(self):
        return _import_gaussdb_submodule("types.range").Range

    @util.memoized_property
    def _psycopg_Multirange(self):
        return _import_gaussdb_submodule("types.multirange").Multirange

    def _get_server_version_info(self, connection):
        return _get_server_version_info(connection)

    def on_connect(self):
        parent = super().on_connect()
        adapt = _import_gaussdb_submodule("adapt")

        class _TolerantBoolLoader(adapt.Loader):
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
    ``driver: psycopg2`` is configured explicitly. macOS defaults to the
    pure-Python ``pg8000`` path because no compatible native
    GaussDB/openGauss libpq is available.
    """

    name = "gaussdb"
    driver = "psycopg2"
    supports_statement_cache = True

    def create_connect_args(self, url):
        args, kwargs = super().create_connect_args(url)
        _resolve_ca_kwarg(kwargs)
        return args, kwargs

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


def _resolve_ca_kwarg(kwargs: dict) -> None:
    """Point ``sslrootcert`` at a file, in place, for the libpq-based drivers."""
    from ._ca_cert import as_path

    cert = kwargs.get("sslrootcert")
    if cert:
        kwargs["sslrootcert"] = as_path(cert)


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

    from ._ca_cert import is_inline_pem

    def _context() -> "ssl_module.SSLContext":
        # An uploaded certificate never needs to touch the disk here: pg8000
        # takes an SSLContext, and one can be built from the PEM text itself.
        if is_inline_pem(sslrootcert):
            return ssl_module.create_default_context(cadata=sslrootcert)
        return ssl_module.create_default_context(cafile=sslrootcert)

    mode = (sslmode or "prefer").strip().lower()
    if mode == "disable":
        return False
    if mode in ("allow", "prefer"):
        return None
    if mode == "require":
        if not sslrootcert:
            return True
        context = _context()
        context.check_hostname = False
        return context
    if mode in ("verify-ca", "verify-full"):
        if not sslrootcert:
            raise ValueError(f"sslmode={mode} requires sslrootcert to point at the CA certificate")
        context = _context()
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
