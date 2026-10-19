# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

"""Athena hooks for the generic Datus datasource and namespace APIs."""

import re
from urllib.parse import quote, unquote, urlencode, urlsplit

from .config import AthenaConfig


def _uri_config(config, **overrides) -> AthenaConfig:
    """Apply the connector's validation and normalization to URI/context fields."""
    extra = getattr(config, "extra", None) or {}
    values = {}
    for name in ("catalog", "database", "workgroup", "region", "profile_name", "ca_bundle", "aws"):
        value = getattr(config, name, None)
        if value is None or value == "":
            value = extra.get(name)
        if value is not None:
            values[name] = value
    values.update(overrides)
    return AthenaConfig.model_validate(values)


def build_athena_uri(config) -> str:
    """A credential-free URI for datasource identity and context."""
    normalized = _uri_config(config)
    params = {"workgroup": normalized.workgroup}
    for name in ("region", "profile_name"):
        if value := getattr(normalized, name):
            params[name] = value
    return (
        f"athena://{quote(normalized.catalog, safe='')}/{quote(normalized.database or '', safe='')}?{urlencode(params)}"
    )


def resolve_athena_context(config, uri: str) -> tuple[str, str, str, str]:
    parsed = urlsplit(uri)
    overrides = {
        name: value
        for name, value in (
            ("catalog", unquote(parsed.netloc)),
            ("database", unquote(parsed.path.lstrip("/"))),
        )
        if value
    }
    normalized = _uri_config(config, **overrides)
    return "athena", normalized.catalog, normalized.database or "", ""


def parse_athena_identifier(value: str) -> dict[str, str]:
    result = dict(catalog_name="", database_name="", schema_name="", table_name="")
    if not value or not value.strip():
        return result
    value = value.strip()
    # Datus's SQL extractor preserves missing leading namespace coordinates
    # ('.database.table' / '..table'). These are logical identifiers, not SQL.
    padding = len(value) - len(value.lstrip("."))
    value = value[padding:]
    if not value or padding > 2:
        raise ValueError("Invalid Athena table identifier: missing table or too many namespace levels")
    # Athena has both Trino double quotes and Hive DDL backticks. Parse only
    # identifier components here, never a SQL statement or an alias.
    pattern = re.compile(r'\s*(?:"((?:[^"]|"")*)"|`((?:[^`]|``)*)`|([^\W\d][\w$]*))\s*')
    parts = []
    position = 0
    while position < len(value):
        match = pattern.match(value, position)
        if not match:
            raise ValueError(f"Invalid Athena table identifier: {value}")
        quoted, backtick, bare = match.groups()
        part = (
            quoted.replace('""', '"')
            if quoted is not None
            else backtick.replace("``", "`")
            if backtick is not None
            else bare
        )
        if not part:
            raise ValueError(f"Invalid Athena table identifier: {value}")
        parts.append(part)
        position = match.end()
        if position == len(value):
            break
        if value[position] != "." or position + 1 == len(value):
            raise ValueError(f"Invalid Athena table identifier: {value}")
        position += 1
    if len(parts) + padding > 3:
        raise ValueError(f"Invalid Athena table identifier: {value}")
    result["table_name"] = parts[-1]
    if len(parts) >= 2:
        result["database_name"] = parts[-2]
    if len(parts) == 3:
        result["catalog_name"] = parts[0]
    return result


def get_athena_sql_generation_notes() -> str:
    return (
        "Athena uses catalog.database.table with no additional schema level. "
        "Use double quotes for SELECT identifiers and backticks for Hive DDL identifiers. "
        "Queries use the configured Workgroup. LIMIT bounds returned rows, not scanned bytes; "
        "enforce scan budgets in the Workgroup. UPDATE/DELETE/MERGE require supported Iceberg tables. "
        "Transactions, session SET/USE and generic dataframe migration are not supported by this adapter."
    )
