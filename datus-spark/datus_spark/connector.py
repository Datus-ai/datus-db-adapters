# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from typing import Any, Dict, List, Optional, Set, Union, override
from urllib.parse import quote_plus

from datus_db_core import get_logger
from datus_sqlalchemy import SQLAlchemyConnector

from .config import SparkConfig

logger = get_logger(__name__)

SPARK_DIALECT = "spark"


class SparkConnector(SQLAlchemyConnector):
    """
    Spark SQL database connector via HiveServer2/Thrift protocol.

    Spark uses a two-level hierarchy: database -> table.
    Connects via the Hive SQLAlchemy dialect (pyhive).
    """

    def __init__(self, config: Union[SparkConfig, dict]):
        """
        Initialize Spark connector.

        Args:
            config: SparkConfig object or dict with configuration
        """
        if isinstance(config, dict):
            config = SparkConfig(**config)
        elif not isinstance(config, SparkConfig):
            raise TypeError(f"config must be SparkConfig or dict, got {type(config)}")

        self.spark_config = config
        self.host = config.host
        self.port = config.port
        self.user = config.username

        database = config.database or "default"

        # Build connection string: hive://user:pass@host:port/database
        encoded_username = quote_plus(config.username)
        encoded_password = quote_plus(config.password) if config.password else ""
        if config.password:
            auth_part = f"{encoded_username}:{encoded_password}@"
        else:
            auth_part = f"{encoded_username}@"

        # Build connection string with auth mechanism
        connection_string = f"hive://{auth_part}{config.host}:{config.port}/{database}"

        if config.auth_mechanism and config.auth_mechanism != "NONE":
            connection_string += f"?auth={config.auth_mechanism}"

        super().__init__(
            connection_string,
            dialect=SPARK_DIALECT,
            timeout_seconds=config.timeout_seconds,
        )

        self.dialect = SPARK_DIALECT
        self._default_database = database

    # ==================== Context Manager Support ====================

    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit with cleanup."""
        self.close()
        return False

    # ==================== System Resources ====================

    @override
    def _sys_databases(self) -> Set[str]:
        """System databases to filter out."""
        return {"information_schema"}

    @override
    def _sys_schemas(self) -> Set[str]:
        """System schemas to filter out (same as databases for Spark)."""
        return self._sys_databases()

    # ==================== Metadata Retrieval ====================

    @override
    def get_databases(self, catalog_name: str = "", include_sys: bool = False) -> List[str]:
        """Get list of databases."""
        result = self._execute_pandas("SHOW DATABASES")
        if result.empty:
            return []
        databases = result.iloc[:, 0].tolist()
        if not include_sys:
            sys_dbs = self._sys_databases()
            databases = [d for d in databases if d.lower() not in sys_dbs]
        return databases

    @override
    def get_schemas(self, catalog_name: str = "", database_name: str = "", include_sys: bool = False) -> List[str]:
        """Spark doesn't have separate schemas, return empty list."""
        return []

    @override
    @staticmethod
    def _qualify(name, real_db, real_schema, arg_db, arg_schema):
        """Prefix the table with the db/schema levels the caller left blank.

        Yields ``[db.][schema.]table`` so an unscoped listing stays addressable; a level is
        prepended only when the caller passed it empty and the connector resolved that coordinate.
        """
        parts = []
        if not arg_db and real_db:
            parts.append(real_db)
        if not arg_schema and real_schema:
            parts.append(real_schema)
        parts.append(name)
        return ".".join(parts)

    def get_tables(self, catalog_name: str = "", database_name: str = "", schema_name: str = "") -> List[str]:
        """Get list of table names."""
        db = database_name or self.database_name
        result = self._execute_pandas(f"SHOW TABLES IN {self.quote_identifier(db)}")
        if result.empty:
            return []
        # SHOW TABLES returns (namespace, tableName, isTemporary) in Spark 3.x
        # Use the second column (tableName) when available, otherwise first
        if len(result.columns) >= 2:
            name_col = result.columns[1]
        else:
            name_col = result.columns[0]
        return [self._qualify(name, db, "", database_name, "") for name in result[name_col].tolist()]

    @override
    def get_views(self, catalog_name: str = "", database_name: str = "", schema_name: str = "") -> List[str]:
        """Get list of view names."""
        db = database_name or self.database_name
        try:
            result = self._execute_pandas(f"SHOW VIEWS IN {self.quote_identifier(db)}")
            if result.empty:
                return []
            if len(result.columns) >= 2:
                name_col = result.columns[1]
            else:
                name_col = result.columns[0]
            return [self._qualify(name, db, "", database_name, "") for name in result[name_col].tolist()]
        except Exception as e:
            logger.warning(f"Failed to get views: {e}")
            return []

    def _show_create_definition(self, db: str, table: str) -> str:
        """Run `SHOW CREATE TABLE` and return the first cell (the DDL text)."""
        sql = f"SHOW CREATE TABLE {self.full_name(database_name=db, table_name=table)}"
        result = self._execute_pandas(sql)
        if result.empty:
            return ""
        return str(result.iloc[0, 0])

    def _objects_with_ddl(
        self,
        object_type: str,
        names: List[str],
        db: str,
        tables_filter: Optional[List[str]],
    ) -> List[Dict[str, str]]:
        """Assemble DDL payloads matching the shape other adapters return."""
        result: List[Dict[str, str]] = []
        for name in names:
            full = self.full_name(database_name=db, table_name=name)
            if tables_filter and full not in tables_filter:
                continue
            try:
                ddl = self._show_create_definition(db, name)
            except Exception as e:
                logger.warning(f"Could not get DDL for {full}: {e}")
                ddl = f"-- DDL not available for {name}"
            result.append(
                {
                    "identifier": full,
                    "catalog_name": "",
                    "database_name": db,
                    "schema_name": db,
                    "table_name": name,
                    "table_type": object_type,
                    "definition": ddl,
                }
            )
        return result

    @override
    def get_tables_with_ddl(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        tables: Optional[List[str]] = None,
    ) -> List[Dict[str, str]]:
        """Get tables with DDL statements (via `SHOW CREATE TABLE`)."""
        db = database_name or self.database_name
        filter_tables = self._reset_filter_tables(tables, catalog_name, database_name, schema_name)
        names = self.get_tables(catalog_name=catalog_name, database_name=db, schema_name=schema_name)
        return self._objects_with_ddl("table", names, db, filter_tables)

    @override
    def get_views_with_ddl(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> List[Dict[str, str]]:
        """Get views with DDL statements (via `SHOW CREATE TABLE`; Spark treats views the same way)."""
        db = database_name or self.database_name
        names = self.get_views(catalog_name=catalog_name, database_name=db, schema_name=schema_name)
        return self._objects_with_ddl("view", names, db, None)

    @override
    def get_schema(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
    ) -> List[Dict[str, Any]]:
        """Get table schema information using DESCRIBE."""
        if not table_name:
            return []

        db = database_name or self.database_name
        full_name = self.full_name(database_name=db, table_name=table_name)

        query_result = self._execute_pandas(f"DESCRIBE {full_name}")

        result = []
        for i in range(len(query_result)):
            col_name = query_result.iloc[i, 0]
            # Skip partition/metadata separator lines
            if col_name is None or str(col_name).startswith("#") or str(col_name).strip() == "":
                continue
            result.append(
                {
                    "cid": len(result),
                    "name": col_name,
                    "type": str(query_result.iloc[i, 1]) if len(query_result.columns) > 1 else "",
                    "nullable": True,  # Spark doesn't expose nullable in DESCRIBE
                    "default_value": None,
                    "pk": False,
                    "comment": str(query_result.iloc[i, 2]) if len(query_result.columns) > 2 else None,
                }
            )
        return result

    # ==================== Database Management ====================

    @override
    def _sqlalchemy_schema(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> Optional[str]:
        """Get schema name for SQLAlchemy Inspector (database name in Spark)."""
        return database_name or self.database_name

    @override
    def do_switch_context(self, conn, catalog_name: str = "", database_name: str = "", schema_name: str = ""):
        """Apply database context to a connection using USE statement."""
        if database_name:
            from sqlalchemy import text

            conn.execute(text(f"USE {self.quote_identifier(database_name)}"))
            conn.commit()

    # ==================== Utility Methods ====================

    @override
    def quote_identifier(self, name: str) -> str:
        """Quote identifiers with backticks for Spark."""
        escaped = name.replace("`", "``")
        return f"`{escaped}`"

    @override
    def full_name(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
    ) -> str:
        """
        Build fully-qualified table name.

        Spark format: `database`.`table`
        """
        db = database_name or self.database_name
        if db:
            return f"{self.quote_identifier(db)}.{self.quote_identifier(table_name)}"
        return self.quote_identifier(table_name)

    def to_dict(self) -> Dict[str, Any]:
        """Convert connector to serializable dictionary."""
        return {
            "db_type": SPARK_DIALECT,
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "database": self.database_name,
        }

    def get_type(self) -> str:
        """Return the database type."""
        return SPARK_DIALECT

    @override
    def test_connection(self) -> bool:
        """Test the database connection."""
        try:
            return super().test_connection()
        finally:
            try:
                self.close()
            except Exception as e:
                logger.debug(f"Ignoring cleanup error during test: {e}")
