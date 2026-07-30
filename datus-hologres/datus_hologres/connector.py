# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import re
from contextvars import ContextVar
from typing import Any, Dict, List, Optional, Set, Union, override

from datus_db_core import TABLE_TYPE, DatusDbException, ErrorCode, SQLType, parse_sql_type
from datus_postgresql import PostgreSQLConnector

from .config import HologresConfig

_DDL_PROPERTY_ORDER = (
    "orientation",
    "distribution_key",
    "clustering_key",
    "event_time_column",
    "bitmap_columns",
    "dictionary_encoding_columns",
    "time_to_live_in_seconds",
    "binlog_level",
    "binlog_ttl",
)
_DDL_PROPERTY_ALIASES = {
    "segment_key": "event_time_column",
    "binlog.level": "binlog_level",
    "binlog.ttl": "binlog_ttl",
}
_DDL_PROPERTY_SET = frozenset((*_DDL_PROPERTY_ORDER, *_DDL_PROPERTY_ALIASES))
_INTEGER_TYPES = frozenset({"SMALLINT", "INT", "INTEGER", "BIGINT", "INT2", "INT4", "INT8"})
_TIME_TYPES = frozenset({"DATE", "TIMESTAMP", "TIMESTAMPTZ", "TIME", "TIMETZ"})
_SIMPLE_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_$]*$")


def _escape_literal(value: str) -> str:
    return value.replace("'", "''")


def _format_identifier_part(value: str) -> str:
    if _SIMPLE_IDENTIFIER_RE.fullmatch(value):
        return value
    escaped = value.replace('"', '""')
    return f'"{escaped}"'


class HologresConnector(PostgreSQLConnector):
    """Datus connector for Alibaba Cloud Hologres."""

    def __init__(self, config: Union[HologresConfig, dict]):
        if isinstance(config, dict):
            config = HologresConfig(**config)
        elif not isinstance(config, HologresConfig):
            raise TypeError(f"config must be HologresConfig or dict, got {type(config)}")
        super().__init__(config)
        # PostgreSQLConnector owns the wire-driver setup. Datus must still route
        # capabilities, parsing, and prompts through the adapter's public dialect.
        self.dialect = "hologres"
        self.config = config
        self._foreign_tables_var: ContextVar[Optional[tuple[str, str, tuple[tuple[str, str], ...]]]] = ContextVar(
            f"hologres_foreign_tables_{id(self)}", default=None
        )

    @override
    def _sys_databases(self) -> Set[str]:
        return super()._sys_databases() | {"postgres"}

    @override
    def _sys_schemas(self) -> Set[str]:
        return super()._sys_schemas() | {
            "hg_internal",
            "hg_recyclebin",
            "hologres",
            "hologres_object_table",
            "hologres_sample",
            "hologres_statistic",
            "hologres_streaming_mv",
        }

    @staticmethod
    def _is_system_schema(schema_name: str) -> bool:
        normalized = (schema_name or "").lower()
        return (
            normalized == "information_schema"
            or normalized.startswith("pg_")
            or normalized.startswith("hg_")
            or normalized.startswith("hologres")
        )

    @override
    def get_schemas(self, catalog_name: str = "", database_name: str = "", include_sys: bool = False) -> List[str]:
        schemas = super().get_schemas(catalog_name, database_name, include_sys=True)
        if include_sys:
            return schemas
        return [schema for schema in schemas if not self._is_system_schema(schema)]

    @staticmethod
    @override
    def _qualify_name(meta, arg_db, arg_schema):
        """Return an unambiguous Hologres table name for the caller's scope."""
        database = meta.get("database_name", "")
        schema = meta.get("schema_name", "")
        table = meta["table_name"]

        if not arg_db and database:
            # Hologres parses two components as schema.table. Once a database
            # prefix is emitted, include the schema even when the caller scoped it.
            return ".".join(_format_identifier_part(part) for part in (database, schema, table) if part)
        if not arg_schema and schema:
            return ".".join(_format_identifier_part(part) for part in (schema, table))
        return _format_identifier_part(table)

    @override
    def identifier(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
    ) -> str:
        """Generate an identifier that round-trips through the adapter parser."""
        database_name = database_name or self.database_name
        schema_name = schema_name or self.schema_name
        return ".".join(_format_identifier_part(part) for part in (database_name, schema_name, table_name) if part)

    @override
    def _get_metadata(
        self,
        table_type: TABLE_TYPE = "table",
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> List[Dict[str, str]]:
        metadata = super()._get_metadata(table_type, catalog_name, database_name, schema_name)
        if table_type != "table":
            return metadata

        database_name = database_name or self.database_name
        schema_name = schema_name or self.schema_name
        foreign_tables = self._get_foreign_tables(database_name, schema_name)

        existing = {(item["schema_name"], item["table_name"]) for item in metadata}
        for schema, table in foreign_tables:
            if (schema, table) in existing:
                continue
            metadata.append(
                {
                    "identifier": self.identifier(
                        database_name=database_name,
                        schema_name=schema,
                        table_name=table,
                    ),
                    "catalog_name": "",
                    "database_name": database_name,
                    "schema_name": schema,
                    "table_name": table,
                    "table_type": "table",
                }
            )
        return metadata

    def _query_foreign_tables(
        self,
        database_name: str,
        schema_name: str,
    ) -> tuple[tuple[str, str], ...]:
        safe_database = _escape_literal(database_name)
        safe_schema = _escape_literal(schema_name)
        query = f"""
            SELECT foreign_table_schema AS table_schema, foreign_table_name AS table_name
            FROM information_schema.foreign_tables
            WHERE foreign_table_catalog = '{safe_database}'
              AND foreign_table_schema = '{safe_schema}'
        """
        foreign_tables = self._execute_pandas(query, database_name=database_name)
        if foreign_tables.empty:
            foreign_tables = self._execute_pandas(
                query.replace(
                    f"foreign_table_schema = '{safe_schema}'",
                    f"lower(foreign_table_schema) = lower('{safe_schema}')",
                ),
                database_name=database_name,
            )
            self._raise_if_ambiguous_schema(foreign_tables, schema_name)

        result = []
        for _, row in foreign_tables.iterrows():
            schema = str(row["table_schema"])
            table = str(row["table_name"])
            result.append((schema, table))
        return tuple(result)

    def _get_foreign_tables(
        self,
        database_name: str,
        schema_name: str,
    ) -> tuple[tuple[str, str], ...]:
        cached = self._foreign_tables_var.get()
        if cached is not None and cached[:2] == (database_name, schema_name):
            return cached[2]
        return self._query_foreign_tables(database_name, schema_name)

    @override
    def _get_objects_with_ddl(
        self,
        table_type: TABLE_TYPE = "table",
        tables: Optional[List[str]] = None,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> List[Dict[str, str]]:
        if table_type != "table":
            return super()._get_objects_with_ddl(
                table_type,
                tables,
                catalog_name,
                database_name,
                schema_name,
            )

        database_name = database_name or self.database_name
        schema_name = schema_name or self.schema_name
        foreign_tables = self._query_foreign_tables(database_name, schema_name)
        token = self._foreign_tables_var.set((database_name, schema_name, foreign_tables))
        try:
            return super()._get_objects_with_ddl(
                table_type,
                tables,
                catalog_name,
                database_name,
                schema_name,
            )
        finally:
            self._foreign_tables_var.reset(token)

    def _get_table_properties(
        self,
        schema_name: str,
        table_name: str,
        database_name: str = "",
    ) -> Dict[str, str]:
        database_name = database_name or self.database_name
        safe_schema = _escape_literal(schema_name)
        safe_table = _escape_literal(table_name)
        result = self._execute_pandas(
            f"""
            SELECT property_key, property_value
            FROM hologres.hg_table_properties
            WHERE table_namespace = '{safe_schema}'
              AND table_name = '{safe_table}'
            """,
            database_name=database_name,
        )
        properties: Dict[str, str] = {}
        for _, row in result.iterrows():
            key = str(row["property_key"]).lower()
            value = row["property_value"]
            if key not in _DDL_PROPERTY_SET or value is None:
                continue
            normalized_key = _DDL_PROPERTY_ALIASES.get(key, key)
            normalized_value = str(value)
            if normalized_key == "time_to_live_in_seconds" and normalized_value == "3153600000":
                continue
            properties[normalized_key] = normalized_value
        return properties

    def _is_foreign_table(
        self,
        schema_name: str,
        table_name: str,
        database_name: str = "",
    ) -> bool:
        database_name = database_name or self.database_name
        return (schema_name, table_name) in self._get_foreign_tables(database_name, schema_name)

    def _get_foreign_table_ddl(
        self,
        schema_name: str,
        table_name: str,
        database_name: str = "",
    ) -> str:
        database_name = database_name or self.database_name
        safe_schema = _escape_literal(schema_name)
        safe_table = _escape_literal(table_name)
        details = self._execute_pandas(
            f"""
            SELECT fs.srvname AS server_name, ft.ftoptions AS table_options
            FROM pg_catalog.pg_foreign_table ft
            JOIN pg_catalog.pg_class c ON c.oid = ft.ftrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_catalog.pg_foreign_server fs ON fs.oid = ft.ftserver
            WHERE n.nspname = '{safe_schema}'
              AND c.relname = '{safe_table}'
            """,
            database_name=database_name,
        )
        if details.empty:
            return f"-- Foreign table DDL not available for {schema_name}.{table_name}"

        columns = self.get_schema(
            database_name=database_name,
            schema_name=schema_name,
            table_name=table_name,
        )
        column_definitions = []
        for column in columns:
            definition = f"    {self.quote_identifier(column['name'])} {column['type']}"
            if not column.get("nullable", True):
                definition += " NOT NULL"
            if column.get("default_value"):
                definition += f" DEFAULT {column['default_value']}"
            column_definitions.append(definition)

        full_name = self.full_name(
            database_name=database_name,
            schema_name=schema_name,
            table_name=table_name,
        )
        server_name = self.quote_identifier(str(details["server_name"].iloc[0]))
        ddl = f"CREATE FOREIGN TABLE {full_name} (\n{',\n'.join(column_definitions)}\n)\nSERVER {server_name}"

        raw_options = details["table_options"].iloc[0]
        if isinstance(raw_options, str):
            raw_options = [part for part in raw_options.strip("{}").split(",") if part]
        elif raw_options is None:
            raw_options = []
        options = []
        for raw_option in raw_options:
            key, separator, value = str(raw_option).partition("=")
            if not separator:
                continue
            options.append(f"    {self.quote_identifier(key)} '{_escape_literal(value)}'")
        if options:
            ddl += f"\nOPTIONS (\n{',\n'.join(options)}\n)"
        return ddl + ";"

    @override
    def _get_ddl(
        self,
        schema_name: str,
        table_name: str,
        object_type: str = "TABLE",
        database_name: str = "",
    ) -> str:
        database_name = database_name or self.database_name
        if object_type == "TABLE" and self._is_foreign_table(
            schema_name,
            table_name,
            database_name=database_name,
        ):
            return self._get_foreign_table_ddl(
                schema_name,
                table_name,
                database_name=database_name,
            )

        ddl = super()._get_ddl(
            schema_name,
            table_name,
            object_type,
            database_name=database_name,
        )
        if object_type != "TABLE" or not ddl.startswith("CREATE TABLE"):
            return ddl

        properties = self._get_table_properties(
            schema_name,
            table_name,
            database_name=database_name,
        )
        if not properties:
            return ddl

        clauses = []
        for key in _DDL_PROPERTY_ORDER:
            if key not in properties:
                continue
            value = _escape_literal(properties[key])
            clauses.append(f"    {key} = '{value}'")

        base = ddl[:-1] if ddl.endswith(";") else ddl
        return f"{base}\nWITH (\n{',\n'.join(clauses)}\n);"

    @override
    def execute_queries(
        self,
        queries: List[str],
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> List[Any]:
        sql_types = [parse_sql_type(query, "postgresql") for query in queries]
        dml_types = {SQLType.INSERT, SQLType.UPDATE, SQLType.DELETE, SQLType.MERGE}
        has_ddl = SQLType.DDL in sql_types
        has_dml = any(sql_type in dml_types for sql_type in sql_types)

        if has_ddl and has_dml:
            raise DatusDbException(
                ErrorCode.DB_TRANSACTION_FAILED,
                message="Hologres does not support mixing DDL and DML in one transaction",
            )
        if has_dml and len(queries) > 1:
            raise DatusDbException(
                ErrorCode.DB_TRANSACTION_FAILED,
                message=(
                    "Hologres multi-statement DML transactions are disabled by default; "
                    "execute each DML statement separately"
                ),
            )
        return super().execute_queries(queries, catalog_name, database_name, schema_name)

    def describe_migration_capabilities(self) -> Dict[str, Any]:
        return {
            "supported": True,
            "dialect_family": "postgres-like",
            "requires": [
                "Primary-key columns must be NOT NULL",
                "A distribution key on a primary-key table must be the primary key or a subset of it",
            ],
            "forbids": [
                "UNIQUE constraints",
                "CHECK constraints",
                "FOREIGN KEY constraints",
                "Mixed DDL and DML transactions",
            ],
            "type_hints": {
                "STRING": "TEXT",
                "VARCHAR without a meaningful limit": "TEXT",
                "DATETIME": "TIMESTAMP",
                "TIMESTAMP WITH TIME ZONE": "TIMESTAMPTZ",
                "HUGEINT": "NUMERIC(38,0)",
                "JSON": "JSONB",
            },
            "layout_hints": {
                "orientation": "Use column for analytical tables and row only for primary-key point lookups",
                "distribution_key": "Prefer a high-cardinality join/group key; for PK tables use a PK subset",
                "event_time_column": "Use a non-null timestamp column frequently used in time filters",
            },
            "example_ddl": (
                "CREATE TABLE public.events (\n"
                "  event_id BIGINT NOT NULL,\n"
                "  occurred_at TIMESTAMPTZ NOT NULL,\n"
                "  payload JSONB,\n"
                "  PRIMARY KEY (event_id)\n"
                ")\n"
                "WITH (\n"
                "  orientation = 'column',\n"
                "  distribution_key = 'event_id',\n"
                "  event_time_column = 'occurred_at'\n"
                ")"
            ),
        }

    def suggest_table_layout(self, columns: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not columns:
            return {"orientation": "column"}

        primary_keys = [str(column["name"]) for column in columns if column.get("pk")]
        distribution_key = primary_keys[:1]
        if not distribution_key:
            scored = []
            for index, column in enumerate(columns):
                name = str(column["name"])
                base_type = re.sub(r"\(.*\)", "", str(column.get("type", "")).upper()).strip()
                score = 0
                if name.lower() == "id" or name.lower().endswith("_id"):
                    score += 100
                if base_type in _INTEGER_TYPES:
                    score += 50
                if not column.get("nullable", True):
                    score += 10
                scored.append((score, -index, name))
            scored.sort(reverse=True)
            if scored and scored[0][0] > 0:
                distribution_key = [scored[0][2]]

        layout: Dict[str, Any] = {"orientation": "column"}
        if distribution_key:
            layout["distribution_key"] = distribution_key

        for column in columns:
            base_type = re.sub(r"\(.*\)", "", str(column.get("type", "")).upper()).strip()
            if base_type in _TIME_TYPES and not column.get("nullable", True):
                layout["event_time_column"] = [str(column["name"])]
                break
        return layout

    def validate_ddl(self, ddl: str) -> List[str]:
        errors = super().validate_ddl(ddl)
        upper = ddl.upper()
        if re.search(r"\bUNIQUE\b", upper):
            errors.append("Hologres internal tables do not support UNIQUE constraints; use PRIMARY KEY if appropriate")
        if re.search(r"\bCHECK\s*\(", upper):
            errors.append("Hologres internal tables do not support CHECK constraints")
        if re.search(r"\bFOREIGN\s+KEY\b", upper):
            errors.append("Hologres internal tables do not support FOREIGN KEY constraints")
        return errors

    def map_source_type(self, source_dialect: str, source_type: str) -> Optional[str]:
        base = re.sub(r"\(.*\)", "", source_type.strip().upper()).strip()
        overrides = {
            "STRING": "TEXT",
            "HUGEINT": "NUMERIC(38,0)",
            "LARGEINT": "NUMERIC(38,0)",
            "DATETIME": "TIMESTAMP",
        }
        return overrides.get(base) or super().map_source_type(source_dialect, source_type)
