# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Generic Agent integration hooks exposed by the Hologres adapter."""

from typing import Any, Dict, Tuple
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

from .config import normalize_hologres_endpoint


def _clean(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "get_secret_value"):
        value = value.get_secret_value()
    return str(value).strip()


def _config_value(db_config, *names: str) -> str:
    for name in names:
        value = _clean(getattr(db_config, name, None))
        if value:
            return value
    extra = getattr(db_config, "extra", None)
    if isinstance(extra, dict):
        for name in names:
            value = _clean(extra.get(name))
            if value:
                return value
    return ""


def build_hologres_uri(db_config) -> str:
    """Build a credential-free URI for datasource identity and context."""
    host = _config_value(db_config, "host")
    database = _config_value(db_config, "database")
    if not host:
        raise ValueError("Hologres host is required")
    if not database:
        raise ValueError("Hologres database is required")

    host, port = normalize_hologres_endpoint(host, _config_value(db_config, "port"))
    schema = _config_value(db_config, "schema_name", "schema") or "public"
    sslmode = _config_value(db_config, "sslmode") or "prefer"
    database_path = quote(database, safe="")
    return f"hologres://{host}:{port}/{database_path}?{urlencode({'schema': schema, 'sslmode': sslmode})}"


def resolve_hologres_context(db_config, uri: str) -> Tuple[str, str, str, str]:
    parsed = urlparse(uri)
    params = parse_qs(parsed.query)
    database = unquote(parsed.path.lstrip("/")) or _config_value(db_config, "database")
    schema = (params.get("schema") or [""])[0] or _config_value(db_config, "schema_name", "schema") or "public"
    return "hologres", "", database, schema


def parse_hologres_identifier(full_table_name: str) -> Dict[str, str]:
    """Parse table, schema.table, or database.schema.table identifiers."""
    parts = _split_identifier(full_table_name)
    result = {"catalog_name": "", "database_name": "", "schema_name": "", "table_name": ""}
    if not parts:
        return result
    if len(parts) > 3:
        raise ValueError(f"Invalid Hologres table identifier: {full_table_name}")

    result["table_name"] = parts[-1]
    if len(parts) >= 2:
        result["schema_name"] = parts[-2]
    if len(parts) == 3:
        result["database_name"] = parts[0]
    return result


def _split_identifier(identifier: str) -> list[str]:
    text = (identifier or "").strip()
    if not text:
        return []

    parts: list[str] = []
    current: list[str] = []
    quote = ""
    pairs = {"`": "`", '"': '"', "[": "]"}
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            if char == quote:
                if index + 1 < len(text) and text[index + 1] == quote:
                    current.append(quote)
                    index += 2
                    continue
                quote = ""
            else:
                current.append(char)
            index += 1
            continue
        if char in pairs:
            quote = pairs[char]
        elif char == ".":
            value = "".join(current).strip()
            if not value:
                raise ValueError(f"Invalid Hologres table identifier: {identifier}")
            parts.append(value)
            current = []
        else:
            current.append(char)
        index += 1
    if quote:
        raise ValueError(f"Invalid Hologres table identifier: {identifier}")
    value = "".join(current).strip()
    if not value:
        raise ValueError(f"Invalid Hologres table identifier: {identifier}")
    parts.append(value)
    return parts


HOLOGRES_SQL_GENERATION_NOTES = """
Hologres SQL rules:
- Use PostgreSQL 11-compatible SQL and double quotes for identifiers.
- Use schema.table inside the connected database, or database.schema.table when a fully qualified name is needed.
- Prefer TEXT to unbounded VARCHAR for string columns.
- Primary-key columns must be NOT NULL. If a distribution_key is configured for a primary-key table,
  it must contain the primary key or a subset of its columns.
- Use Hologres table properties such as orientation, distribution_key, clustering_key, and
  event_time_column in a WITH (...) clause.
- Foreign tables are read-only mappings to external systems and do not use Hologres internal-table properties.
- Do not generate UNIQUE, CHECK, or foreign-key constraints for Hologres internal tables.
- Do not mix DDL and DML in one transaction. Multi-statement DML transactions are disabled by default.
""".strip()
