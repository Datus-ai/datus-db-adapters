# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

"""Credential-free Agent integration hooks for BigQuery."""

from typing import Any, Dict, Tuple
from urllib.parse import quote, unquote, urlencode, urlparse


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


def build_bigquery_uri(db_config) -> str:
    """Build a stable datasource URI without embedding credentials."""
    project = _config_value(db_config, "project", "catalog")
    if not project:
        raise ValueError("BigQuery project is required")
    dataset = _config_value(db_config, "dataset", "database")
    query = {}
    if location := _config_value(db_config, "location"):
        query["location"] = location
    if billing_project := _config_value(db_config, "billing_project_id"):
        query["billing_project_id"] = billing_project
    uri = f"bigquery://{quote(project, safe='')}"
    if dataset:
        uri += f"/{quote(dataset, safe='')}"
    if query:
        uri += "?" + urlencode(query)
    return uri


def resolve_bigquery_context(db_config, uri: str) -> Tuple[str, str, str, str]:
    """Resolve ``(db_type, project, dataset, schema)`` for Datus."""
    parsed = urlparse(uri or "")
    project = unquote(parsed.netloc) or _config_value(db_config, "project", "catalog")
    dataset = unquote(parsed.path.lstrip("/")) or _config_value(db_config, "dataset", "database")
    return "bigquery", project, dataset, ""


def parse_bigquery_identifier(full_table_name: str) -> Dict[str, str]:
    """Parse table, dataset.table, or project.dataset.table identifiers."""
    parts = _split_identifier(full_table_name)
    result = {"catalog_name": "", "database_name": "", "schema_name": "", "table_name": ""}
    if not parts:
        return result
    if len(parts) > 3:
        raise ValueError(f"Invalid BigQuery table identifier: {full_table_name}")
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

    # BigQuery commonly quotes the full project.dataset.table path with one
    # backtick pair. In that form the inner dots remain namespace separators.
    if text.startswith("`") and text.endswith("`") and text.count("`") == 2:
        text = text[1:-1]

    parts: list[str] = []
    current: list[str] = []
    quote_char = ""
    pairs = {"`": "`", '"': '"', "[": "]"}
    index = 0
    while index < len(text):
        char = text[index]
        if quote_char:
            if char == quote_char:
                if index + 1 < len(text) and text[index + 1] == quote_char:
                    current.append(quote_char)
                    index += 2
                    continue
                quote_char = ""
            elif char == "\\" and quote_char == "`" and index + 1 < len(text) and text[index + 1] == "`":
                current.append("`")
                index += 2
                continue
            else:
                current.append(char)
            index += 1
            continue
        if char in pairs:
            quote_char = pairs[char]
        elif char == ".":
            value = "".join(current).strip()
            if not value:
                raise ValueError(f"Invalid BigQuery table identifier: {identifier}")
            parts.append(value)
            current = []
        else:
            current.append(char)
        index += 1
    if quote_char:
        raise ValueError(f"Invalid BigQuery table identifier: {identifier}")
    value = "".join(current).strip()
    if not value:
        raise ValueError(f"Invalid BigQuery table identifier: {identifier}")
    parts.append(value)
    return parts
