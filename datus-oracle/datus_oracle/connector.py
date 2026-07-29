# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import re
from typing import Any, Dict, List, Optional, Set, Union, override

from sqlalchemy import text

from datus_db_core import (
    TABLE_TYPE,
    DatusDbException,
    ErrorCode,
    get_logger,
)
from datus_sqlalchemy import SQLAlchemyConnector

from .config import OracleConfig
from .dialect_operations import quote_oracle_identifier
from .handlers import build_oracle_uri

logger = get_logger(__name__)

_ORA_CODE_RE = re.compile(r"ORA-(\d{5})")

# Object-type names for the ALL_* dictionary views and DBMS_METADATA
_METADATA_VIEWS: Dict[TABLE_TYPE, Dict[str, str]] = {
    "table": {"view": "ALL_TABLES", "name_column": "TABLE_NAME", "ddl_type": "TABLE"},
    "view": {"view": "ALL_VIEWS", "name_column": "VIEW_NAME", "ddl_type": "VIEW"},
    "mv": {"view": "ALL_MVIEWS", "name_column": "MVIEW_NAME", "ddl_type": "MATERIALIZED_VIEW"},
}


def _get_metadata_view(table_type: TABLE_TYPE) -> Dict[str, str]:
    if table_type not in _METADATA_VIEWS:
        raise DatusDbException(ErrorCode.COMMON_FIELD_INVALID, f"Invalid table type '{table_type}'")
    return _METADATA_VIEWS[table_type]


class OracleConnector(SQLAlchemyConnector):
    """Oracle database connector (python-oracledb Thin mode)."""

    def __init__(self, config: Union[OracleConfig, dict]):
        if isinstance(config, dict):
            config = OracleConfig(**config)
        elif not isinstance(config, OracleConfig):
            raise TypeError(f"config must be OracleConfig or dict, got {type(config)}")

        self.host = config.host
        self.port = config.port
        self.username = config.username
        self.password = config.password

        connection_string = build_oracle_uri(config)
        super().__init__(
            connection_string,
            dialect="oracle",
            timeout_seconds=config.timeout_seconds,
        )
        # Set after super().__init__() so BaseSqlConnector doesn't overwrite
        # with a plain ConnectionConfig (which lacks service_name, etc.)
        self.config = config
        self._default_schema = (config.schema_name or config.username).upper()

    # ==================== System Resources ====================

    @override
    def _sys_schemas(self) -> Set[str]:
        """Oracle-maintained schemas to filter out."""
        return {
            "SYS",
            "SYSTEM",
            "OUTLN",
            "XDB",
            "CTXSYS",
            "MDSYS",
            "ORDSYS",
            "ORDDATA",
            "OLAPSYS",
            "DBSNMP",
            "APPQOSSYS",
            "GSMADMIN_INTERNAL",
            "WMSYS",
            "OJVMSYS",
            "DVSYS",
            "DVF",
            "LBACSYS",
            "AUDSYS",
            "REMOTE_SCHEDULER_AGENT",
            "SYSBACKUP",
            "SYSDG",
            "SYSKM",
            "SYSRAC",
            "SYS$UMF",
            "DIP",
            "ANONYMOUS",
            "XS$NULL",
            "GGSYS",
        }

    # ==================== Utility Methods ====================

    @override
    def quote_identifier(self, name: str) -> str:
        """Quote as an upper-cased identifier (see OracleDialectOperations)."""
        return quote_oracle_identifier(name)

    @staticmethod
    def _safe_upper(value: str) -> str:
        """Upper-case and escape a value for use inside a SQL string literal."""
        return value.replace("'", "''").upper() if value else ""

    # ==================== Connection ====================

    @override
    def test_connection(self) -> bool:
        """Test database connection with Oracle's mandatory FROM clause."""
        try:
            with self._conn() as conn:
                conn.execute(text("SELECT 1 FROM DUAL"))
            return True
        except Exception as e:
            if isinstance(e, DatusDbException):
                raise
            raise DatusDbException(
                ErrorCode.DB_CONNECTION_FAILED,
                message_args={"error_message": "Connection test failed"},
            ) from e

    @override
    def do_switch_context(self, conn, catalog_name: str = "", database_name: str = "", schema_name: str = ""):
        """Apply schema context. The service/PDB is fixed per connection."""
        if schema_name:
            conn.execute(text(f"ALTER SESSION SET CURRENT_SCHEMA = {self.quote_identifier(schema_name)}"))

    # ==================== Error Mapping ====================

    @override
    def _handle_exception(self, e: Exception, sql: str = "", operation: str = "SQL execution") -> DatusDbException:
        """Map ORA error codes to Datus exceptions.

        The original ORA message text is always preserved in the mapped
        message: Datus Agent's transfer auto-create-table path recognizes a
        missing table by matching the driver's English error text.
        """
        if isinstance(e, DatusDbException):
            return e

        if hasattr(e, "orig") and e.orig is not None:
            error_message = str(e.orig)
        else:
            error_message = str(e)

        match = _ORA_CODE_RE.search(error_message)
        if not match:
            return super()._handle_exception(e, sql, operation)

        ora_code = int(match.group(1))
        message_args = {"error_message": error_message, "sql": sql}

        if ora_code == 1017:
            return DatusDbException(ErrorCode.DB_AUTHENTICATION_FAILED, message_args=message_args)
        if ora_code == 12154 or 12500 <= ora_code <= 12599:
            return DatusDbException(ErrorCode.DB_CONNECTION_FAILED, message_args=message_args)
        if ora_code == 942:
            return DatusDbException(ErrorCode.DB_TABLE_NOT_EXISTS, message_args={"table_name": error_message})
        if ora_code == 1031:
            message_args["operation"] = operation
            return DatusDbException(ErrorCode.DB_PERMISSION_DENIED, message_args=message_args)
        if ora_code == 1:
            return DatusDbException(ErrorCode.DB_CONSTRAINT_VIOLATION, message_args=message_args)
        if ora_code == 1013:
            return DatusDbException(ErrorCode.DB_EXECUTION_TIMEOUT, message_args=message_args)
        if 900 <= ora_code <= 999:
            return DatusDbException(ErrorCode.DB_EXECUTION_SYNTAX_ERROR, message_args=message_args)
        return DatusDbException(ErrorCode.DB_EXECUTION_ERROR, message_args=message_args)

    # ==================== Metadata Retrieval ====================

    def _get_metadata(
        self,
        table_type: TABLE_TYPE = "table",
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> List[Dict[str, str]]:
        """List objects from the ALL_* dictionary views (no DBA_* required)."""
        self.connect()
        schema_name = schema_name or self.schema_name

        meta_view = _get_metadata_view(table_type)
        view = meta_view["view"]
        name_column = meta_view["name_column"]

        if schema_name:
            owner_filter = f"OWNER = '{self._safe_upper(schema_name)}'"
        else:
            sys_list = ", ".join(f"'{s}'" for s in sorted(self._sys_schemas()))
            owner_filter = f"OWNER NOT IN ({sys_list})"

        sql = f"SELECT OWNER, {name_column} AS TABLE_NAME FROM {view} WHERE {owner_filter} ORDER BY {name_column}"
        query_result = self._execute_pandas(sql)

        result = []
        for i in range(len(query_result)):
            owner = query_result["owner"][i] if "owner" in query_result.columns else query_result["OWNER"][i]
            tb_name = (
                query_result["table_name"][i] if "table_name" in query_result.columns else query_result["TABLE_NAME"][i]
            )
            result.append(
                {
                    "identifier": self.identifier(schema_name=owner, table_name=tb_name),
                    "catalog_name": "",
                    "database_name": "",
                    "schema_name": owner,
                    "table_name": tb_name,
                    "table_type": table_type,
                }
            )
        return result

    def _get_ddl(self, schema_name: str, table_name: str, ddl_type: str = "TABLE") -> str:
        """Get DDL via DBMS_METADATA.GET_DDL (transcribed, never inferred)."""
        full_name = self.full_name(schema_name=schema_name, table_name=table_name)
        safe_schema = self._safe_upper(schema_name)
        safe_table = self._safe_upper(table_name)
        sql = f"SELECT DBMS_METADATA.GET_DDL('{ddl_type}', '{safe_table}', '{safe_schema}') AS DDL FROM DUAL"
        result = self._execute_pandas(sql)
        column = "ddl" if "ddl" in result.columns else "DDL"
        if not result.empty and result[column][0]:
            return str(result[column][0]).strip()
        return f"-- DDL not available for {full_name}"

    def _get_objects_with_ddl(
        self,
        table_type: TABLE_TYPE = "table",
        tables: Optional[List[str]] = None,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> List[Dict[str, str]]:
        result = []
        filter_tables = self._reset_filter_tables(tables, catalog_name, database_name, schema_name)
        ddl_type = _get_metadata_view(table_type)["ddl_type"]

        for meta in self._get_metadata(table_type, catalog_name, database_name, schema_name):
            full_name = self.full_name(schema_name=meta["schema_name"], table_name=meta["table_name"])
            if filter_tables and full_name not in filter_tables:
                continue
            try:
                ddl = self._get_ddl(meta["schema_name"], meta["table_name"], ddl_type)
            except Exception as e:
                logger.warning(f"Could not get DDL for {full_name}: {e}")
                ddl = f"-- DDL not available for {meta['table_name']}"
            meta["definition"] = ddl
            result.append(meta)

        return result

    @staticmethod
    def _qualify_name(meta, arg_schema):
        parts = []
        if not arg_schema and meta.get("schema_name"):
            parts.append(meta["schema_name"])
        parts.append(meta["table_name"])
        return ".".join(parts)

    @override
    def get_tables(self, catalog_name: str = "", database_name: str = "", schema_name: str = "") -> List[str]:
        return [
            self._qualify_name(meta, schema_name)
            for meta in self._get_metadata("table", catalog_name, database_name, schema_name)
        ]

    @override
    def get_views(self, catalog_name: str = "", database_name: str = "", schema_name: str = "") -> List[str]:
        return [
            self._qualify_name(meta, schema_name)
            for meta in self._get_metadata("view", catalog_name, database_name, schema_name)
        ]

    @override
    def get_materialized_views(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> List[str]:
        return [
            self._qualify_name(meta, schema_name)
            for meta in self._get_metadata("mv", catalog_name, database_name, schema_name)
        ]

    @override
    def get_tables_with_ddl(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        tables: Optional[List[str]] = None,
    ) -> List[Dict[str, str]]:
        return self._get_objects_with_ddl("table", tables, catalog_name, database_name, schema_name)

    @override
    def get_views_with_ddl(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> List[Dict[str, str]]:
        return self._get_objects_with_ddl("view", None, catalog_name, database_name, schema_name)

    @override
    def get_schema(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
    ) -> List[Dict[str, Any]]:
        """Get column info from ALL_TAB_COLUMNS with PK and comments."""
        if not table_name:
            return []

        schema_name = schema_name or self.schema_name
        safe_schema = self._safe_upper(schema_name)
        safe_table = self._safe_upper(table_name)

        sql = f"""
            SELECT
                c.COLUMN_NAME AS field,
                c.DATA_TYPE AS data_type,
                c.DATA_LENGTH AS data_length,
                c.DATA_PRECISION AS data_precision,
                c.DATA_SCALE AS data_scale,
                c.NULLABLE AS nullable,
                c.DATA_DEFAULT AS default_value,
                CASE WHEN pk.COLUMN_NAME IS NOT NULL THEN 1 ELSE 0 END AS is_pk,
                cc.COMMENTS AS col_comment
            FROM ALL_TAB_COLUMNS c
            LEFT JOIN (
                SELECT acc.OWNER, acc.TABLE_NAME, acc.COLUMN_NAME
                FROM ALL_CONSTRAINTS ac
                JOIN ALL_CONS_COLUMNS acc
                    ON ac.CONSTRAINT_NAME = acc.CONSTRAINT_NAME
                    AND ac.OWNER = acc.OWNER
                WHERE ac.CONSTRAINT_TYPE = 'P'
                    AND ac.OWNER = '{safe_schema}'
                    AND ac.TABLE_NAME = '{safe_table}'
            ) pk ON pk.OWNER = c.OWNER
                AND pk.TABLE_NAME = c.TABLE_NAME
                AND pk.COLUMN_NAME = c.COLUMN_NAME
            LEFT JOIN ALL_COL_COMMENTS cc
                ON cc.OWNER = c.OWNER
                AND cc.TABLE_NAME = c.TABLE_NAME
                AND cc.COLUMN_NAME = c.COLUMN_NAME
            WHERE c.OWNER = '{safe_schema}'
              AND c.TABLE_NAME = '{safe_table}'
            ORDER BY c.COLUMN_ID
        """

        query_result = self._execute_pandas(sql)

        result = []
        for i in range(len(query_result)):
            result.append(
                {
                    "cid": i,
                    "name": query_result["field"][i],
                    "type": self._format_column_type(
                        query_result["data_type"][i],
                        query_result["data_length"][i],
                        query_result["data_precision"][i],
                        query_result["data_scale"][i],
                    ),
                    "nullable": query_result["nullable"][i] == "Y",
                    "default_value": query_result["default_value"][i],
                    "pk": bool(query_result["is_pk"][i]),
                    "comment": query_result["col_comment"][i] if query_result["col_comment"][i] else None,
                }
            )
        return result

    @staticmethod
    def _format_column_type(data_type: str, data_length: Any, data_precision: Any, data_scale: Any) -> str:
        """Render an Oracle column type with its length/precision/scale."""
        import pandas as pd

        data_type = str(data_type)

        def _num(value) -> Optional[int]:
            if value is None or (isinstance(value, float) and pd.isna(value)):
                return None
            return int(value)

        precision = _num(data_precision)
        scale = _num(data_scale)
        length = _num(data_length)

        if data_type == "NUMBER":
            if precision is not None and scale is not None and scale != 0:
                return f"NUMBER({precision},{scale})"
            if precision is not None:
                return f"NUMBER({precision})"
            return "NUMBER"
        if data_type in ("CHAR", "VARCHAR2", "NCHAR", "NVARCHAR2", "RAW") and length is not None:
            return f"{data_type}({length})"
        return data_type

    # ==================== Schema Management ====================

    @override
    def get_databases(self, catalog_name: str = "", include_sys: bool = False) -> List[str]:
        """Oracle has no database level below the service; namespace is schema-only."""
        return []

    @override
    def get_schemas(self, catalog_name: str = "", database_name: str = "", include_sys: bool = False) -> List[str]:
        """List schemas from ALL_USERS (accessible to any user)."""
        result = self._execute_pandas("SELECT USERNAME FROM ALL_USERS ORDER BY USERNAME")
        column = "username" if "username" in result.columns else "USERNAME"
        schemas = result[column].tolist()
        if not include_sys:
            sys_schemas = self._sys_schemas()
            schemas = [s for s in schemas if s not in sys_schemas]
        return schemas

    @override
    def _sqlalchemy_schema(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> Optional[str]:
        return schema_name or self.schema_name

    # ==================== Sample Data ====================

    def get_sample_rows(
        self,
        tables: Optional[List[str]] = None,
        top_n: int = 5,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_type: TABLE_TYPE = "table",
    ) -> List[Dict[str, str]]:
        """Get sample rows using FETCH FIRST (Oracle has no LIMIT clause)."""
        self.connect()
        schema_name = schema_name or self.schema_name
        result = []

        if tables:
            metadata = [
                {
                    "identifier": self.identifier(schema_name=schema_name, table_name=table_name),
                    "schema_name": schema_name,
                    "table_name": table_name,
                }
                for table_name in tables
            ]
        else:
            if table_type == "full" or table_type not in _METADATA_VIEWS:
                metadata = []
                for one_type in ("table", "view", "mv"):
                    metadata.extend(self._get_metadata(one_type, "", database_name, schema_name))
            else:
                metadata = self._get_metadata(table_type, "", database_name, schema_name)

        for meta in metadata:
            full_name = self.full_name(schema_name=meta["schema_name"], table_name=meta["table_name"])
            sql = f"SELECT * FROM {full_name} FETCH FIRST {int(top_n)} ROWS ONLY"
            try:
                df = self._execute_pandas(sql)
            except Exception as e:
                logger.warning(f"Could not sample {full_name}: {e}")
                continue
            if not df.empty:
                result.append(
                    {
                        "identifier": meta["identifier"],
                        "catalog_name": "",
                        "database_name": "",
                        "schema_name": meta["schema_name"],
                        "table_name": meta["table_name"],
                        "sample_rows": df.to_csv(index=False),
                    }
                )
        return result

    # ==================== Naming ====================

    @override
    def identifier(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
    ) -> str:
        schema_name = schema_name or self.schema_name
        if schema_name:
            return f"{schema_name}.{table_name}"
        return table_name

    @override
    def full_name(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
    ) -> str:
        schema_name = schema_name or self.schema_name
        if schema_name:
            return f"{self.quote_identifier(schema_name)}.{self.quote_identifier(table_name)}"
        return self.quote_identifier(table_name)

    @override
    def _reset_filter_tables(
        self,
        tables: Optional[List[str]] = None,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> List[str]:
        schema_name = schema_name or self.schema_name
        return super()._reset_filter_tables(tables, "", "", schema_name)
