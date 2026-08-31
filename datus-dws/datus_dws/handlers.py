# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Generic Agent integration hooks exposed by the DWS adapter."""

from typing import Any, Dict, Tuple
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

from .config import normalize_dws_endpoint


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


def build_dws_uri(db_config) -> str:
    """Build a credential-free URI for datasource identity and context."""
    host = _config_value(db_config, "host")
    database = _config_value(db_config, "database")
    if not host:
        raise ValueError("DWS host is required")
    if not database:
        raise ValueError("DWS database is required")

    host, port = normalize_dws_endpoint(host, _config_value(db_config, "port"))
    schema = _config_value(db_config, "schema_name", "schema") or "public"
    sslmode = _config_value(db_config, "sslmode") or "prefer"
    database_path = quote(database, safe="")
    # An IPv6 literal must be bracketed or the authority's port is unparseable.
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"dws://{host}:{port}/{database_path}?{urlencode({'schema': schema, 'sslmode': sslmode})}"


def resolve_dws_context(db_config, uri: str) -> Tuple[str, str, str, str]:
    parsed = urlparse(uri)
    params = parse_qs(parsed.query)
    database = unquote(parsed.path.lstrip("/")) or _config_value(db_config, "database")
    schema = (params.get("schema") or [""])[0] or _config_value(db_config, "schema_name", "schema") or "public"
    return "dws", "", database, schema


def parse_dws_identifier(full_table_name: str) -> Dict[str, str]:
    """Parse table, schema.table, or database.schema.table identifiers.

    DWS has no catalog layer above the database, so a fourth part is an error
    rather than something to silently drop.
    """
    parts = _split_identifier(full_table_name)
    result = {"catalog_name": "", "database_name": "", "schema_name": "", "table_name": ""}
    if not parts:
        return result
    if len(parts) > 3:
        raise ValueError(f"Invalid DWS table identifier: {full_table_name}")

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
            else:
                current.append(char)
            index += 1
            continue
        if char in pairs:
            quote_char = pairs[char]
        elif char == ".":
            value = "".join(current).strip()
            if not value:
                raise ValueError(f"Invalid DWS table identifier: {identifier}")
            parts.append(value)
            current = []
        else:
            current.append(char)
        index += 1
    if quote_char:
        raise ValueError(f"Invalid DWS table identifier: {identifier}")
    value = "".join(current).strip()
    if not value:
        raise ValueError(f"Invalid DWS table identifier: {identifier}")
    parts.append(value)
    return parts
