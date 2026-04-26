# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""URI builder and context resolver for Hologres."""

from typing import Tuple

from datus_postgresql.handlers import build_postgresql_uri, resolve_postgresql_context


def build_hologres_uri(db_config) -> str:
    """Build a PostgreSQL-protocol SQLAlchemy URI for Hologres."""
    return build_postgresql_uri(db_config)


def resolve_hologres_context(db_config, uri: str) -> Tuple[str, str, str, str]:
    """Resolve context while preserving PostgreSQL-compatible fields.

    The first tuple slot is the adapter identity used by the existing registry
    context model; catalog, database, and schema are resolved through the
    PostgreSQL-compatible path unchanged.
    """
    _, catalog, database, schema = resolve_postgresql_context(db_config, uri)
    return "hologres", catalog, database, schema
