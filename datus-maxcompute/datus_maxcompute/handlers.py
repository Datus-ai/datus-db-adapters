# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Generic integration hooks exposed by the MaxCompute adapter."""

from typing import Any, Dict, Tuple
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


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


def build_maxcompute_uri(db_config) -> str:
    """Build a credential-free URI used for datasource identity and context."""
    project = _config_value(db_config, "project", "database")
    endpoint = _config_value(db_config, "endpoint")
    parsed_endpoint = urlparse(endpoint)
    if parsed_endpoint.username is not None or parsed_endpoint.password is not None:
        raise ValueError("MaxCompute endpoint must not contain user information")
    endpoint = urlunparse(
        (
            parsed_endpoint.scheme,
            parsed_endpoint.netloc,
            parsed_endpoint.path,
            parsed_endpoint.params,
            "",
            "",
        )
    )
    schema = _config_value(db_config, "schema_name", "schema")
    query = {"endpoint": endpoint}
    if schema:
        query["schema"] = schema
    return f"maxcompute://{project}?{urlencode(query)}"


def resolve_maxcompute_context(db_config, uri: str) -> Tuple[str, str, str, str]:
    parsed = urlparse(uri)
    params = parse_qs(parsed.query)
    project = parsed.netloc or _config_value(db_config, "project", "database")
    schema = (params.get("schema") or [""])[0]
    if not schema:
        schema = _config_value(db_config, "schema_name", "schema")
    return "maxcompute", "", project, schema


def parse_maxcompute_identifier(full_table_name: str) -> Dict[str, str]:
    """Parse Datus identifiers for both MaxCompute namespace models.

    Two components always mean ``project.table``. Three components mean
    ``project.schema.table``; ``schema.table`` is intentionally not inferred.
    """
    parts = _split_identifier(full_table_name)
    result = {"catalog_name": "", "database_name": "", "schema_name": "", "table_name": ""}
    if not parts:
        return result
    if len(parts) > 3:
        raise ValueError(f"Invalid MaxCompute table identifier: {full_table_name}")
    result["table_name"] = parts[-1]
    if len(parts) >= 2:
        result["database_name"] = parts[0]
    if len(parts) == 3:
        result["schema_name"] = parts[1]
    return result


def _split_identifier(identifier: str) -> list[str]:
    text = (identifier or "").strip()
    if not text:
        return []

    parts: list[str] = []
    current: list[str] = []
    quote = ""
    pairs = {"`": "`", '"': '"', "[": "]"}
    for char in text:
        if quote:
            if char == quote:
                quote = ""
            else:
                current.append(char)
            continue
        if char in pairs:
            quote = pairs[char]
        elif char == ".":
            value = "".join(current).strip()
            if not value:
                raise ValueError(f"Invalid MaxCompute table identifier: {identifier}")
            parts.append(value)
            current = []
        else:
            current.append(char)
    if quote:
        raise ValueError(f"Invalid MaxCompute table identifier: {identifier}")
    value = "".join(current).strip()
    if not value:
        raise ValueError(f"Invalid MaxCompute table identifier: {identifier}")
    parts.append(value)
    return parts


MAXCOMPUTE_SQL_GENERATION_NOTES = """
MaxCompute namespace rules:
- Use project.table for a two-level project.
- Use project.schema.table for a schema-enabled three-level project.
- Do not infer schema.table from a two-part identifier; Datus treats it as project.table.
- Do not generate transaction control statements. UPDATE and DELETE require a compatible
  transactional table and may be rejected by MaxCompute for ordinary tables.
""".strip()
