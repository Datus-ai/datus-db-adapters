# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from .config import PostgreSQLConfig
from .connector import PostgreSQLConnector
from .relational_backend import PostgresRelationalBackend
from .vector_backend import PgVectorBackend
from .storage_ddl import render_all_ddl, render_relational_ddl, render_vector_ddl

__version__ = "0.1.0"
__all__ = [
    "PostgreSQLConnector",
    "PostgreSQLConfig",
    "PostgresRelationalBackend",
    "PgVectorBackend",
    "render_all_ddl",
    "render_relational_ddl",
    "render_vector_ddl",
    "register",
]


def register():
    """Register PostgreSQL connector with Datus registry."""
    from datus.tools.db_tools import connector_registry
    from datus.storage.db_manager import register_backend as register_relational_backend
    from datus.storage.backends.vector.registry import register_vector_backend

    connector_registry.register("postgresql", PostgreSQLConnector, config_class=PostgreSQLConfig)
    register_relational_backend("postgresql", PostgresRelationalBackend)
    register_vector_backend("pgvector", PgVectorBackend)
