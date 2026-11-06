# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import re
from typing import Any, Dict, List, Optional, Set, Union, override

from pandas import DataFrame
from sqlalchemy.engine import URL

from datus_db_core import TABLE_TYPE, CatalogSupportMixin, DatusDbException, ErrorCode, get_logger
from datus_sqlalchemy import SQLAlchemyConnector

from .config import DatabricksConfig
from .handlers import parse_databricks_identifier

logger = get_logger(__name__)

_SYSTEM_NAMESPACES = {"information_schema", "sys"}


def _profile_config(profile: str, host: str):
    """Load a Databricks CLI profile. The token stays in the CLI cache."""
    from databricks.sdk.core import Config

    return Config(profile=profile, host=f"https://{host}")


class DatabricksConnector(SQLAlchemyConnector, CatalogSupportMixin):
    """Databricks SQL warehouse connector backed by the SQLAlchemy dialect in ``databricks-sql-connector``.

    The engine URL carries the warehouse, catalog, and schema. Credentials are
    attached per physical connection: a CLI profile header factory, or an
    access token. Neither is written into the URL.
    """

    def __init__(self, config: Union[DatabricksConfig, dict]):
        if isinstance(config, dict):
            config = DatabricksConfig(**config)
        elif not isinstance(config, DatabricksConfig):
            raise TypeError(f"config must be DatabricksConfig or dict, got {type(config)}")

        self.databricks_config = config
        super().__init__(
            self._connection_url(config),
            dialect="databricks",
            timeout_seconds=config.timeout_seconds,
        )
        self._default_catalog = config.catalog
        self._default_database = config.schema_name
        self._sdk_config = None
        self.configure_connection(
            connect_args={
                "_socket_timeout": config.timeout_seconds,
                "user_agent_entry": "datus-databricks",
            },
            connection_parameters=self._connection_parameters,
        )

    @staticmethod
    def _connection_url(config: DatabricksConfig) -> str:
        return str(
            URL.create(
                "databricks",
                host=config.host,
                query={
                    "http_path": config.resolved_http_path(),
                    "catalog": config.catalog,
                    "schema": config.schema_name,
                },
            )
        )

    def _connection_parameters(self) -> Dict[str, Any]:
        config = self.databricks_config
        if config.auth_mode == "access_token":
            return {"access_token": config.access_token.get_secret_value()}
        sdk_config = self._oauth_config()
        return {"credentials_provider": lambda: sdk_config.authenticate}

    def _oauth_config(self):
        if self._sdk_config is None:
            try:
                self._sdk_config = _profile_config(self.databricks_config.profile, self.databricks_config.host)
            except Exception as exc:
                raise DatusDbException(
                    ErrorCode.DB_AUTHENTICATION_FAILED,
                    message_args={"error_message": f"Databricks profile auth failed: {exc}"},
                ) from exc
        return self._sdk_config

    @override
    def quote_identifier(self, name: str) -> str:
        escaped = name.replace("`", "``")
        return f"`{escaped}`"

    def _uc_schema(self, database_name: str = "", schema_name: str = "") -> str:
        return database_name or schema_name or self.database_name

    def _qualified_namespace(self, catalog_name: str = "", database_name: str = "", schema_name: str = "") -> str:
        catalog = catalog_name or self.catalog_name
        schema = self._uc_schema(database_name, schema_name)
        if not catalog or not schema:
            raise DatusDbException(
                ErrorCode.DB_FAILED,
                message_args={"error_message": "Databricks catalog and schema are required"},
            )
        return f"{self.quote_identifier(catalog)}.{self.quote_identifier(schema)}"

    @override
    def do_switch_context(self, conn, catalog_name: str = "", database_name: str = "", schema_name: str = ""):
        catalog = catalog_name or self.catalog_name
        schema = database_name or schema_name or self.database_name
        catalog_changed = bool(catalog and catalog != self.databricks_config.catalog)
        schema_changed = bool(schema and schema != self.databricks_config.schema_name)
        if catalog_changed:
            self._use(conn, "CATALOG", catalog)
        if schema and (schema_changed or catalog_changed):
            self._use(conn, "SCHEMA", schema)

    def _use(self, conn, kind: str, name: str) -> None:
        conn.exec_driver_sql(
            f"USE {kind} {self.quote_identifier(name)}",
            execution_options={"no_parameters": True},
        )

    @override
    def full_name(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
    ) -> str:
        catalog, schema, name = self._resolve_table(table_name, catalog_name, database_name, schema_name)
        return ".".join(self.quote_identifier(part) for part in (catalog, schema, name) if part)

    @override
    def identifier(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
    ) -> str:
        catalog, schema, name = self._resolve_table(table_name, catalog_name, database_name, schema_name)
        return ".".join(part for part in (catalog, schema, name) if part)

    def _resolve_table(self, table_name: str, catalog_name: str, database_name: str, schema_name: str):
        parsed = parse_databricks_identifier(table_name)
        catalog = parsed["catalog_name"] or catalog_name or self.catalog_name
        schema = parsed["database_name"] or self._uc_schema(database_name, schema_name)
        return catalog, schema, parsed["table_name"] or table_name

    @override
    def _sys_databases(self) -> Set[str]:
        return set(_SYSTEM_NAMESPACES)

    @override
    def get_catalogs(self) -> List[str]:
        frame = self._execute_pandas("SHOW CATALOGS")
        return self._names(frame, "catalog")

    @override
    def switch_catalog(self, catalog_name: str) -> None:
        self.switch_context(catalog_name=catalog_name)

    @override
    def default_catalog(self) -> str:
        return self.databricks_config.catalog

    @override
    def get_databases(self, catalog_name: str = "", include_sys: bool = False) -> List[str]:
        catalog = catalog_name or self.catalog_name
        sql = f"SHOW SCHEMAS IN {self.quote_identifier(catalog)}" if catalog else "SHOW SCHEMAS"
        names = self._names(self._execute_pandas(sql), "databaseName", "namespace")
        if include_sys:
            return names
        return [name for name in names if name.lower() not in _SYSTEM_NAMESPACES]

    @override
    def get_schemas(self, catalog_name: str = "", database_name: str = "", include_sys: bool = False) -> List[str]:
        """Unity Catalog has no third namespace below catalog and schema."""
        return []

    @override
    def get_tables(self, catalog_name: str = "", database_name: str = "", schema_name: str = "") -> List[str]:
        namespace = self._qualified_namespace(catalog_name, database_name, schema_name)
        table_names = self._names(self._execute_pandas(f"SHOW TABLES IN {namespace}"), "tableName")
        view_names = set(self._names(self._execute_pandas(f"SHOW VIEWS IN {namespace}"), "viewName"))
        names = [name for name in table_names if name not in view_names]
        return [self._qualify_listed(name, catalog_name, database_name, schema_name) for name in names]

    @override
    def get_views(self, catalog_name: str = "", database_name: str = "", schema_name: str = "") -> List[str]:
        return self._views(catalog_name, database_name, schema_name, materialized_only=False)

    @override
    def get_materialized_views(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> List[str]:
        return self._views(catalog_name, database_name, schema_name, materialized_only=True)

    def _views(self, catalog_name: str, database_name: str, schema_name: str, materialized_only: bool) -> List[str]:
        namespace = self._qualified_namespace(catalog_name, database_name, schema_name)
        frame = self._execute_pandas(f"SHOW VIEWS IN {namespace}")
        if frame.empty or "viewName" not in frame.columns:
            return []
        names = [
            str(row["viewName"]) for _, row in frame.iterrows() if not materialized_only or self._is_materialized(row)
        ]
        return [self._qualify_listed(name, catalog_name, database_name, schema_name) for name in names]

    @staticmethod
    def _is_materialized(row) -> bool:
        if "isMaterialized" not in row.index:
            return False
        flag = row["isMaterialized"]
        return flag is True or str(flag).lower() == "true"

    def _qualify_listed(self, name: str, catalog_name: str, database_name: str, schema_name: str) -> str:
        # Always return catalog.schema.table. A two-part name is read by
        # Databricks as schema.table, so dropping the schema when the caller
        # passed one produced `catalog`.`table` and TABLE_OR_VIEW_NOT_FOUND.
        catalog = catalog_name or self.catalog_name
        schema = self._uc_schema(database_name, schema_name)
        return ".".join(part for part in (catalog, schema, name) if part)

    @override
    def get_schema(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
    ) -> List[Dict[str, Any]]:
        if not table_name:
            return []
        full_name = self.full_name(catalog_name, database_name, schema_name, table_name)
        frame = self._execute_pandas(f"DESCRIBE TABLE {full_name}")
        columns: List[Dict[str, Any]] = []
        for _, row in frame.iterrows():
            name = str(row.iloc[0] or "").strip()
            if not name or name.startswith("#"):
                continue
            columns.append(
                {
                    "cid": len(columns),
                    "name": name,
                    "type": str(row.iloc[1]) if len(row) > 1 else "",
                    "comment": str(row.iloc[2]) if len(row) > 2 and row.iloc[2] is not None else None,
                    "nullable": True,
                    "pk": False,
                    "default_value": None,
                }
            )
        return columns

    @override
    def get_tables_with_ddl(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        tables: Optional[List[str]] = None,
    ) -> List[Dict[str, str]]:
        return self._objects_with_ddl("table", catalog_name, database_name, schema_name, tables)

    @override
    def get_views_with_ddl(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> List[Dict[str, str]]:
        return self._objects_with_ddl("view", catalog_name, database_name, schema_name, None)

    def _objects_with_ddl(
        self,
        object_type: str,
        catalog_name: str,
        database_name: str,
        schema_name: str,
        tables: Optional[List[str]],
    ) -> List[Dict[str, str]]:
        names = self._ddl_names(object_type, catalog_name, database_name, schema_name, tables)
        catalog = catalog_name or self.catalog_name
        schema = self._uc_schema(database_name, schema_name)
        return [
            self._ddl_entry(object_type, catalog, schema, catalog_name, database_name, schema_name, name)
            for name in names
        ]

    def _ddl_entry(
        self,
        object_type: str,
        catalog: str,
        schema: str,
        catalog_name: str,
        database_name: str,
        schema_name: str,
        name: str,
    ) -> Dict[str, str]:
        bare = self._bare_name(name)
        full_name = self.full_name(catalog_name, database_name, schema_name, name)
        return {
            "identifier": self.identifier(catalog, schema, "", bare),
            "catalog_name": catalog,
            "database_name": schema,
            "schema_name": "",
            "table_name": bare,
            "table_type": object_type,
            "definition": self._definition_or_placeholder(full_name, bare),
        }

    def _ddl_names(
        self,
        object_type: str,
        catalog_name: str,
        database_name: str,
        schema_name: str,
        tables: Optional[List[str]],
    ) -> List[str]:
        if tables:
            return list(tables)
        if object_type == "view":
            return self.get_views(catalog_name, database_name, schema_name)
        return self.get_tables(catalog_name, database_name, schema_name)

    def _definition_or_placeholder(self, full_name: str, bare_name: str) -> str:
        try:
            frame = self._execute_pandas(f"SHOW CREATE TABLE {full_name}")
        except Exception as exc:
            logger.warning("Could not get DDL for %s: %s", full_name, exc)
            return f"-- DDL not available for {bare_name}"
        if frame.empty:
            return f"-- DDL not available for {bare_name}"
        return str(frame.iloc[0, 0])

    @override
    def get_sample_rows(
        self,
        tables: Optional[List[str]] = None,
        top_n: int = 5,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_type: TABLE_TYPE = "table",
    ) -> List[Dict[str, Any]]:
        limit = self._sample_limit(top_n)
        if table_type not in ("table", "view", "mv", "full"):
            raise DatusDbException(ErrorCode.COMMON_FIELD_INVALID, f"Unsupported table_type: {table_type}")
        catalog = catalog_name or self.catalog_name
        schema = self._uc_schema(database_name, schema_name)
        samples = []
        targets = self._sample_targets(tables, table_type, catalog_name, database_name, schema_name)
        for table_name, object_type in targets:
            frame = self._execute_pandas(
                f"SELECT * FROM {self.full_name(catalog, schema, '', table_name)} LIMIT {limit}"
            )
            if frame.empty:
                continue
            samples.append(self._sample_entry(catalog, schema, table_name, object_type, frame))
        return samples

    @staticmethod
    def _sample_limit(top_n: int) -> int:
        try:
            limit = int(top_n)
        except (TypeError, ValueError) as exc:
            raise DatusDbException(ErrorCode.COMMON_FIELD_INVALID, "top_n must be a positive integer") from exc
        if limit <= 0:
            raise DatusDbException(ErrorCode.COMMON_FIELD_INVALID, "top_n must be a positive integer")
        return limit

    def _sample_targets(
        self,
        tables: Optional[List[str]],
        table_type: str,
        catalog_name: str,
        database_name: str,
        schema_name: str,
    ) -> List[tuple]:
        if tables:
            label = "table" if table_type == "full" else table_type
            return [(self._bare_name(name), label) for name in tables]
        targets = []
        if table_type in ("table", "full"):
            targets.extend(self._named(self.get_tables(catalog_name, database_name, schema_name), "table"))
        if table_type in ("view", "full"):
            targets.extend(self._named(self.get_views(catalog_name, database_name, schema_name), "view"))
        if table_type in ("mv", "full"):
            targets.extend(self._named(self.get_materialized_views(catalog_name, database_name, schema_name), "mv"))
        return targets

    def _named(self, names: List[str], object_type: str) -> List[tuple]:
        return [(self._bare_name(name), object_type) for name in names]

    def _sample_entry(self, catalog: str, schema: str, table_name: str, object_type: str, frame: DataFrame):
        return {
            "identifier": self.identifier(catalog, schema, "", table_name),
            "catalog_name": catalog,
            "database_name": schema,
            "schema_name": "",
            "table_name": table_name,
            "table_type": object_type,
            "sample_rows": frame.to_csv(index=False),
        }

    @staticmethod
    def _bare_name(name: str) -> str:
        return name.split(".")[-1].strip("`")

    @staticmethod
    def _names(frame: DataFrame, *preferred: str) -> List[str]:
        if frame.empty:
            return []
        for name in preferred:
            if name in frame.columns:
                return [str(value) for value in frame[name].tolist()]
        return [str(value) for value in frame.iloc[:, 0].tolist()]

    @override
    def get_type(self) -> str:
        return "databricks"

    def to_dict(self) -> Dict[str, Any]:
        config = self.databricks_config
        return {
            "db_type": "databricks",
            "host": config.host,
            "http_path": config.resolved_http_path(),
            "catalog": self.catalog_name,
            "schema": self.database_name,
            "auth_mode": config.auth_mode,
            "profile": config.profile,
        }

    @override
    def describe_migration_capabilities(self) -> Dict[str, Any]:
        return {
            "supported": True,
            "dialect_family": "databricks",
            "requires": [],
            "forbids": [
                "ENGINE= (MySQL)",
                "AUTO_INCREMENT (MySQL)",
                "DISTRIBUTED BY ... BUCKETS (StarRocks)",
            ],
            "type_hints": {
                "VARCHAR": "STRING",
                "TEXT": "STRING",
                "BLOB": "BINARY",
            },
            "example_ddl": "CREATE TABLE t (id BIGINT, name STRING) USING DELTA",
        }

    @override
    def map_source_type(self, source_dialect: str, source_type: str) -> Optional[str]:
        base = re.sub(r"\(.*\)", "", source_type.strip().upper()).strip()
        return {
            "VARCHAR": "STRING",
            "NVARCHAR": "STRING",
            "CHAR": "STRING",
            "TEXT": "STRING",
            "BLOB": "BINARY",
            "BYTEA": "BINARY",
            "JSONB": "STRING",
            "HUGEINT": "DECIMAL(38,0)",
            "LARGEINT": "DECIMAL(38,0)",
        }.get(base)

    @override
    def validate_ddl(self, ddl: str) -> List[str]:
        errors = super().validate_ddl(ddl)
        upper = ddl.upper()
        if "AUTO_INCREMENT" in upper:
            errors.append("AUTO_INCREMENT is MySQL syntax; Databricks uses GENERATED ALWAYS AS IDENTITY")
        if re.search(r"\bENGINE\s*=", upper):
            errors.append("ENGINE= is MySQL syntax and is not valid Databricks DDL")
        return errors
