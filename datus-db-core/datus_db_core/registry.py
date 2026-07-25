# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import threading
from typing import Any, Callable, Dict, Optional, Set, Type

from datus_db_core.exceptions import DatusDbException, ErrorCode
from datus_db_core.logging import get_logger

logger = get_logger(__name__)


class AdapterMetadata:
    """Metadata for a database adapter."""

    def __init__(
        self,
        db_type: str,
        connector_class: Type,
        config_class: Optional[Type] = None,
        display_name: Optional[str] = None,
        parser_dialect: Optional[str] = None,
        identifier_parser: Optional[Callable] = None,
        sql_generation_notes: Optional[Any] = None,
    ):
        self.db_type = db_type
        self.connector_class = connector_class
        self.config_class = config_class
        self.display_name = display_name or db_type.capitalize()
        self.parser_dialect = parser_dialect
        self.identifier_parser = identifier_parser
        self.sql_generation_notes = sql_generation_notes

    def get_config_fields(self) -> Dict[str, Dict[str, Any]]:
        if not self.config_class:
            return {}
        try:
            from pydantic import BaseModel

            if not issubclass(self.config_class, BaseModel):
                return {}

            fields_info = {}
            for field_name, field_info in self.config_class.model_fields.items():
                field_data = {
                    "required": field_info.is_required(),
                    "default": field_info.default if not field_info.is_required() else None,
                    "description": field_info.description or "",
                    "type": (
                        field_info.annotation.__name__
                        if hasattr(field_info.annotation, "__name__")
                        else str(field_info.annotation)
                    ),
                }
                if hasattr(field_info, "json_schema_extra") and field_info.json_schema_extra:
                    field_data.update(field_info.json_schema_extra)
                fields_info[field_name] = field_data
            return fields_info
        except Exception as e:
            logger.debug(f"Failed to extract config fields for {self.db_type}: {e}")
            return {}


class ConnectorRegistry:
    """Central registry for database connectors."""

    _connectors: Dict[str, Type] = {}
    _factories: Dict[str, Callable] = {}
    _metadata: Dict[str, AdapterMetadata] = {}
    _capabilities: Dict[str, Set[str]] = {}
    _uri_builders: Dict[str, Callable] = {}
    _context_resolvers: Dict[str, Callable] = {}
    _initialized: bool = False
    _lock: threading.RLock = threading.RLock()

    _DIALECT_ALIASES: Dict[str, str] = {
        "postgres": "postgresql",
        "sqlserver": "mssql",
    }

    @classmethod
    def _resolve_key(cls, db_type: str) -> str:
        key = db_type.lower()
        return cls._DIALECT_ALIASES.get(key, key)

    @classmethod
    def register(
        cls,
        db_type: str,
        connector_class: Type,
        factory: Optional[Callable] = None,
        config_class: Optional[Type] = None,
        display_name: Optional[str] = None,
        capabilities: Optional[Set[str]] = None,
        uri_builder: Optional[Callable] = None,
        context_resolver: Optional[Callable] = None,
        *,
        parser_dialect: Optional[str] = None,
        identifier_parser: Optional[Callable] = None,
        sql_generation_notes: Optional[Any] = None,
    ):
        key = cls._resolve_key(db_type)
        with cls._lock:
            cls._connectors[key] = connector_class
            if factory:
                cls._factories[key] = factory
            if capabilities is not None:
                cls._capabilities[key] = capabilities
            if uri_builder:
                cls._uri_builders[key] = uri_builder
            if context_resolver:
                cls._context_resolvers[key] = context_resolver

            cls._metadata[key] = AdapterMetadata(
                db_type=key,
                connector_class=connector_class,
                config_class=config_class,
                display_name=display_name,
                parser_dialect=parser_dialect,
                identifier_parser=identifier_parser,
                sql_generation_notes=sql_generation_notes,
            )
        logger.debug(f"Registered connector: {db_type} -> {connector_class.__name__}")

    @classmethod
    def create_connector(cls, db_type: str, config):
        key = cls._resolve_key(db_type)

        if key not in cls._connectors:
            cls._try_load_adapter(key)

        if key not in cls._connectors:
            raise DatusDbException(
                ErrorCode.DB_CONNECTION_FAILED,
                message=f"Connector '{db_type}' not found. "
                f"Available connectors: {list(cls._connectors.keys())}. "
                f"For additional databases, install: pip install datus-{key}",
            )

        if key in cls._factories:
            return cls._factories[key](config)

        connector_class = cls._connectors[key]
        return connector_class(config)

    @classmethod
    def _try_load_adapter(cls, db_type: str):
        try:
            module_name = f"datus_{db_type}"
            import importlib

            module = importlib.import_module(module_name)
            if hasattr(module, "register"):
                module.register()
                logger.info(f"Dynamically loaded adapter: {db_type}")
        except ImportError as e:
            if getattr(e, "name", None) == module_name:
                logger.debug(f"No adapter found for: {db_type}")
                return
            raise
        except Exception as e:
            logger.warning(f"Failed to load adapter {db_type}: {e}")

    @classmethod
    def discover_adapters(cls):
        if cls._initialized:
            return
        with cls._lock:
            if cls._initialized:
                return

            try:
                from importlib.metadata import entry_points

                try:
                    adapter_eps = entry_points(group="datus.adapters")
                except TypeError:
                    eps = entry_points()
                    adapter_eps = eps.get("datus.adapters", [])

                for ep in adapter_eps:
                    try:
                        register_func = ep.load()
                        register_func()
                        logger.info(f"Discovered adapter: {ep.name}")
                    except Exception as e:
                        logger.warning(f"Failed to load adapter {ep.name}: {e}")
            except Exception as e:
                logger.warning(f"Entry points discovery failed: {e}")
            finally:
                cls._initialized = True

    @classmethod
    def list_connectors(cls) -> Dict[str, Type]:
        return cls._connectors.copy()

    @classmethod
    def is_registered(cls, db_type: str) -> bool:
        return cls._resolve_key(db_type) in cls._connectors

    @classmethod
    def get_metadata(cls, db_type: str) -> Optional[AdapterMetadata]:
        return cls._metadata.get(cls._resolve_key(db_type))

    @classmethod
    def list_available_adapters(cls) -> Dict[str, AdapterMetadata]:
        cls.discover_adapters()
        return cls._metadata.copy()

    @classmethod
    def register_handlers(
        cls,
        db_type: str,
        capabilities: Optional[Set[str]] = None,
        uri_builder: Optional[Callable] = None,
        context_resolver: Optional[Callable] = None,
        *,
        parser_dialect: Optional[str] = None,
        identifier_parser: Optional[Callable] = None,
        sql_generation_notes: Optional[Any] = None,
    ):
        key = cls._resolve_key(db_type)
        if capabilities is not None:
            cls._capabilities[key] = capabilities
        if uri_builder:
            cls._uri_builders[key] = uri_builder
        if context_resolver:
            cls._context_resolvers[key] = context_resolver
        metadata = cls._metadata.get(key)
        if metadata:
            if parser_dialect is not None:
                metadata.parser_dialect = parser_dialect
            if identifier_parser is not None:
                metadata.identifier_parser = identifier_parser
            if sql_generation_notes is not None:
                metadata.sql_generation_notes = sql_generation_notes

    @classmethod
    def has_capabilities(cls, db_type: str) -> bool:
        return cls._resolve_key(db_type) in cls._capabilities

    @classmethod
    def get_capabilities(cls, db_type: str) -> Set[str]:
        return cls._capabilities.get(cls._resolve_key(db_type), set()).copy()

    @classmethod
    def support_catalog(cls, db_type: str) -> bool:
        return "catalog" in cls._capabilities.get(cls._resolve_key(db_type), set())

    @classmethod
    def support_database(cls, db_type: str) -> bool:
        return "database" in cls._capabilities.get(cls._resolve_key(db_type), set())

    @classmethod
    def support_schema(cls, db_type: str) -> bool:
        return "schema" in cls._capabilities.get(cls._resolve_key(db_type), set())

    @classmethod
    def get_uri_builder(cls, db_type: str) -> Optional[Callable]:
        return cls._uri_builders.get(cls._resolve_key(db_type))

    @classmethod
    def get_context_resolver(cls, db_type: str) -> Optional[Callable]:
        return cls._context_resolvers.get(cls._resolve_key(db_type))

    @classmethod
    def get_parser_dialect(cls, db_type: str) -> Optional[str]:
        metadata = cls.get_metadata(db_type)
        return metadata.parser_dialect if metadata else None

    @classmethod
    def get_identifier_parser(cls, db_type: str) -> Optional[Callable]:
        metadata = cls.get_metadata(db_type)
        return metadata.identifier_parser if metadata else None

    @classmethod
    def get_sql_generation_notes(cls, db_type: str) -> Optional[Any]:
        metadata = cls.get_metadata(db_type)
        return metadata.sql_generation_notes if metadata else None


# Global instance
connector_registry = ConnectorRegistry()
