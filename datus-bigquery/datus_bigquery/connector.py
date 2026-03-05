# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from typing import Any, Dict, List, Optional, Set, Union, override

from datus.schemas.base import TABLE_TYPE
from datus.utils.exceptions import DatusException, ErrorCode
from datus.utils.loggings import get_logger
from datus_sqlalchemy import SQLAlchemyConnector
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from .config import BigQueryConfig

logger = get_logger(__name__)


class BigQueryConnector(SQLAlchemyConnector):
    """BigQuery database connector using sqlalchemy-bigquery."""

    def __init__(self, config: Union[BigQueryConfig, dict]):
        if isinstance(config, dict):
            config = BigQueryConfig(**config)
        elif not isinstance(config, BigQueryConfig):
            raise TypeError(f"config must be BigQueryConfig or dict, got {type(config)}")

        self.config = config
        self.project = config.project
        self.credentials_path = config.credentials_path
        self.location = config.location

        dataset = config.dataset or ""

        # Build connection string: bigquery://project/dataset
        connection_string = f"bigquery://{self.project}"
        if dataset:
            connection_string += f"/{dataset}"

        # Append credentials_path as query param if provided
        query_parts = []
        if self.credentials_path:
            query_parts.append(f"credentials_path={self.credentials_path}")
        if self.location:
            query_parts.append(f"location={self.location}")
        if query_parts:
            connection_string += "?" + "&".join(query_parts)

        super().__init__(
            connection_string,
            dialect="bigquery",
            timeout_seconds=config.timeout_seconds,
        )
        self.catalog_name = self.project
        self.database_name = dataset

    # ==================== Connection ====================

    @override
    def connect(self):
        """Create engine with NullPool (BigQuery uses stateless HTTP API)."""
        if self.engine:
            return
        create_disposition = "CREATE_IF_NEEDED"
        self.engine = create_engine(
            self.connection_string,
            poolclass=NullPool,
            create_disposition=create_disposition,
        )
        self._owns_engine = True

    # ==================== System Resources ====================

    @override
    def _sys_databases(self) -> Set[str]:
        return {"INFORMATION_SCHEMA", "information_schema"}

    @override
    def _sys_schemas(self) -> Set[str]:
        return self._sys_databases()

    # ==================== Utility Methods ====================

    @staticmethod
    def _quote_identifier(identifier: str) -> str:
        """Wrap identifiers with backticks for BigQuery."""
        escaped = identifier.replace("`", "\\`")
        return f"`{escaped}`"

    # ==================== Metadata Retrieval ====================

    @override
    def get_tables(self, catalog_name: str = "", database_name: str = "", schema_name: str = "") -> List[str]:
        self.connect()
        dataset = database_name or self.database_name
        if not dataset:
            raise DatusException(
                ErrorCode.COMMON_FIELD_INVALID,
                "BigQuery requires a dataset name to list tables. Set dataset in config or pass database_name.",
            )

        project = catalog_name or self.catalog_name
        sql = (
            f"SELECT table_name FROM `{project}`.`{dataset}`.INFORMATION_SCHEMA.TABLES "
            f"WHERE table_type IN ('BASE TABLE', 'VIEW')"
        )
        result = self._execute_pandas(sql)
        if result.empty:
            return []
        return result["table_name"].tolist()

    @override
    def get_tables_with_ddl(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        tables: Optional[List[str]] = None,
    ) -> List[Dict[str, str]]:
        self.connect()
        dataset = database_name or self.database_name
        project = catalog_name or self.catalog_name

        if not dataset:
            raise DatusException(
                ErrorCode.COMMON_FIELD_INVALID,
                "BigQuery requires a dataset name to list tables.",
            )

        sql = (
            f"SELECT table_name, table_type, ddl "
            f"FROM `{project}`.`{dataset}`.INFORMATION_SCHEMA.TABLES "
            f"WHERE table_type IN ('BASE TABLE', 'VIEW')"
        )
        df = self._execute_pandas(sql)
        if df.empty:
            return []

        filter_tables = self._reset_filter_tables(tables, project, dataset)
        result = []
        for _, row in df.iterrows():
            table_name = row["table_name"]
            full = self.full_name(catalog_name=project, database_name=dataset, table_name=table_name)
            if filter_tables and full not in filter_tables:
                continue
            table_type = "view" if row["table_type"] == "VIEW" else "table"
            result.append(
                {
                    "identifier": self.identifier(catalog_name=project, database_name=dataset, table_name=table_name),
                    "catalog_name": project,
                    "database_name": dataset,
                    "schema_name": "",
                    "table_name": table_name,
                    "table_type": table_type,
                    "definition": row.get("ddl", f"-- DDL not available for {table_name}"),
                }
            )
        return result

    @override
    def get_views_with_ddl(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> List[Dict[str, str]]:
        self.connect()
        dataset = database_name or self.database_name
        project = catalog_name or self.catalog_name

        if not dataset:
            return []

        sql = (
            f"SELECT table_name, ddl "
            f"FROM `{project}`.`{dataset}`.INFORMATION_SCHEMA.TABLES "
            f"WHERE table_type = 'VIEW'"
        )
        df = self._execute_pandas(sql)
        if df.empty:
            return []

        result = []
        for _, row in df.iterrows():
            table_name = row["table_name"]
            result.append(
                {
                    "identifier": self.identifier(catalog_name=project, database_name=dataset, table_name=table_name),
                    "catalog_name": project,
                    "database_name": dataset,
                    "schema_name": "",
                    "table_name": table_name,
                    "table_type": "view",
                    "definition": row.get("ddl", f"-- DDL not available for {table_name}"),
                }
            )
        return result

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

        self.connect()
        dataset = database_name or self.database_name
        project = catalog_name or self.catalog_name

        sql = (
            f"SELECT column_name, data_type, is_nullable, column_default, ordinal_position "
            f"FROM `{project}`.`{dataset}`.INFORMATION_SCHEMA.COLUMNS "
            f"WHERE table_name = '{table_name}' "
            f"ORDER BY ordinal_position"
        )
        df = self._execute_pandas(sql)
        if df.empty:
            return []

        result = []
        for _, row in df.iterrows():
            result.append(
                {
                    "cid": row["ordinal_position"],
                    "name": row["column_name"],
                    "type": row["data_type"],
                    "nullable": row["is_nullable"] == "YES",
                    "default_value": row.get("column_default", ""),
                    "pk": False,
                }
            )
        return result

    # ==================== Database/Schema Management ====================

    @override
    def get_databases(self, catalog_name: str = "", include_sys: bool = False) -> List[str]:
        """List datasets in the project."""
        self.connect()
        project = catalog_name or self.catalog_name
        sql = f"SELECT schema_name FROM `{project}`.INFORMATION_SCHEMA.SCHEMATA"
        df = self._execute_pandas(sql)
        if df.empty:
            return []
        datasets = df["schema_name"].tolist()
        if not include_sys:
            sys_dbs = self._sys_databases()
            datasets = [d for d in datasets if d not in sys_dbs]
        return datasets

    @override
    def get_schemas(self, catalog_name: str = "", database_name: str = "", include_sys: bool = False) -> List[str]:
        """BigQuery has no schema layer below dataset. Returns empty list."""
        return []

    @override
    def _sqlalchemy_schema(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> Optional[str]:
        return database_name or self.database_name

    @override
    def do_switch_context(self, catalog_name: str = "", database_name: str = "", schema_name: str = ""):
        """Switch context by updating project/dataset state. No SQL execution needed."""
        if catalog_name:
            self.catalog_name = catalog_name
        if database_name:
            self.database_name = database_name

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
    ) -> List[Dict[str, str]]:
        self.connect()
        dataset = database_name or self.database_name
        project = catalog_name or self.catalog_name
        result = []

        if tables:
            target_tables = tables
        else:
            target_tables = self.get_tables(catalog_name=project, database_name=dataset)

        for table_name in target_tables:
            full = self.full_name(catalog_name=project, database_name=dataset, table_name=table_name)
            sql = f"SELECT * FROM {full} LIMIT {top_n}"
            try:
                df = self._execute_pandas(sql)
                if not df.empty:
                    result.append(
                        {
                            "identifier": self.identifier(
                                catalog_name=project, database_name=dataset, table_name=table_name
                            ),
                            "catalog_name": project,
                            "database_name": dataset,
                            "schema_name": "",
                            "table_name": table_name,
                            "sample_rows": df.to_csv(index=False),
                        }
                    )
            except Exception as e:
                logger.warning(f"Could not get sample rows for {full}: {e}")

        return result

    # ==================== Name Formatting ====================

    @override
    def full_name(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
    ) -> str:
        """Build fully-qualified name: `project`.`dataset`.`table`."""
        project = catalog_name or self.catalog_name
        dataset = database_name or self.database_name
        if project and dataset:
            return (
                f"{self._quote_identifier(project)}"
                f".{self._quote_identifier(dataset)}"
                f".{self._quote_identifier(table_name)}"
            )
        if dataset:
            return f"{self._quote_identifier(dataset)}.{self._quote_identifier(table_name)}"
        return self._quote_identifier(table_name)

    @override
    def _reset_filter_tables(
        self,
        tables: Optional[List[str]] = None,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> List[str]:
        catalog_name = catalog_name or self.catalog_name
        database_name = database_name or self.database_name
        return super()._reset_filter_tables(tables, catalog_name, database_name, "")

    @override
    def identifier(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
    ) -> str:
        """Build identifier: project.dataset.table."""
        project = catalog_name or self.catalog_name
        dataset = database_name or self.database_name
        if project and dataset:
            return f"{project}.{dataset}.{table_name}"
        if dataset:
            return f"{dataset}.{table_name}"
        return table_name

    @override
    def get_type(self) -> str:
        return "bigquery"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "db_type": "bigquery",
            "project": self.catalog_name,
            "dataset": self.database_name,
        }
