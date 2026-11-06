# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Credential-free Agent integration hooks for Databricks SQL warehouses."""

from typing import Any, Dict, Tuple
from urllib.parse import quote, unquote, urlencode, urlsplit


def _clean(value: Any) -> str:
    if value is None or callable(value):
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


def _http_path(db_config) -> str:
    http_path = _config_value(db_config, "http_path")
    if http_path:
        return http_path
    warehouse_id = _config_value(db_config, "warehouse_id")
    if not warehouse_id:
        raise ValueError("Databricks warehouse_id or http_path is required")
    return f"/sql/1.0/warehouses/{warehouse_id}"


def build_databricks_uri(db_config) -> str:
    """Build a stable datasource URI without embedding credentials."""
    host = _config_value(db_config, "host")
    catalog = _config_value(db_config, "catalog")
    schema = _config_value(db_config, "schema_name", "schema", "database")
    if not host or not catalog or not schema:
        raise ValueError("Databricks host, catalog, and schema are required")
    query = {"http_path": _http_path(db_config)}
    if warehouse_id := _config_value(db_config, "warehouse_id"):
        query["warehouse_id"] = warehouse_id
    if auth_mode := _config_value(db_config, "auth_mode"):
        query["auth_mode"] = auth_mode
    if profile := _config_value(db_config, "profile"):
        query["profile"] = profile
    return f"databricks://{quote(host, safe='')}/{quote(catalog, safe='')}/{quote(schema, safe='')}?{urlencode(query)}"


def resolve_databricks_context(db_config, uri: str) -> Tuple[str, str, str, str]:
    """Resolve ``(db_type, catalog, schema, unused)`` for Datus."""
    parsed = urlsplit(uri or "")
    segments = [unquote(part) for part in parsed.path.split("/") if part]
    catalog = segments[0] if segments else _config_value(db_config, "catalog")
    schema = segments[1] if len(segments) > 1 else _config_value(db_config, "schema_name", "schema", "database")
    return "databricks", catalog, schema, ""


def parse_databricks_identifier(full_table_name: str) -> Dict[str, str]:
    """Parse table, schema.table, or catalog.schema.table identifiers."""
    parts = _split_identifier(full_table_name)
    result = {"catalog_name": "", "database_name": "", "schema_name": "", "table_name": ""}
    if not parts:
        return result
    if len(parts) > 3:
        raise ValueError(f"Invalid Databricks table identifier: {full_table_name}")
    result["table_name"] = parts[-1]
    if len(parts) >= 2:
        result["database_name"] = parts[-2]
    if len(parts) == 3:
        result["catalog_name"] = parts[0]
    return result


def _split_identifier(identifier: str) -> list[str]:
    text = (identifier or "").strip()
    if not text:
        return []
    parts: list[str] = []
    current: list[str] = []
    quoted = False
    index = 0
    while index < len(text):
        char = text[index]
        if char == "`":
            if quoted and index + 1 < len(text) and text[index + 1] == "`":
                current.append("`")
                index += 2
                continue
            quoted = not quoted
            index += 1
            continue
        if char == "." and not quoted:
            value = "".join(current).strip()
            if not value:
                raise ValueError(f"Invalid Databricks table identifier: {identifier}")
            parts.append(value)
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    if quoted:
        raise ValueError(f"Invalid Databricks table identifier: {identifier}")
    value = "".join(current).strip()
    if not value:
        raise ValueError(f"Invalid Databricks table identifier: {identifier}")
    parts.append(value)
    return parts
