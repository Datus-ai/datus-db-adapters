# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

"""Native HANA DB-API adapter. No SQLAlchemy dialect or JVM is required."""

from contextlib import contextmanager
from typing import Any, Iterator

from hdbcli import dbapi

from datus_db_core import BaseSqlConnector, DatusDbException, ErrorCode, ExecuteSQLResult, MigrationTargetMixin
from datus_db_core.mixins import SchemaNamespaceMixin

from .config import HanaConfig
from .handlers import parse_hana_identifier, quote_identifier


class HanaConnector(BaseSqlConnector, SchemaNamespaceMixin, MigrationTargetMixin):
    """One isolated connection per operation; no shared mutable SDK session."""

    def __init__(self, config: HanaConfig | dict):
        if isinstance(config, dict):
            config = HanaConfig(**config)
        if not isinstance(config, HanaConfig):
            raise TypeError("config must be HanaConfig or dict")
        super().__init__(config, "hana")
        self._default_schema = config.schema_name or config.username or ""

    def _validate_context(self, catalog_name: str = "", database_name: str = "") -> None:
        if catalog_name or (database_name and database_name != self.config.database):
            raise ValueError("HANA tenant is fixed at connection creation; only schema overrides are supported")

    def _connect_args(self, schema_name: str = "") -> dict[str, Any]:
        config = self.config
        args = {
            "connectTimeout": config.timeout_seconds * 1000,
            "communicationTimeout": config.communication_timeout_seconds * 1000,
            "encrypt": config.encrypt,
            "sslValidateCertificate": config.ssl_validate_certificate,
        }
        if config.userkey:
            args["userkey"] = config.userkey
        else:
            args.update(user=config.username, password=config.password)
        if config.host:
            args.update(address=config.host, port=config.port)
        for key, value in (
            ("databaseName", config.database),
            ("currentSchema", schema_name or self.schema_name),
            ("sslTrustStore", config.ssl_trust_store),
            ("sslHostNameInCertificate", config.ssl_host_name_in_certificate),
        ):
            if value:
                args[key] = value
        return args

    def _exception(self, error: Exception, operation: str = "execution") -> DatusDbException:
        if isinstance(error, DatusDbException):
            return error
        message = str(error)
        for secret in (self.config.password, self.config.userkey):
            if secret:
                message = message.replace(secret, "<redacted>")
        code = getattr(error, "errorcode", None)
        mapped = {
            10: ErrorCode.DB_AUTHENTICATION_FAILED,
            257: ErrorCode.DB_EXECUTION_SYNTAX_ERROR,
            258: ErrorCode.DB_PERMISSION_DENIED,
            259: ErrorCode.DB_TABLE_NOT_EXISTS,
            301: ErrorCode.DB_CONSTRAINT_VIOLATION,
            131: ErrorCode.DB_EXECUTION_TIMEOUT,
        }.get(code, ErrorCode.DB_CONNECTION_FAILED if operation == "connection" else ErrorCode.DB_EXECUTION_ERROR)
        return DatusDbException(mapped, message=f"HANA {operation} failed (code={code}): {message}")

    @staticmethod
    def _close(resource: Any) -> None:
        if resource is not None:
            try:
                resource.close()
            except Exception:
                pass

    @contextmanager
    def _session(self, schema_name: str = "") -> Iterator[Any]:
        connection = None
        try:
            try:
                connection = dbapi.connect(**self._connect_args(schema_name))
                connection.setautocommit(False)
            except Exception as error:
                raise self._exception(error, "connection") from None
            yield connection
            connection.commit()
        except Exception:
            if connection is not None:
                try:
                    connection.rollback()
                except Exception:
                    pass
            raise
        finally:
            self._close(connection)

    def connect(self) -> None:
        self.test_connection()

    def close(self) -> None:
        # Each operation owns and closes its connection, including failures.
        return None

    def test_connection(self) -> bool:
        result = self.execute_query("SELECT 1 FROM DUMMY", "list")
        if not result.success:
            raise DatusDbException(ErrorCode.DB_CONNECTION_FAILED, message=result.error)
        return result.row_count == 1 and list(result.sql_return[0].values()) == [1]

    @staticmethod
    def quote_identifier(name: str) -> str:
        return quote_identifier(name)

    def full_name(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = "", table_name: str = ""
    ) -> str:
        self._validate_context(catalog_name, database_name)
        return ".".join(quote_identifier(part) for part in (schema_name or self.schema_name, table_name) if part)

    def identifier(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = "", table_name: str = ""
    ) -> str:
        return self.full_name(catalog_name, database_name, schema_name, table_name)

    def switch_context(self, catalog_name: str = "", database_name: str = "", schema_name: str = "") -> None:
        self._validate_context(catalog_name, database_name)
        if schema_name:
            with self._session(schema_name):
                pass
            self.schema_name = schema_name

    @staticmethod
    def _column_names(cursor) -> list[str]:
        names = [column[0] for column in cursor.description]
        if len(set(names)) != len(names):
            raise ValueError("Duplicate result column names require explicit SQL aliases")
        return names

    def _rows(self, cursor, max_rows: int | None = None) -> Iterator[tuple]:
        row_count = 0
        size = 0
        while max_rows is None or row_count < max_rows:
            batch_size = min(512, self.config.max_result_rows + 1 - row_count)
            if max_rows is not None:
                batch_size = min(batch_size, max_rows - row_count)
            batch = cursor.fetchmany(batch_size)
            if not batch:
                break
            for row in batch:
                row_count += 1
                if row_count > self.config.max_result_rows:
                    raise ValueError("HANA result row budget exceeded; narrow the query")
                values = []
                for value in row:
                    if hasattr(value, "read"):
                        value = value.read(self.config.max_result_bytes + 1)
                    size += (
                        memoryview(value).nbytes
                        if isinstance(value, (bytes, bytearray, memoryview))
                        else len(str(value).encode("utf-8"))
                    )
                    if size > self.config.max_result_bytes:
                        raise ValueError("HANA result byte budget exceeded; narrow the query")
                    values.append(value)
                yield tuple(values)

    @staticmethod
    def _format(names: list[str], rows: list[tuple], result_format: str):
        if result_format == "list":
            return [dict(zip(names, row, strict=True)) for row in rows]
        if result_format == "arrow":
            import pyarrow as pa

            return pa.table({name: [row[i] for row in rows] for i, name in enumerate(names)})
        import pandas as pd

        frame = pd.DataFrame(rows, columns=names, dtype=object)
        return frame if result_format == "pandas" else frame.to_csv(index=False)

    def _cursor_result(self, cursor: Any, sql: str, result_format: str) -> ExecuteSQLResult:
        if cursor.description:
            names = self._column_names(cursor)
            rows = list(self._rows(cursor))
            value = self._format(names, rows, result_format)
            if result_format == "csv" and len(value.encode("utf-8")) > self.config.max_result_bytes:
                raise ValueError("HANA CSV result byte budget exceeded; narrow the query")
            return ExecuteSQLResult(
                success=True, sql_query=sql, row_count=len(rows), sql_return=value, result_format=result_format
            )
        count = max(cursor.rowcount, 0)
        return ExecuteSQLResult(
            success=True, sql_query=sql, row_count=count, sql_return=str(count), result_format=result_format
        )

    def execute_query(
        self, sql, result_format="csv", catalog_name="", database_name="", schema_name="", *, parameters=None
    ):
        try:
            self._validate_context(catalog_name, database_name)
            if result_format not in {"list", "csv", "pandas", "arrow"}:
                raise ValueError("result_format must be list, csv, pandas or arrow")
            with self._session(schema_name) as connection:
                cursor = None
                try:
                    cursor = connection.cursor()
                    if parameters is None:
                        cursor.execute(sql)
                    else:
                        cursor.execute(sql, parameters)
                    return self._cursor_result(cursor, sql, result_format)
                finally:
                    self._close(cursor)
        except Exception as error:
            return ExecuteSQLResult(
                success=False,
                error=str(self._exception(error)),
                sql_query=sql,
                row_count=0,
                sql_return="",
                result_format=result_format,
            )

    def execute(self, input_params, result_format=None, catalog_name="", database_name="", schema_name=""):
        self.validate_input(input_params)
        sql = input_params["sql_query"] if isinstance(input_params, dict) else input_params.sql_query
        fmt = result_format or (
            input_params.get("result_format", "csv")
            if isinstance(input_params, dict)
            else getattr(input_params, "result_format", "csv")
        )
        # Native execution is intentionally independent of local analysis.
        # Datus tools validate permissions before dispatching to the SDK.
        return self.execute_query(sql, fmt, catalog_name, database_name, schema_name)

    def execute_insert(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        return self.execute_query(sql, "list", catalog_name, database_name, schema_name)

    execute_update = execute_insert
    execute_delete = execute_insert
    execute_ddl = execute_insert

    def execute_pandas(self, sql: str) -> ExecuteSQLResult:
        return self.execute_query(sql, "pandas")

    def execute_arrow(self, sql: str) -> ExecuteSQLResult:
        return self.execute_query(sql, "arrow")

    def execute_csv(self, sql: str) -> ExecuteSQLResult:
        return self.execute_query(sql, "csv")

    def execute_queries(self, queries: list[str]) -> list[ExecuteSQLResult]:
        """One transaction for DML batches. HANA DDL may commit implicitly."""
        try:
            with self._session() as connection:
                cursor = None
                try:
                    cursor = connection.cursor()
                    results = []
                    for sql in queries:
                        cursor.execute(sql)
                        results.append(self._cursor_result(cursor, sql, "list"))
                    return results
                finally:
                    self._close(cursor)
        except Exception as error:
            raise self._exception(error) from None

    def execute_content_set(self, sql_query: str) -> ExecuteSQLResult:
        return ExecuteSQLResult(
            success=False, error="Use switch_context(schema_name=...) or configure schema", sql_query=sql_query
        )

    def execute_csv_iterator(self, query: str, max_rows=100, with_header=True) -> Iterator[tuple]:
        """Yield at most max_rows; consume or close the iterator to release its session."""
        if max_rows < 0:
            raise ValueError("max_rows must be nonnegative")
        try:
            with self._session() as connection:
                cursor = None
                try:
                    cursor = connection.cursor()
                    cursor.execute(query)
                    names = self._column_names(cursor) if cursor.description else []
                    if with_header:
                        yield tuple(names)
                    if cursor.description:
                        yield from self._rows(cursor, max_rows=max_rows)
                finally:
                    self._close(cursor)
        except Exception as error:
            raise ValueError(str(self._exception(error))) from None

    def _metadata_rows(self, sql, parameters=None, schema_name="") -> list[dict]:
        result = self.execute_query(sql, "list", schema_name=schema_name, parameters=parameters)
        if not result.success:
            raise DatusDbException(ErrorCode.DB_QUERY_METADATA_FAILED, message=result.error)
        return result.sql_return

    def _sys_schemas(self) -> set[str]:
        return {"SYS", "SYSTEM", "PUBLIC"}

    def _effective_schema(self, schema_name: str = "") -> str:
        return schema_name or self.schema_name

    def get_databases(self, catalog_name="", include_sys=False):
        self._validate_context(catalog_name)
        return []

    def get_schemas(self, catalog_name="", database_name="", include_sys=False):
        self._validate_context(catalog_name, database_name)
        rows = self._metadata_rows("SELECT SCHEMA_NAME FROM SYS.SCHEMAS ORDER BY SCHEMA_NAME")
        return [
            row["SCHEMA_NAME"]
            for row in rows
            if include_sys
            or (row["SCHEMA_NAME"] not in self._sys_schemas() and not row["SCHEMA_NAME"].startswith("_SYS_"))
        ]

    def _objects(self, kind, catalog_name="", database_name="", schema_name=""):
        self._validate_context(catalog_name, database_name)
        if kind not in {"table", "view"}:
            raise ValueError("HANA supports table and view metadata, not materialized views")
        view, name = ("TABLES", "TABLE_NAME") if kind == "table" else ("VIEWS", "VIEW_NAME")
        schema = self._effective_schema(schema_name)
        rows = self._metadata_rows(
            f"SELECT SCHEMA_NAME, {name} AS OBJECT_NAME FROM SYS.{view} WHERE SCHEMA_NAME = ? ORDER BY {name}",
            (schema,),
        )
        return [
            {
                "identifier": self.identifier(schema_name=row["SCHEMA_NAME"], table_name=row["OBJECT_NAME"]),
                "catalog_name": "",
                "database_name": "",
                "schema_name": row["SCHEMA_NAME"],
                "table_name": row["OBJECT_NAME"],
                "table_type": kind,
            }
            for row in rows
        ]

    def get_tables(self, catalog_name="", database_name="", schema_name=""):
        return [row["table_name"] for row in self._objects("table", catalog_name, database_name, schema_name)]

    def get_views(self, catalog_name="", database_name="", schema_name=""):
        return [row["table_name"] for row in self._objects("view", catalog_name, database_name, schema_name)]

    @staticmethod
    def _column_type(row: dict) -> str:
        kind = row["DATA_TYPE_NAME"]
        if kind in {"DECIMAL", "SMALLDECIMAL"} and row["LENGTH"] and row["SCALE"] is not None:
            return f"{kind}({row['LENGTH']},{row['SCALE']})" if kind == "DECIMAL" else kind
        if kind in {"VARCHAR", "NVARCHAR", "CHAR", "NCHAR", "VARBINARY", "BINARY"} and row["LENGTH"]:
            return f"{kind}({row['LENGTH']})"
        return kind

    def get_schema(self, catalog_name="", database_name="", schema_name="", table_name=""):
        self._validate_context(catalog_name, database_name)
        if not table_name:
            raise ValueError("table_name is required")
        schema = self._effective_schema(schema_name)
        # Catalog data is parameterized. The UNION includes view columns too.
        sql = """SELECT COLUMN_NAME, DATA_TYPE_NAME, LENGTH, SCALE, IS_NULLABLE, POSITION, COMMENTS, DEFAULT_VALUE
                 FROM SYS.TABLE_COLUMNS WHERE SCHEMA_NAME = ? AND TABLE_NAME = ?
                 UNION ALL
                 SELECT COLUMN_NAME, DATA_TYPE_NAME, LENGTH, SCALE, IS_NULLABLE, POSITION, COMMENTS, DEFAULT_VALUE
                 FROM SYS.VIEW_COLUMNS WHERE SCHEMA_NAME = ? AND VIEW_NAME = ?
                 ORDER BY POSITION"""
        rows = self._metadata_rows(sql, (schema, table_name, schema, table_name))
        return [
            {
                "cid": row["POSITION"] - 1,
                "name": row["COLUMN_NAME"],
                "type": self._column_type(row),
                "nullable": row["IS_NULLABLE"] == "TRUE",
                "comment": row["COMMENTS"],
                "default_value": row["DEFAULT_VALUE"],
            }
            for row in rows
        ]

    def get_tables_with_ddl(self, catalog_name="", database_name="", schema_name="", tables=None):
        result = []
        for meta in self._objects("table", catalog_name, database_name, schema_name):
            if tables and meta["table_name"] not in tables and meta["identifier"] not in tables:
                continue
            columns = self.get_schema(schema_name=meta["schema_name"], table_name=meta["table_name"])
            if not columns:
                raise ValueError("No column metadata available for the discovered HANA table")
            # A column-only description for discovery, not lossless migration DDL.
            fields = ", ".join(
                f"{quote_identifier(col['name'])} {col['type']}" + ("" if col["nullable"] else " NOT NULL")
                for col in columns
            )
            meta["definition"] = f"CREATE TABLE {meta['identifier']} ({fields})"
            result.append(meta)
        return result

    def get_views_with_ddl(self, catalog_name="", database_name="", schema_name=""):
        result = []
        for meta in self._objects("view", catalog_name, database_name, schema_name):
            rows = self._metadata_rows(
                "SELECT DEFINITION FROM SYS.VIEWS WHERE SCHEMA_NAME = ? AND VIEW_NAME = ?",
                (meta["schema_name"], meta["table_name"]),
            )
            if not rows or not rows[0]["DEFINITION"]:
                raise ValueError("No definition available for the discovered HANA view")
            meta["definition"] = rows[0]["DEFINITION"]
            result.append(meta)
        return result

    def get_sample_rows(
        self, tables=None, top_n=5, catalog_name="", database_name="", schema_name="", table_type="table"
    ):
        self._validate_context(catalog_name, database_name)
        if top_n < 0:
            raise ValueError("top_n must be nonnegative")
        schema = self._effective_schema(schema_name)
        if tables:
            objects = []
            for name in tables:
                parts = parse_hana_identifier(name)
                objects.append(
                    {
                        "catalog_name": "",
                        "database_name": "",
                        "schema_name": parts["schema_name"] or schema,
                        "table_name": parts["table_name"],
                        "table_type": table_type,
                    }
                )
        else:
            kinds = ("table", "view") if table_type == "full" else (table_type,)
            objects = [obj for kind in kinds for obj in self._objects(kind, schema_name=schema)]
        result = []
        for meta in objects:
            identifier = self.identifier(schema_name=meta["schema_name"], table_name=meta["table_name"])
            sample = self.execute_query(f"SELECT * FROM {identifier} LIMIT {int(top_n)}", "csv")
            if not sample.success:
                raise ValueError(sample.error)
            result.append(dict(meta, identifier=identifier, sample_rows=sample.sql_return))
        return result

    def describe_migration_capabilities(self) -> dict[str, Any]:
        return {
            "supported": False,
            "dialect_family": "hana",
            "reason": "Lossless migration DDL and arbitrary HANA SQL analysis are not implemented",
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "hana",
            "host": self.config.host,
            "port": self.config.port,
            "database": self.config.database,
            "schema": self.schema_name,
        }
