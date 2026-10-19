# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""URI builder and context resolver for ClickHouse."""

from typing import Optional, Tuple, Union

from sqlalchemy.engine.url import URL, make_url

from .config import connection_port


def _clean_str(value: Optional[Union[str, int]]) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        for item in value:
            if item:
                return str(item).strip()
        return ""
    return str(value).strip()


def _value_or_none(value: Optional[Union[str, int]]) -> Optional[str]:
    cleaned = _clean_str(value)
    return cleaned or None


def _port_or_none(port_value: Optional[Union[str, int]]) -> Optional[int]:
    cleaned = _clean_str(port_value)
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def connection_url(config) -> URL:
    """Use the same route for adapter execution and Agent context URIs."""
    extra = getattr(config, "extra", None) or {}
    protocol = getattr(config, "protocol", None) or extra.get("protocol", "http")
    return URL.create(
        drivername="clickhouse+http",
        username=config.username,
        password=config.password,
        host=_value_or_none(config.host),
        port=connection_port(protocol, _port_or_none(config.port)),
        database=config.database or None,
        query={"protocol": protocol},
    )


def build_clickhouse_uri(db_config) -> str:
    # Public context URIs hide passwords; verification settings stay out of URLs.
    return str(connection_url(db_config))


def resolve_clickhouse_context(db_config, uri: str) -> Tuple[str, str, str, str]:
    url = make_url(uri)
    database = _clean_str(url.database) or _clean_str(db_config.database) or "default"
    schema = _clean_str(getattr(db_config, "schema", None))
    catalog = _clean_str(getattr(db_config, "catalog", None))
    return "clickhouse", catalog, database, schema
