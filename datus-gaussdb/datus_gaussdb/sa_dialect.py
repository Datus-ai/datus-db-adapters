# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""SQLAlchemy dialect for GaussDB on top of the official ``gaussdb`` driver.

The driver is a psycopg3 fork with the module tree renamed psycopg->gaussdb.
SQLAlchemy's built-in ``postgresql+psycopg`` dialect hard-imports ``psycopg``
submodules in ~15 places; rather than reimplementing the dialect, the
(API-identical) ``gaussdb`` module tree is aliased into ``sys.modules`` under
the ``psycopg`` names before the dialect first touches them.

URL form: ``gaussdb+psycopg://user:password@host:port/database``
"""

import sys
import types

from sqlalchemy.dialects import registry
from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg

from ._libpq import import_gaussdb

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
        # GaussDB's version() returns "gaussdb (GaussDB Kernel 503...)" /
        # "(openGauss 7.0...)", which the parent regex (expects
        # "PostgreSQL X.Y") cannot parse. The PG compatibility level is
        # reported via server_version instead (typically 9.2.x).
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
