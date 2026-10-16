# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

"""Credential-free routing URI and HANA's two-part identifier contract."""

import re

_IDENTIFIER_PART = re.compile(r'\s*("(?:[^"]|"")*"|(?:[^\W\d]|_)[\w$%*]*|[%*])\s*')


def quote_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def parse_hana_identifier(value: str) -> dict[str, str]:
    names = []
    position = 0
    while position < len(value):
        match = _IDENTIFIER_PART.match(value, position)
        if match is None:
            raise ValueError("Expected a HANA identifier")
        raw = match.group(1)
        name = raw[1:-1].replace('""', '"') if raw.startswith('"') else raw.upper()
        if not name:
            raise ValueError("Empty HANA identifier")
        names.append(name)
        position = match.end()
        if position == len(value):
            break
        if value[position] != "." or position + 1 == len(value):
            raise ValueError("Expected a dot between HANA identifiers")
        position += 1
    if not 1 <= len(names) <= 2:
        raise ValueError("HANA object names must be table or schema.table")
    return {
        "catalog_name": "",
        "database_name": "",
        "schema_name": names[0] if len(names) == 2 else "",
        "table_name": names[-1],
    }


def build_hana_uri(config) -> str:
    # DBManager passes generic DbConfig; adapter-specific values live in extra.
    extra = getattr(config, "extra", None) or {}
    host = getattr(config, "host", None) or "userstore"
    port = getattr(config, "port", None) or extra.get("port") or 39041
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"hana://{host}:{int(port)}"


def resolve_hana_context(config, uri: str) -> tuple[str, str, str, str]:
    extra = getattr(config, "extra", None) or {}
    schema = (
        getattr(config, "schema_name", None)
        or getattr(config, "schema", None)
        or extra.get("schema")
        or getattr(config, "username", None)
        or ""
    )
    return "hana", "", "", str(schema)
