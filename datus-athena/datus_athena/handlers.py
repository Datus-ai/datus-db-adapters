# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

"""Athena hooks for the generic Datus datasource and namespace APIs."""

import re
from urllib.parse import quote, unquote, urlencode, urlsplit

from .config import AthenaConfig


def _value(config, name: str, default: str = "") -> str:
    value = getattr(config, name, None)
    if not value:
        value = (getattr(config, "extra", None) or {}).get(name)
    return str(value) if value else default


def build_athena_uri(config) -> str:
    """A credential-free URI for datasource identity and context."""
    catalog = _value(config, "catalog", "AwsDataCatalog")
    database = _value(config, "database")
    params = {"workgroup": _value(config, "workgroup", "primary")}
    identity_values = {name: _value(config, name) or None for name in ("region", "profile_name", "ca_bundle")}
    nested = getattr(config, "aws", None)
    if nested is None:
        nested = (getattr(config, "extra", None) or {}).get("aws")
    if nested is not None:
        identity_values["aws"] = nested
    identity = AthenaConfig.model_validate(identity_values)
    for name in ("region", "profile_name"):
        if value := getattr(identity, name):
            params[name] = value
    return f"athena://{quote(catalog, safe='')}/{quote(database, safe='')}?{urlencode(params)}"


def resolve_athena_context(config, uri: str) -> tuple[str, str, str, str]:
    parsed = urlsplit(uri)
    return (
        "athena",
        unquote(parsed.netloc) or _value(config, "catalog", "AwsDataCatalog"),
        unquote(parsed.path.lstrip("/")) or _value(config, "database"),
        "",
    )


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
