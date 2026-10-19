"""Shared execution and Agent context URLs for Trino."""

import re
from urllib.parse import quote_plus, unquote_plus

from sqlalchemy.engine import URL, make_url


def connection_url(config) -> URL:
    extra = getattr(config, "extra", None) or {}
    scheme = getattr(config, "http_scheme", None) or extra.get("http_scheme", "http")
    if scheme not in ("http", "https"):
        raise ValueError("http_scheme must be http or https")
    catalog = getattr(config, "catalog", None) or "hive"
    schema = getattr(config, "schema_name", None) or extra.get("schema_name", "default")
    return URL.create(
        "trino",
        username=config.username,
        password=config.password or None,
        host=config.host or "127.0.0.1",
        port=int(config.port) if config.port else 8080,
        database=f"{quote_plus(catalog)}/{quote_plus(schema)}",
        query={"http_scheme": scheme},
    )


def build_trino_uri(db_config) -> str:
    # Verification settings stay out of public context URLs; passwords are masked.
    return str(connection_url(db_config))


def resolve_trino_context(db_config, uri: str) -> tuple[str, str, str, str]:
    parts = (make_url(uri).database or "").split("/", 1)
    extra = getattr(db_config, "extra", None) or {}
    catalog = unquote_plus(parts[0]) or db_config.catalog or "hive"
    schema = unquote_plus(parts[1]) if len(parts) == 2 else extra.get("schema_name", "default")
    return "trino", catalog, schema, schema


def parse_trino_identifier(value: str) -> dict[str, str]:
    """Parse catalog.schema.table and the Agent's missing-prefix scope notation."""
    result = dict(catalog_name="", database_name="", schema_name="", table_name="")
    value = (value or "").strip()
    if not value:
        return result
    padding = len(value) - len(value.lstrip("."))
    value = value[padding:]
    pattern = re.compile(r'\s*(?:"((?:[^"]|"")*)"|([^\W\d][\w$]*|\*))\s*')
    parts = []
    position = 0
    while position < len(value):
        match = pattern.match(value, position)
        if match is None:
            raise ValueError("Invalid Trino table identifier")
        quoted, bare = match.groups()
        part = quoted.replace('""', '"') if quoted is not None else bare
        if not part:
            raise ValueError("Empty Trino table identifier")
        parts.append(part)
        position = match.end()
        if position == len(value):
            break
        if value[position] != "." or position + 1 == len(value):
            raise ValueError("Expected a dot between Trino identifiers")
        position += 1
    if not parts or len(parts) + padding > 3:
        raise ValueError("Trino object names must be table, schema.table or catalog.schema.table")
    result["table_name"] = parts[-1]
    if len(parts) >= 2:
        result["schema_name"] = parts[-2]
    if len(parts) == 3:
        result["catalog_name"] = parts[0]
    return result
