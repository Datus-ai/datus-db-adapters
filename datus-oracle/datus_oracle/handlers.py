# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""URI builder and context resolver for Oracle."""

from typing import Optional, Tuple, Union

from sqlalchemy.engine.url import URL


def _clean_str(value: Optional[Union[str, int]]) -> str:
    if value is None:
        return ""
    return str(value).strip()


def build_oracle_uri(db_config) -> str:
    """Build a SQLAlchemy URI for python-oracledb Thin mode.

    Exactly one of ``service_name``, ``sid`` or ``dsn`` is set (validated by
    OracleConfig). The service/PDB is a connection target only.
    """
    username = _clean_str(db_config.username)
    password = _clean_str(db_config.password)
    dsn = _clean_str(getattr(db_config, "dsn", None))

    if dsn:
        # SQLAlchemy does not percent-decode the URL host. Pass the opaque
        # Easy Connect string, TNS alias or descriptor through the query so
        # create_connect_args hands the original DSN to python-oracledb.
        return URL.create(
            drivername="oracle+oracledb",
            username=username or None,
            password=password or None,
            query={"dsn": dsn},
        ).render_as_string(hide_password=False)

    sid = _clean_str(getattr(db_config, "sid", None))
    service_name = _clean_str(getattr(db_config, "service_name", None))
    url = URL.create(
        drivername="oracle+oracledb",
        username=username or None,
        password=password or None,
        host=_clean_str(db_config.host) or None,
        port=int(db_config.port) if _clean_str(db_config.port) else None,
        database=sid or None,
        query={"service_name": service_name} if service_name else {},
    )
    # str(URL) masks the password; the connector needs the real one
    return url.render_as_string(hide_password=False)


def resolve_oracle_context(db_config, uri: str) -> Tuple[str, str, str, str]:
    """Resolve (dialect, catalog, database, schema) for Oracle.

    Oracle exposes a single schema namespace: no catalog, no database level.
    The default schema is the configured one, else the connecting user's
    schema (Oracle folds unquoted user names to upper case).
    """
    schema = getattr(db_config, "schema_name", None)
    if not isinstance(schema, str) or not schema.strip():
        # dict-style configs may carry the raw "schema" key; guard against
        # pydantic's deprecated BaseModel.schema classmethod
        schema = getattr(db_config, "schema", None)
    if not isinstance(schema, str) or not schema.strip():
        return "oracle", "", "", _clean_str(db_config.username).upper()
    return "oracle", "", "", schema.strip()
