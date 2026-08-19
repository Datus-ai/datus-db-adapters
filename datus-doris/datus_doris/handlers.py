# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Generic Agent integration hooks exposed by the Doris adapter.

Doris addresses objects as ``catalog.database.table`` (``DorisParser.g4``:
``USE (catalog=identifier DOT)? database=identifier``), so the context tuple
carries a catalog and leaves the schema level empty. ``internal`` is the
built-in catalog holding Doris-managed tables
(``InternalCatalog.INTERNAL_CATALOG_NAME``).
"""

from typing import Any, Dict, Tuple
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

DEFAULT_CATALOG = "internal"
DEFAULT_PORT = 9030

# ``def`` is the placeholder MySQL protocol clients report for TABLE_CATALOG.
# Doris returns it through information_schema, so treat it as "unset".
_PLACEHOLDER_CATALOGS = frozenset({"def"})


def _clean(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "get_secret_value"):
        value = value.get_secret_value()
    return str(value).strip()


def _config_value(db_config, *names: str) -> str:
    """Read the first non-empty attribute, falling back to an ``extra`` mapping."""
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


def _port_value(db_config) -> int:
    raw = _config_value(db_config, "port")
    if not raw:
        return DEFAULT_PORT
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid Doris port: {raw}") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"Doris port must be between 1 and 65535: {port}")
    return port


def _normalize_catalog(catalog: str) -> str:
    catalog = _clean(catalog)
    if not catalog or catalog.lower() in _PLACEHOLDER_CATALOGS:
        return DEFAULT_CATALOG
    return catalog


def build_doris_uri(db_config) -> str:
    """Build a credential-free URI for datasource identity and context.

    The catalog cannot live in the URI path (the single path segment is the
    database), so it travels as a query parameter. The result is stable for a
    given config and never contains the password.
    """
    host = _config_value(db_config, "host")
    if not host:
        raise ValueError("Doris host is required")
    if "://" in host:
        raise ValueError("Doris host must not include a URI scheme")

    port = _port_value(db_config)
    catalog = _normalize_catalog(_config_value(db_config, "catalog"))
    database = _config_value(db_config, "database")

    query = {"catalog": catalog}
    database_path = quote(database, safe="") if database else ""
    return f"doris://{host}:{port}/{database_path}?{urlencode(query)}"


def resolve_doris_context(db_config, uri: str) -> Tuple[str, str, str, str]:
    """Resolve ``(db_type, catalog, database, schema)`` from a URI plus config.

    Doris has no schema level between database and table, so the schema slot is
    always empty — matching the ``{"catalog", "database"}`` capability set.
    """
    parsed = urlparse(uri or "")
    params = parse_qs(parsed.query)

    catalog = (params.get("catalog") or [""])[0] or _config_value(db_config, "catalog")
    database = unquote(parsed.path.lstrip("/")) or _config_value(db_config, "database")
    return "doris", _normalize_catalog(catalog), database, ""


def parse_doris_identifier(full_table_name: str) -> Dict[str, str]:
    """Parse ``table``, ``database.table``, or ``catalog.database.table``.

    Doris quotes identifiers with backticks; double quotes and square brackets
    are accepted defensively so an identifier copied from another dialect still
    parses instead of splitting on a dot inside the quoted region.
    """
    parts = _split_identifier(full_table_name)
    result = {"catalog_name": "", "database_name": "", "schema_name": "", "table_name": ""}
    if not parts:
        return result
    if len(parts) > 3:
        raise ValueError(f"Invalid Doris table identifier: {full_table_name}")

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
    quote_char = ""
    pairs = {"`": "`", '"': '"', "[": "]"}
    index = 0
    while index < len(text):
        char = text[index]
        if quote_char:
            if char == quote_char:
                # A doubled quote inside a quoted region is an escaped quote.
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
                raise ValueError(f"Invalid Doris table identifier: {identifier}")
            parts.append(value)
            current = []
        else:
            current.append(char)
        index += 1
    if quote_char:
        raise ValueError(f"Invalid Doris table identifier: {identifier}")
    value = "".join(current).strip()
    if not value:
        raise ValueError(f"Invalid Doris table identifier: {identifier}")
    parts.append(value)
    return parts
