# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

"""AWS Athena connector using the official SDK and bounded API result reads."""

import time
from collections.abc import Iterator
from typing import Any, Literal
from uuid import uuid4

import pyarrow as pa
from botocore.config import Config

from datus_db_core import BaseSqlConnector, CatalogSupportMixin, MigrationTargetMixin, get_logger
from datus_db_core.aws import AWSIdentity, AWSIdentityConfig

from .config import AthenaConfig
from .handlers import parse_athena_identifier
from .models import AthenaExecuteSQLResult
from .results import arrow_type, convert_value

logger = get_logger(__name__)
_TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}


class AthenaConnector(BaseSqlConnector, CatalogSupportMixin, MigrationTargetMixin):
    """Glue Catalog metadata and Athena SQL execution in one configured Workgroup.

    ``session`` is optional; SDK sessions retain their credential providers so role
    credentials can refresh. No credentials are extracted, frozen or logged here.
    """

    def __init__(self, config: AthenaConfig | dict, *, session: Any = None) -> None:
        config = config if isinstance(config, AthenaConfig) else AthenaConfig.model_validate(config)
        super().__init__(config, dialect="athena")
        self._default_catalog = config.catalog
        self._default_database = config.database or ""
        self._identity = AWSIdentity(
            AWSIdentityConfig(region=config.region, profile=config.profile_name, ca_bundle=config.ca_bundle),
            session=session,
            client_config=Config(
                connect_timeout=config.timeout_seconds,
                read_timeout=config.timeout_seconds,
                retries={"mode": "standard", "total_max_attempts": 3},
            ),
        )
        self._client: Any = None
        self.connect()

    def connect(self) -> None:
        if self._client is None:
            self._client = self._identity.client("athena")

    def close(self) -> None:
        self._client = None
        self._identity.close()

    def _context(self, catalog_name: str = "", database_name: str = "", schema_name: str = "") -> tuple[str, str]:
        if schema_name:
            raise ValueError("Athena uses catalog.database.table; schema_name must be empty")
        return catalog_name or self.catalog_name, database_name or self.database_name

    def switch_context(self, catalog_name: str = "", database_name: str = "", schema_name: str = "") -> None:
        self._context(catalog_name, database_name, schema_name)
        super().switch_context(catalog_name, database_name, schema_name)

    @staticmethod
    def quote_identifier(name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    def _resolve_table(
        self, table_name: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> tuple[str, str, str]:
        catalog, database = self._context(catalog_name, database_name, schema_name)
        parsed = parse_athena_identifier(table_name)
        if not parsed["table_name"]:
            raise ValueError("table_name must not be empty")
        return parsed["catalog_name"] or catalog, parsed["database_name"] or database, parsed["table_name"]

    def full_name(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = "", table_name: str = ""
    ) -> str:
        parts = self._resolve_table(table_name, catalog_name, database_name, schema_name)
        if not parts[1]:
            raise ValueError("An Athena database is required for a qualified table name")
        return ".".join(self.quote_identifier(part) for part in parts if part)

    def default_catalog(self) -> str:
        return self.config.catalog

    def switch_catalog(self, catalog_name: str) -> None:
        self.switch_context(catalog_name=catalog_name)

    @staticmethod
    def _diagnostics(execution: dict) -> dict[str, Any]:
        status = execution.get("Status", {})
        return {
            "state": status.get("State"),
            "state_change_reason": status.get("StateChangeReason"),
            "athena_error": status.get("AthenaError"),
            "statistics": execution.get("Statistics", {}),
            "engine_version": execution.get("EngineVersion", {}),
            "workgroup": execution.get("WorkGroup"),
            "statement_type": execution.get("StatementType"),
            "managed_results": execution.get("ManagedQueryResultsConfiguration", {}).get("Enabled", False),
        }

    def _workgroup_configuration(self) -> dict:
        self.connect()
        group = self._client.get_work_group(WorkGroup=self.config.workgroup)["WorkGroup"]
        if group.get("State") != "ENABLED":
            raise ValueError(f"Athena Workgroup {self.config.workgroup!r} is not enabled")
        configuration = group.get("Configuration", {})
        engine = configuration.get("EngineVersion", {}).get("SelectedEngineVersion", "")
        if "spark" in engine.lower():
            raise ValueError("This adapter requires an Athena SQL Workgroup; Spark Workgroups are unsupported")
        return configuration

    def _start(self, sql: str, catalog: str, database: str, client_request_token: str | None) -> str:
        if not sql.strip():
            raise ValueError("SQL must not be empty")
        group = self._workgroup_configuration()
        request = {
            "QueryString": sql,
            # One token per logical execution. Botocore reuses it on HTTP retries.
            "ClientRequestToken": client_request_token or uuid4().hex,
            "WorkGroup": self.config.workgroup,
            "QueryExecutionContext": {
                key: value for key, value in {"Catalog": catalog, "Database": database}.items() if value
            },
        }
        managed = group.get("ManagedQueryResultsConfiguration", {}).get("Enabled", False)
        if self.config.output_location and not managed and not group.get("EnforceWorkGroupConfiguration", False):
            request["ResultConfiguration"] = {"OutputLocation": self.config.output_location}
        return self._client.start_query_execution(**request)["QueryExecutionId"]

    def _wait(self, query_id: str, execution: dict) -> None:
        deadline = time.monotonic() + self.config.query_timeout_seconds
        while True:
            execution.update(self._client.get_query_execution(QueryExecutionId=query_id)["QueryExecution"])
            state = execution.get("Status", {}).get("State")
            if state in _TERMINAL:
                if state != "SUCCEEDED":
                    status = execution["Status"]
                    reason = status.get("StateChangeReason") or status.get("AthenaError", {}).get("ErrorMessage", "")
                    raise RuntimeError(f"Athena query {state}: {reason}")
                return
            if state not in {"QUEUED", "RUNNING"}:
                raise RuntimeError(f"Unknown Athena query state: {state!r}")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Athena query wait deadline exceeded")
            time.sleep(min(self.config.poll_interval_seconds, remaining))

    def _cancel(self, query_id: str, execution: dict) -> dict:
        result = {"cancellation_requested": False, "cancellation_confirmed": False}
        try:
            self._client.stop_query_execution(QueryExecutionId=query_id)
            result["cancellation_requested"] = True
            deadline = time.monotonic() + self.config.cancel_timeout_seconds
            while time.monotonic() < deadline:
                execution.update(self._client.get_query_execution(QueryExecutionId=query_id)["QueryExecution"])
                state = execution.get("Status", {}).get("State")
                if state in _TERMINAL:
                    result["cancellation_confirmed"] = state == "CANCELLED"
                    break
                time.sleep(min(self.config.poll_interval_seconds, max(0, deadline - time.monotonic())))
        except Exception as exc:
            result["cancellation_error"] = str(exc)
        return dict(self._diagnostics(execution), **result)

    def cancel_query(self, query_execution_id: str) -> dict:
        """Request native cancellation; report the separately observed final state."""
        self.connect()
        return dict(query_execution_id=query_execution_id, **self._cancel(query_execution_id, {}))

    def _fetch(self, query_id: str, execution: dict, max_rows: int) -> tuple[pa.Table, dict]:
        deadline = time.monotonic() + self.config.result_timeout_seconds
        token = None
        seen_tokens = set()
        columns = None
        rows = []
        byte_count = 0
        info = {"truncated": False, "result_bytes": 0}
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("Athena result read deadline exceeded")
            request = {"QueryExecutionId": query_id, "MaxResults": self.config.page_size}
            if token:
                request["NextToken"] = token
            response = self._client.get_query_results(**request)
            if time.monotonic() > deadline:
                raise TimeoutError("Athena result read deadline exceeded")
            result_set = response.get("ResultSet", {})
            first_page = columns is None
            if first_page:
                columns = result_set.get("ResultSetMetadata", {}).get("ColumnInfo", [])
                schema = pa.schema([(c["Name"], arrow_type(c)) for c in columns])
            page_rows = result_set.get("Rows", [])
            # SELECT results have a header on the first page. Athena UTILITY
            # (SHOW/DESCRIBE/EXPLAIN) output is headerless: retain its first row.
            if first_page and execution.get("StatementType") == "DML" and page_rows:
                page_rows = page_rows[1:]
            if "UpdateCount" in response:
                info["update_count"] = response["UpdateCount"]
            for raw_row in page_rows:
                values = [cell.get("VarCharValue") for cell in raw_row.get("Data", [])]
                if len(values) != len(columns):
                    raise ValueError("Athena result row width differs from column metadata")
                row_bytes = sum(len(value.encode("utf-8")) if value is not None else 0 for value in values)
                reason = "max_rows" if len(rows) >= max_rows else None
                if byte_count + row_bytes > self.config.max_result_bytes:
                    reason = "max_result_bytes"
                if reason:
                    info.update(truncated=True, truncation_reason=reason)
                    break
                rows.append([convert_value(value, field.type) for value, field in zip(values, schema, strict=True)])
                byte_count += row_bytes
            next_token = response.get("NextToken")
            if info["truncated"] or not next_token:
                break
            if len(rows) >= max_rows:
                info.update(truncated=True, truncation_reason="max_rows")
                break
            if next_token in seen_tokens:
                raise RuntimeError("Athena repeated a result pagination token")
            seen_tokens.add(next_token)
            token = next_token
        info["result_bytes"] = byte_count
        arrays = [pa.array([row[i] for row in rows], type=field.type) for i, field in enumerate(schema)]
        return pa.Table.from_arrays(arrays, schema=schema), info

    def _execute(
        self,
        sql: str,
        result_format: str,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        *,
        non_query: bool = False,
        fetch_update_count: bool = False,
        max_rows: int | None = None,
        client_request_token: str | None = None,
    ) -> AthenaExecuteSQLResult:
        query_id = None
        execution: dict[str, Any] = {}
        info = {}
        try:
            if result_format not in {"csv", "arrow", "pandas", "list", ""}:
                raise ValueError(f"Unsupported result format: {result_format}")
            catalog, database = self._context(catalog_name, database_name, schema_name)
            query_id = self._start(sql, catalog, database, client_request_token)
            self._wait(query_id, execution)
            if non_query:
                count = None
                if fetch_update_count:
                    response = self._client.get_query_results(QueryExecutionId=query_id, MaxResults=1)
                    count = response.get("UpdateCount")
                payload = None
            else:
                table, info = self._fetch(
                    query_id, execution, min(self.config.max_rows, max_rows or self.config.max_rows)
                )
                count = table.num_rows
                if result_format == "arrow":
                    payload = table
                elif result_format == "list":
                    if len(set(table.column_names)) != len(table.column_names):
                        raise ValueError("List results require unique column names; use SQL aliases or Arrow")
                    payload = table.to_pylist()
                elif result_format == "pandas":
                    payload = table.to_pandas()
                else:
                    payload = table.to_pandas().to_csv(index=False)
            return AthenaExecuteSQLResult(
                success=True,
                sql_query=sql,
                sql_return=payload,
                row_count=count,
                result_format=result_format,
                query_execution_id=query_id,
                execution_info=dict(self._diagnostics(execution), **info),
            )
        except Exception as exc:
            if query_id and execution.get("Status", {}).get("State") not in _TERMINAL:
                info = self._cancel(query_id, execution)
            error = f"{exc} [QueryExecutionId={query_id}]" if query_id else str(exc)
            logger.warning("Athena execution failed: %s", error)
            return AthenaExecuteSQLResult(
                success=False,
                error=error,
                sql_query=sql,
                result_format=result_format,
                query_execution_id=query_id,
                execution_info=dict(self._diagnostics(execution), **info),
            )

    def execute_query(
        self,
        sql: str,
        result_format: Literal["csv", "arrow", "pandas", "list"] = "csv",
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        *,
        client_request_token: str | None = None,
    ) -> AthenaExecuteSQLResult:
        return self._execute(
            sql, result_format, catalog_name, database_name, schema_name, client_request_token=client_request_token
        )

    def execute_explain(
        self,
        sql: str,
        result_format: Literal["csv", "arrow", "pandas", "list"] = "csv",
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> AthenaExecuteSQLResult:
        return self.execute_query(sql, result_format, catalog_name, database_name, schema_name)

    def execute_arrow(self, sql: str) -> AthenaExecuteSQLResult:
        return self.execute_query(sql, "arrow")

    def execute_pandas(self, sql: str) -> AthenaExecuteSQLResult:
        return self.execute_query(sql, "pandas")

    def execute_csv(self, sql: str) -> AthenaExecuteSQLResult:
        return self.execute_query(sql, "csv")

    def execute_ddl(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> AthenaExecuteSQLResult:
        return self._execute(sql, "", catalog_name, database_name, schema_name, non_query=True)

    def execute_insert(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> AthenaExecuteSQLResult:
        return self._execute(sql, "", catalog_name, database_name, schema_name, non_query=True, fetch_update_count=True)

    def execute_update(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> AthenaExecuteSQLResult:
        return self.execute_insert(sql, catalog_name, database_name, schema_name)

    def execute_delete(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> AthenaExecuteSQLResult:
        return self.execute_insert(sql, catalog_name, database_name, schema_name)

    def execute_content_set(self, sql_query: str) -> AthenaExecuteSQLResult:
        return AthenaExecuteSQLResult(
            success=False,
            sql_query=sql_query,
            error="Athena session statements and transactions are unsupported; pass catalog/database as context",
        )

    def execute_queries(self, queries: list[str]) -> list[AthenaExecuteSQLResult]:
        return [self.execute({"sql_query": sql}) for sql in queries]

    def execute_csv_iterator(
        self, query: str, max_rows: int = 100, with_header: bool = True
    ) -> Iterator[tuple[str, ...]]:
        if max_rows < 0:
            raise ValueError("max_rows must not be negative")
        if max_rows == 0:
            return
        result = self._execute(query, "arrow", max_rows=max_rows)
        if not result.success:
            raise RuntimeError(result.error)
        table = result.sql_return
        if with_header:
            yield tuple(table.column_names)
        for row in zip(*(column.to_pylist() for column in table.columns), strict=True):
            yield tuple("" if value is None else str(value) for value in row)

    def _pages(self, operation: str, key: str, **kwargs) -> Iterator[dict[str, Any]]:
        self.connect()
        request = dict(WorkGroup=self.config.workgroup, MaxResults=50, **kwargs)
        seen = set()
        while True:
            response = getattr(self._client, operation)(**request)
            yield from response.get(key, [])
            token = response.get("NextToken")
            if not token:
                return
            if token in seen:
                raise RuntimeError("Athena repeated a metadata pagination token")
            seen.add(token)
            request["NextToken"] = token

    def get_catalogs(self) -> list[str]:
        return [
            c["CatalogName"]
            for c in self._pages("list_data_catalogs", "DataCatalogsSummary")
            if c.get("Type") == "GLUE"
        ]

    def get_databases(self, catalog_name: str = "", include_sys: bool = False) -> list[str]:
        catalog, _ = self._context(catalog_name)
        return [
            d["Name"]
            for d in self._pages("list_databases", "DatabaseList", CatalogName=catalog)
            if include_sys or d["Name"] != "information_schema"
        ]

    def _objects(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> tuple[str, str, Iterator[dict[str, Any]]]:
        catalog, database = self._context(catalog_name, database_name, schema_name)
        if not database:
            raise ValueError("An Athena database is required for table metadata")
        return (
            catalog,
            database,
            self._pages("list_table_metadata", "TableMetadataList", CatalogName=catalog, DatabaseName=database),
        )

    @staticmethod
    def _object_type(metadata: dict) -> str:
        return "view" if metadata.get("TableType") == "VIRTUAL_VIEW" else "table"

    def _names(self, kind: str, catalog_name: str, database_name: str, schema_name: str) -> list[str]:
        _, _, objects = self._objects(catalog_name, database_name, schema_name)
        return [obj["Name"] for obj in objects if self._object_type(obj) == kind]

    def get_tables(self, catalog_name: str = "", database_name: str = "", schema_name: str = "") -> list[str]:
        return self._names("table", catalog_name, database_name, schema_name)

    def get_views(self, catalog_name: str = "", database_name: str = "", schema_name: str = "") -> list[str]:
        return self._names("view", catalog_name, database_name, schema_name)

    def _table_metadata(self, catalog: str, database: str, table: str) -> dict[str, Any]:
        if not database:
            raise ValueError("An Athena database is required for table metadata")
        self.connect()
        return self._client.get_table_metadata(
            CatalogName=catalog, DatabaseName=database, TableName=table, WorkGroup=self.config.workgroup
        )["TableMetadata"]

    def get_schema(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = "", table_name: str = ""
    ) -> list[dict[str, Any]]:
        catalog, database, table = self._resolve_table(table_name, catalog_name, database_name, schema_name)
        meta = self._table_metadata(catalog, database, table)
        partitions = {column["Name"] for column in meta.get("PartitionKeys", [])}
        columns = {column["Name"]: column for column in meta.get("Columns", []) + meta.get("PartitionKeys", [])}
        return [
            dict(
                cid=i,
                name=c["Name"],
                type=c["Type"],
                comment=c.get("Comment"),
                nullable=True,
                pk=False,
                default_value=None,
                is_partition=c["Name"] in partitions,
            )
            for i, c in enumerate(columns.values())
        ]

    def _entry(self, catalog: str, database: str, table: str, kind: str) -> dict[str, Any]:
        return dict(
            identifier=self.identifier(catalog_name=catalog, database_name=database, table_name=table),
            catalog_name=catalog,
            database_name=database,
            schema_name="",
            table_name=table,
            table_type=kind,
        )

    def _with_ddl(
        self,
        kind: str,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        tables: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        catalog, database, objects = self._objects(catalog_name, database_name, schema_name)
        requested = set()
        for table in tables or []:
            resolved = self._resolve_table(table, catalog, database)
            if resolved[:2] != (catalog, database):
                raise ValueError(f"Table {table!r} is outside requested catalog/database")
            requested.add(resolved[2])
        entries = []
        for meta in objects:
            name = meta["Name"]
            if self._object_type(meta) != kind or (requested and name not in requested):
                continue
            # SHOW CREATE uses the request's catalog/database context. Hive DDL
            # backticks and Trino view quoting are intentionally distinct.
            quoted = self.quote_identifier(name) if kind == "view" else "`" + name.replace("`", "``") + "`"
            result = self.execute_query(
                f"SHOW CREATE {'VIEW' if kind == 'view' else 'TABLE'} {quoted}", "arrow", catalog, database
            )
            if not result.success or result.execution_info.get("truncated"):
                raise RuntimeError(result.error or "Athena DDL result exceeded the configured result budget")
            table = result.sql_return
            if table.num_columns != 1 or not table.num_rows or table.column(0).null_count:
                raise RuntimeError("Athena SHOW CREATE did not return a complete DDL definition")
            definition = "\n".join(str(row[0]) for row in zip(*(c.to_pylist() for c in table.columns), strict=True))
            entries.append(dict(self._entry(catalog, database, name, kind), definition=definition))
        return entries

    def get_tables_with_ddl(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = "", tables: list[str] | None = None
    ) -> list[dict[str, Any]]:
        return self._with_ddl("table", catalog_name, database_name, schema_name, tables)

    def get_views_with_ddl(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> list[dict[str, Any]]:
        return self._with_ddl("view", catalog_name, database_name, schema_name)

    def get_sample_rows(
        self,
        tables: list[str] | None = None,
        top_n: int = 5,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_type: Literal["table", "view", "mv", "full"] = "table",
    ) -> list[dict[str, Any]]:
        if top_n <= 0:
            raise ValueError("top_n must be positive")
        if table_type not in {"table", "view", "full", "mv"}:
            raise ValueError(f"Unsupported table_type: {table_type}")
        catalog, database = self._context(catalog_name, database_name, schema_name)
        targets = []
        if tables:
            for name in tables:
                c, d, t = self._resolve_table(name, catalog, database)
                targets.append((c, d, self._table_metadata(c, d, t)))
        else:
            c, d, objects = self._objects(catalog, database)
            targets = [(c, d, meta) for meta in objects]
        entries = []
        for c, d, meta in targets:
            kind = self._object_type(meta)
            if table_type not in {"full", kind}:
                continue
            name = meta["Name"]
            result = self._execute(
                f"SELECT * FROM {self.full_name(c, d, table_name=name)} LIMIT {int(top_n)}", "csv", c, d, max_rows=top_n
            )
            if not result.success or result.execution_info.get("truncated"):
                raise RuntimeError(result.error or "Athena sample exceeded the configured result budget")
            entries.append(dict(self._entry(c, d, name, kind), sample_rows=result.sql_return))
        return entries

    def test_connection(self) -> bool:
        self.connect()
        # Discovery includes built-in catalogs that do not necessarily have a
        # separately registered GetDataCatalog record in a fresh AWS account.
        if self.catalog_name not in self.get_catalogs():
            raise ValueError("The configured catalog must be an accessible Glue data catalog")
        if self.database_name:
            self._client.get_database(
                CatalogName=self.catalog_name, DatabaseName=self.database_name, WorkGroup=self.config.workgroup
            )
        else:
            # Trigger a real metadata permission check even with no default DB.
            list(self._pages("list_databases", "DatabaseList", CatalogName=self.catalog_name))
        result = self.execute_query("SELECT 1 AS datus_connection_test", "list")
        if not result.success:
            raise RuntimeError(result.error)
        if result.sql_return != [{"datus_connection_test": 1}]:
            raise RuntimeError("Athena connection query returned an unexpected result")
        return True

    def describe_migration_capabilities(self) -> dict[str, Any]:
        return dict(
            supported=False,
            dialect_family="athena",
            requires=[],
            forbids=[],
            type_hints={},
            notes="Generic dataframe migration is unsupported; DDL/DML depend on catalog and table format",
        )

    def to_dict(self) -> dict[str, Any]:
        return dict(type="athena", **self.config.model_dump())
