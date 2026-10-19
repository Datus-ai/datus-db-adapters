"""Shared execution and Agent context URLs for Trino."""

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
