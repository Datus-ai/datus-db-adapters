# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from collections import OrderedDict
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple, Union, override

from pandas import isna
from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import NullPool

from datus_db_core import TABLE_TYPE, DatusDbException, ErrorCode, get_logger
from datus_sqlalchemy import SQLAlchemyConnector

from .config import BigQueryConfig
from .handlers import parse_bigquery_identifier

logger = get_logger(__name__)

_PHYSICAL_TABLE_TYPES = ("BASE TABLE", "CLONE", "SNAPSHOT", "EXTERNAL")
_MAX_ENGINES = 8


class BigQueryConnector(SQLAlchemyConnector):
    """BigQuery connector backed by ``sqlalchemy-bigquery``.

    BigQuery's project and default dataset are encoded in the engine URL.
    Therefore per-call Datus context overrides select an engine from a small
    LRU cache instead of issuing a session-level ``USE`` statement.
    """

    def __init__(self, config: Union[BigQueryConfig, dict]):
        if isinstance(config, dict):
            config = BigQueryConfig(**config)
        elif not isinstance(config, BigQueryConfig):
            raise TypeError(f"config must be BigQueryConfig or dict, got {type(config)}")

        self.bigquery_config = config
        super().__init__(
            self._build_connection_string(config.project, config.dataset or ""),
            dialect="bigquery",
            timeout_seconds=config.timeout_seconds,
        )
        self._default_catalog = config.project
        self._default_database = config.dataset or ""
        self._engines: OrderedDict[Tuple[str, str], Any] = OrderedDict()

    # ==================== Connection Management ====================

    @staticmethod
    def _build_connection_string(project: str, dataset: str = "") -> str:
        uri = f"bigquery://{project}"
        return f"{uri}/{dataset}" if dataset else uri

    def _engine_kwargs(self) -> Dict[str, Any]:
        config = self.bigquery_config
        kwargs: Dict[str, Any] = {"poolclass": NullPool}
        if config.credentials_path:
            kwargs["credentials_path"] = config.credentials_path
        elif config.credentials_info:
            kwargs["credentials_info"] = config.credentials_info.get_secret_value()
        elif config.credentials_base64:
            kwargs["credentials_base64"] = config.credentials_base64.get_secret_value()
        if config.billing_project_id:
            kwargs["billing_project_id"] = config.billing_project_id
        if config.location:
            kwargs["location"] = config.location
        return kwargs

    def _get_engine(self, project: str, dataset: str = ""):
        key = (project, dataset)
        with self._engine_lock:
            if key in self._engines:
                self._engines.move_to_end(key)
                return self._engines[key]

            engine = create_engine(self._build_connection_string(project, dataset), **self._engine_kwargs())
            self._engines[key] = engine
            while len(self._engines) > _MAX_ENGINES:
                _, evicted = self._engines.popitem(last=False)
                try:
                    evicted.dispose()
                except Exception as exc:
                    logger.warning("Error disposing evicted BigQuery engine: %s", exc)
            return engine

    @override
    def _ensure_engine(self):
        try:
            engine = self._get_engine(self.catalog_name, self.database_name)
            self.engine = engine
            self._owns_engine = True
            return engine
        except Exception as exc:
            self.engine = None
            self._owns_engine = False
            raise self._handle_exception(exc, operation="BigQuery connection") from exc

    @contextmanager
    @override
    def _conn(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> Iterator[Any]:
        project = catalog_name or self.catalog_name
        dataset = database_name or self.database_name
        engine = self._get_engine(project, dataset)
        conn = engine.connect()
        try:
            self.do_switch_context(
                conn,
                catalog_name=project,
                database_name=dataset,
                schema_name=schema_name,
            )
            yield conn
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    @override
    def close(self):
        seen: Set[int] = set()
        for engine in self._engines.values():
            if id(engine) in seen:
                continue
            seen.add(id(engine))
            try:
                engine.dispose()
            except Exception as exc:
                logger.warning("Error disposing BigQuery engine: %s", exc)
        self._engines.clear()
        self.engine = None
        self._owns_engine = False

    @override
    def do_switch_context(
        self,
        conn,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ):
        """No-op: project and dataset selection is handled by ``_conn``."""

    # ==================== Namespace and Naming ====================

    @override
    def _sys_databases(self) -> Set[str]:
        return {"information_schema"}

    @override
    def _sys_schemas(self) -> Set[str]:
        return self._sys_databases()

    @override
    def quote_identifier(self, name: str) -> str:
        escaped = name.replace("`", "\\`")
        return f"`{escaped}`"

    _quote_identifier = quote_identifier

    def _resolve_table(
        self,
        table_name: str,
        catalog_name: str = "",
        database_name: str = "",
    ) -> Tuple[str, str, str]:
        parsed = parse_bigquery_identifier(table_name)
        parsed_project = parsed["catalog_name"]
        parsed_dataset = parsed["database_name"]

        if parsed_project:
            project = parsed_project
            dataset = parsed_dataset
        elif parsed_dataset and database_name and not catalog_name:
            # A two-part listing is project.table when the caller already
            # supplied the dataset but omitted the project.
            project = parsed_dataset
            dataset = database_name
        else:
            project = catalog_name or self.catalog_name
            dataset = parsed_dataset or database_name or self.database_name
        return project, dataset, parsed["table_name"]

    @override
    def full_name(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
    ) -> str:
        project, dataset, name = self._resolve_table(table_name, catalog_name, database_name)
        parts = [part for part in (project, dataset, name) if part]
        return ".".join(self.quote_identifier(part) for part in parts)

    @override
    def identifier(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
    ) -> str:
        project, dataset, name = self._resolve_table(table_name, catalog_name, database_name)
        return ".".join(part for part in (project, dataset, name) if part)

    @override
    def _reset_filter_tables(
        self,
        tables: Optional[List[str]] = None,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> List[str]:
        return super()._reset_filter_tables(
            tables,
            catalog_name or self.catalog_name,
            database_name or self.database_name,
            "",
        )

    @override
    def _sqlalchemy_schema(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> Optional[str]:
        return database_name or self.database_name or None

    # ==================== Metadata Retrieval ====================

    def _require_dataset(self, database_name: str, operation: str) -> str:
        dataset = database_name or self.database_name
        if not dataset:
            raise DatusDbException(
                ErrorCode.COMMON_FIELD_INVALID,
                f"BigQuery requires a dataset to {operation}. Configure dataset or pass database_name.",
            )
        return dataset

    def _information_schema(self, project: str, dataset: str, view: str) -> str:
        return f"{self.quote_identifier(project)}.{self.quote_identifier(dataset)}.INFORMATION_SCHEMA.{view}"

    @staticmethod
    def _sql_literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    @staticmethod
    def _qualify_listed_name(
        name: str,
        project: str,
        dataset: str,
        requested_project: str,
        requested_dataset: str,
    ) -> str:
        parts = []
        if not requested_project:
            parts.append(project)
        if not requested_dataset:
            parts.append(dataset)
        parts.append(name)
        return ".".join(parts)

    def _list_objects(
        self,
        table_types: Tuple[str, ...],
        project: str,
        dataset: str,
        requested_project: str,
        requested_dataset: str,
    ) -> List[str]:
        types = ", ".join(self._sql_literal(table_type) for table_type in table_types)
        sql = (
            f"SELECT table_name FROM {self._information_schema(project, dataset, 'TABLES')} "
            f"WHERE table_type IN ({types}) ORDER BY table_name"
        )
        df = self._execute_pandas(sql, catalog_name=project, database_name=dataset)
        if df.empty:
            return []
        return [
            self._qualify_listed_name(name, project, dataset, requested_project, requested_dataset)
            for name in df["table_name"].tolist()
        ]

    @override
    def get_tables(self, catalog_name: str = "", database_name: str = "", schema_name: str = "") -> List[str]:
        project = catalog_name or self.catalog_name
        dataset = self._require_dataset(database_name, "list tables")
        return self._list_objects(_PHYSICAL_TABLE_TYPES, project, dataset, catalog_name, database_name)

    @override
    def get_views(self, catalog_name: str = "", database_name: str = "", schema_name: str = "") -> List[str]:
        project = catalog_name or self.catalog_name
        dataset = self._require_dataset(database_name, "list views")
        return self._list_objects(("VIEW",), project, dataset, catalog_name, database_name)

    @override
    def get_materialized_views(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> List[str]:
        project = catalog_name or self.catalog_name
        dataset = self._require_dataset(database_name, "list materialized views")
        return self._list_objects(("MATERIALIZED VIEW",), project, dataset, catalog_name, database_name)

    def _get_objects_with_ddl(
        self,
        table_types: Tuple[str, ...],
        datus_table_type: str,
        catalog_name: str,
        database_name: str,
        tables: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        project = catalog_name or self.catalog_name
        dataset = self._require_dataset(database_name, "read object DDL")
        types = ", ".join(self._sql_literal(table_type) for table_type in table_types)
        sql = (
            f"SELECT table_name, ddl FROM {self._information_schema(project, dataset, 'TABLES')} "
            f"WHERE table_type IN ({types}) ORDER BY table_name"
        )
        df = self._execute_pandas(sql, catalog_name=project, database_name=dataset)
        if df.empty:
            return []

        requested = set(tables or [])
        objects: List[Dict[str, Any]] = []
        for _, row in df.iterrows():
            table_name = row["table_name"]
            full_name = self.full_name(project, dataset, "", table_name)
            identifier = self.identifier(project, dataset, "", table_name)
            if requested and not {table_name, full_name, identifier}.intersection(requested):
                continue
            ddl = row.get("ddl")
            objects.append(
                {
                    "identifier": identifier,
                    "catalog_name": project,
                    "database_name": dataset,
                    "schema_name": "",
                    "table_name": table_name,
                    "table_type": datus_table_type,
                    "definition": "" if ddl is None or isna(ddl) else str(ddl),
                }
            )
        return objects

    @override
    def get_tables_with_ddl(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        tables: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        return self._get_objects_with_ddl(
            _PHYSICAL_TABLE_TYPES,
            "table",
            catalog_name,
            database_name,
            tables,
        )

    @override
    def get_views_with_ddl(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> List[Dict[str, Any]]:
        return self._get_objects_with_ddl(("VIEW",), "view", catalog_name, database_name)

    def get_materialized_views_with_ddl(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> List[Dict[str, Any]]:
        return self._get_objects_with_ddl(
            ("MATERIALIZED VIEW",),
            "mv",
            catalog_name,
            database_name,
        )

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
        project = catalog_name or self.catalog_name
        dataset = self._require_dataset(database_name, "read a table schema")
        sql = (
            "SELECT column_name, data_type, is_nullable, column_default, ordinal_position "
            f"FROM {self._information_schema(project, dataset, 'COLUMNS')} "
            f"WHERE table_name = {self._sql_literal(table_name)} ORDER BY ordinal_position"
        )
        df = self._execute_pandas(sql, catalog_name=project, database_name=dataset)
        if df.empty:
            return []
        return [
            {
                "cid": int(row["ordinal_position"]),
                "name": row["column_name"],
                "type": row["data_type"],
                "nullable": row["is_nullable"] == "YES",
                "default_value": (
                    ""
                    if row.get("column_default") is None or isna(row.get("column_default"))
                    else row["column_default"]
                ),
                "pk": False,
            }
            for _, row in df.iterrows()
        ]

    @override
    def get_databases(self, catalog_name: str = "", include_sys: bool = False) -> List[str]:
        project = catalog_name or self.catalog_name
        try:
            datasets = inspect(self._get_engine(project)).get_schema_names()
        except Exception as exc:
            raise self._handle_exception(exc, operation="list BigQuery datasets") from exc
        if include_sys:
            return datasets
        system = self._sys_databases()
        return [dataset for dataset in datasets if dataset.lower() not in system]

    @override
    def get_schemas(
        self,
        catalog_name: str = "",
        database_name: str = "",
        include_sys: bool = False,
    ) -> List[str]:
        """BigQuery has no namespace layer below a dataset."""
        return []

    # ==================== Sample Data ====================

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
        try:
            limit = int(top_n)
        except (TypeError, ValueError) as exc:
            raise DatusDbException(ErrorCode.COMMON_FIELD_INVALID, "top_n must be a positive integer") from exc
        if limit <= 0:
            raise DatusDbException(ErrorCode.COMMON_FIELD_INVALID, "top_n must be a positive integer")
        if table_type not in ("table", "view", "mv", "full"):
            raise DatusDbException(ErrorCode.COMMON_FIELD_INVALID, f"Unsupported table_type: {table_type}")

        project = catalog_name or self.catalog_name
        dataset = self._require_dataset(database_name, "sample rows")
        if tables:
            targets = [(table_name.split(".")[-1].strip("`"), table_type) for table_name in tables]
        else:
            targets: List[Tuple[str, str]] = []
            if table_type in ("table", "full"):
                targets.extend((name, "table") for name in self.get_tables(project, dataset))
            if table_type in ("view", "full"):
                targets.extend((name, "view") for name in self.get_views(project, dataset))
            if table_type in ("mv", "full"):
                targets.extend((name, "mv") for name in self.get_materialized_views(project, dataset))

        samples: List[Dict[str, Any]] = []
        for table_name, object_type in targets:
            full_name = self.full_name(project, dataset, "", table_name)
            df = self._execute_pandas(
                f"SELECT * FROM {full_name} LIMIT {limit}",
                catalog_name=project,
                database_name=dataset,
            )
            if df.empty:
                continue
            samples.append(
                {
                    "identifier": self.identifier(project, dataset, "", table_name),
                    "catalog_name": project,
                    "database_name": dataset,
                    "schema_name": "",
                    "table_name": table_name,
                    "table_type": object_type,
                    "sample_rows": df.to_csv(index=False),
                }
            )
        return samples

    # ==================== MigrationTargetMixin ====================

    @override
    def describe_migration_capabilities(self) -> Dict[str, Any]:
        return {
            "supported": True,
            "dialect_family": "bigquery",
            "requires": ["PRIMARY KEY and FOREIGN KEY constraints must be declared NOT ENFORCED"],
            "forbids": [
                "AUTO_INCREMENT, SERIAL, or IDENTITY columns",
                "ENGINE clauses",
                "DUPLICATE KEY or AGGREGATE KEY table models",
                "DISTRIBUTED BY ... BUCKETS clauses",
            ],
            "type_hints": {
                "integer": "INT64",
                "floating point": "FLOAT64",
                "high precision decimal": "NUMERIC or BIGNUMERIC",
                "text": "STRING",
                "binary": "BYTES",
                "boolean": "BOOL",
                "timestamp": "TIMESTAMP for an instant; DATETIME for civil time",
                "semi-structured": "JSON, ARRAY, or STRUCT",
            },
            "example_ddl": (
                "CREATE TABLE `project.dataset.events` (\n"
                "  event_id INT64 NOT NULL,\n"
                "  event_ts TIMESTAMP,\n"
                "  payload JSON,\n"
                "  PRIMARY KEY (event_id) NOT ENFORCED\n"
                ")\nPARTITION BY DATE(event_ts)\nCLUSTER BY event_id"
            ),
        }

    @override
    def suggest_table_layout(self, columns: List[Dict[str, Any]]) -> Dict[str, Any]:
        for column in columns:
            column_type = str(column.get("type", "")).upper()
            if column_type in {"DATE", "DATETIME", "TIMESTAMP"}:
                return {"partition_by": column["name"]}
        return {}

    @override
    def validate_ddl(self, ddl: str) -> List[str]:
        import re

        from datus_db_core.sql_utils import mask_sql_quoted_regions, strip_sql_comments

        upper = mask_sql_quoted_regions(strip_sql_comments(ddl)).upper()
        errors: List[str] = []
        for keyword in ("AUTO_INCREMENT", "SERIAL", "IDENTITY"):
            if re.search(rf"\b{keyword}\b", upper):
                errors.append(f"{keyword} is not supported by BigQuery")
        if re.search(r"\bENGINE\s*=", upper):
            errors.append("ENGINE clauses are not supported by BigQuery")
        if re.search(r"\b(?:DUPLICATE|AGGREGATE)\s+KEY\b", upper):
            errors.append("Doris/StarRocks table models are not supported by BigQuery")
        if re.search(r"\bDISTRIBUTED\s+BY\b|\bBUCKETS\b", upper):
            errors.append("DISTRIBUTED BY ... BUCKETS is not supported by BigQuery; use CLUSTER BY")
        key_constraint_patterns = (
            r"\bPRIMARY\s+KEY(?:\s*\([^)]*\))?(?:\s+NOT\s+ENFORCED)?",
            r"\bFOREIGN\s+KEY\s*\([^)]*\)\s+REFERENCES\s+(?:[^\s(]+\s*)?\([^)]*\)(?:\s+NOT\s+ENFORCED)?",
        )
        for pattern in key_constraint_patterns:
            for constraint in re.finditer(pattern, upper):
                if not re.search(r"\bNOT\s+ENFORCED\b", constraint.group(0)):
                    errors.append("BigQuery PRIMARY KEY and FOREIGN KEY constraints must be declared NOT ENFORCED")
        return errors

    @override
    def map_source_type(self, source_dialect: str, source_type: str) -> Optional[str]:
        import re

        base = re.sub(r"\(.*\)", "", source_type.strip().upper()).strip()
        return {
            "TINYINT": "INT64",
            "SMALLINT": "INT64",
            "INTEGER": "INT64",
            "BIGINT": "INT64",
            "HUGEINT": "BIGNUMERIC",
            "LARGEINT": "BIGNUMERIC",
            "REAL": "FLOAT64",
            "DOUBLE": "FLOAT64",
            "VARCHAR": "STRING",
            "CHAR": "STRING",
            "TEXT": "STRING",
            "BINARY": "BYTES",
            "VARBINARY": "BYTES",
            "BYTEA": "BYTES",
            "JSONB": "JSON",
            "TIMESTAMP WITH TIME ZONE": "TIMESTAMP",
            "TIMESTAMP WITHOUT TIME ZONE": "DATETIME",
        }.get(base)

    @override
    def get_type(self) -> str:
        return "bigquery"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "db_type": "bigquery",
            "project": self.catalog_name,
            "dataset": self.database_name,
            "location": self.bigquery_config.location,
            "billing_project_id": self.bigquery_config.billing_project_id,
        }
