# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""pgvector backend implementation for Datus vector storage."""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional, Sequence

import pyarrow as pa
from sqlalchemy import MetaData, Table, and_ as sa_and, bindparam, cast, func, inspect, or_ as sa_or, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql.elements import ClauseElement

from datus.storage.backends.vector.interfaces import (
    And,
    BackendCapabilities,
    Condition,
    FilterExpr,
    Not,
    Op,
    Or,
    VectorBackend,
    VectorTable,
)
from datus.utils.exceptions import DatusException, ErrorCode
from datus.utils.loggings import get_logger

logger = get_logger(__name__)


try:
    from pgvector.psycopg2 import register_vector
    from pgvector.sqlalchemy import Vector as PgVectorType

    _PGVECTOR_AVAILABLE = True
except Exception:
    PgVectorType = None  # type: ignore[assignment]
    register_vector = None  # type: ignore[assignment]
    _PGVECTOR_AVAILABLE = False


def _vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(f"{v:.8f}" for v in vector) + "]"


class PgVectorTable(VectorTable):
    def __init__(
        self,
        engine: Engine,
        table: Table,
        spec,
        namespace: str,
        distance_op: str,
        fts_enabled: bool,
    ):
        self._engine = engine
        self._table = table
        self._spec = spec
        self._namespace = namespace
        self._distance_op = distance_op
        self._fts_enabled = fts_enabled
        self.name = table.name

    def add(self, rows: Sequence[Mapping[str, Any]]) -> None:
        if not rows:
            return
        rows = [self._inject_namespace(row) for row in rows]
        stmt = self._table.insert()
        self._execute(stmt, rows)

    def upsert(self, rows: Sequence[Mapping[str, Any]], on: str) -> None:
        if not rows:
            return
        rows = [self._inject_namespace(row) for row in rows]
        conflict_cols = [on]
        if "namespace" in self._table.c and "namespace" not in conflict_cols:
            conflict_cols.insert(0, "namespace")

        stmt = pg_insert(self._table).values(rows)
        update_cols = {c.name: stmt.excluded[c.name] for c in self._table.columns if c.name not in conflict_cols}
        stmt = stmt.on_conflict_do_update(index_elements=conflict_cols, set_=update_cols)
        self._execute(stmt)

    def update(self, where: FilterExpr, values: Mapping[str, Any]) -> None:
        where_clause = self._compile_where(where)
        if where_clause is None:
            return
        stmt = self._table.update().where(where_clause).values(**values)
        self._execute(stmt)

    def delete(self, where: FilterExpr) -> None:
        where_clause = self._compile_where(where)
        if where_clause is None:
            return
        stmt = self._table.delete().where(where_clause)
        self._execute(stmt)

    def count(self, where: Optional[FilterExpr] = None) -> int:
        stmt = select(func.count()).select_from(self._table)
        where_clause = self._compile_where(where)
        if where_clause is not None:
            stmt = stmt.where(where_clause)
        return self._execute_scalar(stmt)

    def search_all(
        self,
        where: Optional[FilterExpr] = None,
        select: Optional[Sequence[str]] = None,
        limit: Optional[int] = None,
    ) -> pa.Table:
        stmt = self._build_select(select, where, limit=limit)
        rows = self._execute_select(stmt)
        return pa.Table.from_pylist(rows)

    def search_vector(
        self,
        vector: Sequence[float],
        top_n: int,
        where: Optional[FilterExpr] = None,
        select: Optional[Sequence[str]] = None,
    ) -> pa.Table:
        stmt = self._build_select(select, where)
        vector_param = _vector_literal(vector)
        if PgVectorType is not None:
            query_vector = cast(bindparam("query_vector"), PgVectorType)
        else:
            query_vector = text("CAST(:query_vector AS vector)")
        distance_expr = self._table.c[self._spec.vector_column].op(self._distance_op)(query_vector)
        if not select or "_distance" not in select:
            stmt = stmt.add_columns(distance_expr.label("_distance"))
        stmt = stmt.order_by(distance_expr).limit(top_n)
        rows = self._execute_select(stmt, {"query_vector": vector_param})
        return pa.Table.from_pylist(rows)

    def search_text(
        self,
        text_query: str,
        top_n: int,
        where: Optional[FilterExpr] = None,
        select: Optional[Sequence[str]] = None,
    ) -> pa.Table:
        raise DatusException(
            ErrorCode.STORAGE_SEARCH_FAILED,
            message_args={"error_message": "pgvector backend does not support native embedding"},
        )

    def search_hybrid(
        self,
        text: str,
        top_n: int,
        where: Optional[FilterExpr] = None,
        select: Optional[Sequence[str]] = None,
        reranker: Optional[Any] = None,
        vector: Optional[Sequence[float]] = None,
    ) -> pa.Table:
        if not self._fts_enabled or self._spec.text_source is None or vector is None:
            return self.search_vector(vector or [], top_n=top_n, where=where, select=select)

        text_col = self._spec.text_source
        if text_col not in self._table.c:
            return self.search_vector(vector, top_n=top_n, where=where, select=select)

        vector_param = _vector_literal(vector)
        where_clause, where_params = self._compile_where_sql(where)
        namespace_clause = "namespace = :ns" if "namespace" in self._table.c else None
        base_clauses = [c for c in [namespace_clause, where_clause] if c]
        base_where_sql = f"WHERE {' AND '.join(base_clauses)}" if base_clauses else ""
        fts_clauses = base_clauses + [
            f"to_tsvector('simple', {text_col}) @@ plainto_tsquery('simple', :fts_query)"
        ]
        fts_where_sql = f"WHERE {' AND '.join(fts_clauses)}"

        select_cols = list(select) if select else [col.name for col in self._table.c]
        select_sql = ", ".join(select_cols)
        table_name = self._qualified_table_name()
        vector_cast = "CAST(:query_vector AS vector)"
        vec_sim = f"1 - ({self._spec.vector_column} {self._distance_op} {vector_cast}) / 2"
        bm25 = f"ts_rank_cd(to_tsvector('simple', {text_col}), plainto_tsquery('simple', :fts_query))"
        score = f"(0.6 * {vec_sim}) + (0.4 * 8.0 * {bm25})"
        distance_expr = f"{self._spec.vector_column} {self._distance_op} {vector_cast}"
        distance_sql = f", {distance_expr} AS _distance" if "_distance" not in select_cols else ""

        search_sql = f"""
        WITH vector_candidates AS (
            SELECT ctid
            FROM {table_name}
            {base_where_sql}
            ORDER BY {self._spec.vector_column} {self._distance_op} {vector_cast}
            LIMIT :candidate_k
        ),
        fts_candidates AS (
            SELECT ctid
            FROM {table_name}
            {fts_where_sql}
            ORDER BY ts_rank_cd(to_tsvector('simple', {text_col}), plainto_tsquery('simple', :fts_query)) DESC
            LIMIT :candidate_k
        ),
        candidate_ids AS (
            SELECT ctid FROM vector_candidates
            UNION
            SELECT ctid FROM fts_candidates
        )
        SELECT {select_sql}
               {distance_sql},
               {vec_sim} AS vec_sim,
               {bm25} AS bm25,
               {score} AS score
        FROM {table_name}
        WHERE ctid IN (SELECT ctid FROM candidate_ids)
        ORDER BY score DESC
        LIMIT :top_n
        """
        params = {
            "query_vector": vector_param,
            "fts_query": text,
            "top_n": top_n,
            "candidate_k": max(top_n * 5, 50),
        }
        if "namespace" in self._table.c:
            params["ns"] = self._namespace
        params.update(where_params)
        rows = self._execute_text(search_sql, params)
        return pa.Table.from_pylist(rows)

    def create_vector_index(self, **opts: Any) -> None:
        logger.info("pgvector backend: vector index creation requires manual DDL")

    def create_fts_index(self, fields: Sequence[str], **opts: Any) -> None:
        logger.info("pgvector backend: FTS index creation requires manual DDL")

    def create_scalar_index(self, fields: Sequence[str], **opts: Any) -> None:
        logger.info("pgvector backend: scalar index creation requires manual DDL")

    def _inject_namespace(self, row: Mapping[str, Any]) -> Mapping[str, Any]:
        if "namespace" not in self._table.c:
            return row
        if "namespace" in row and row["namespace"] != self._namespace:
            raise DatusException(
                ErrorCode.COMMON_FIELD_INVALID,
                message=f"namespace mismatch for table {self.name}: {row['namespace']} != {self._namespace}",
            )
        return {**row, "namespace": self._namespace}

    def _compile_where(self, expr: Optional[FilterExpr]) -> Optional[ClauseElement]:
        if expr is None:
            return self._namespace_filter()
        if isinstance(expr, ClauseElement):
            return sa_and(self._namespace_filter(), expr) if self._namespace_filter() is not None else expr
        if isinstance(expr, str):
            return text(expr)
        clause = self._compile_node(expr)
        if self._namespace_filter() is not None:
            clause = sa_and(self._namespace_filter(), clause)
        return clause

    def _compile_where_sql(self, expr: Optional[FilterExpr]) -> tuple[Optional[str], dict]:
        if expr is None:
            return None, {}
        if isinstance(expr, str):
            return expr, {}
        return self._compile_node_sql(expr, "where")

    def _namespace_filter(self) -> Optional[ClauseElement]:
        if "namespace" in self._table.c:
            return self._table.c.namespace == self._namespace
        return None

    def _compile_node(self, node: Any) -> ClauseElement:
        if isinstance(node, Condition) or hasattr(node, "field"):
            return self._compile_condition(node)
        if isinstance(node, And) or getattr(node, "__class__", None).__name__ == "And":
            parts = [self._compile_node(n) for n in node.nodes if n is not None]
            return sa_and(*parts) if parts else text("1=1")
        if isinstance(node, Or) or getattr(node, "__class__", None).__name__ == "Or":
            parts = [self._compile_node(n) for n in node.nodes if n is not None]
            return sa_or(*parts) if parts else text("1=0")
        if isinstance(node, Not) or getattr(node, "__class__", None).__name__ == "Not":
            return ~self._compile_node(node.node)
        raise TypeError(f"Unsupported filter node: {type(node)}")

    def _compile_condition(self, cond: Any) -> ClauseElement:
        field = cond.field
        if field not in self._table.c:
            raise ValueError(f"Unknown column '{field}' in filter")
        column = self._table.c[field]
        op = cond.op
        op_value = op.value if hasattr(op, "value") else str(op)
        value = cond.value

        if value is None:
            if op_value == "=" or op == Op.EQ:
                return column.is_(None)
            if op_value == "!=" or op == Op.NE:
                return column.is_not(None)
            raise ValueError(f"Operator {op} is invalid with NULL (field: {field})")

        if op_value == "IN" or op == Op.IN:
            if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
                raise TypeError("IN expects a non-string iterable value")
            values = list(value)
            if not values:
                return text("1=0")
            return column.in_(values)

        if op_value == "LIKE" or op == Op.LIKE:
            return column.like(value)

        if op_value == "=":
            return column == value
        if op_value == "!=":
            return column != value
        if op_value == ">":
            return column > value
        if op_value == ">=":
            return column >= value
        if op_value == "<":
            return column < value
        if op_value == "<=":
            return column <= value

        raise ValueError(f"Unsupported operator: {op}")

    def _compile_node_sql(self, node: Any, param_prefix: str) -> tuple[str, dict[str, Any]]:
        if isinstance(node, Condition) or hasattr(node, "field"):
            return self._compile_condition_sql(node, param_prefix)

        if isinstance(node, And) or getattr(node, "__class__", None).__name__ == "And":
            parts: list[str] = []
            params: dict[str, Any] = {}
            for index, sub_node in enumerate(node.nodes):
                if sub_node is None:
                    continue
                clause, clause_params = self._compile_node_sql(sub_node, f"{param_prefix}_and_{index}")
                parts.append(f"({clause})")
                params.update(clause_params)
            return (" AND ".join(parts) if parts else "1=1"), params

        if isinstance(node, Or) or getattr(node, "__class__", None).__name__ == "Or":
            parts = []
            params: dict[str, Any] = {}
            for index, sub_node in enumerate(node.nodes):
                if sub_node is None:
                    continue
                clause, clause_params = self._compile_node_sql(sub_node, f"{param_prefix}_or_{index}")
                parts.append(f"({clause})")
                params.update(clause_params)
            return (" OR ".join(parts) if parts else "1=0"), params

        if isinstance(node, Not) or getattr(node, "__class__", None).__name__ == "Not":
            clause, params = self._compile_node_sql(node.node, f"{param_prefix}_not")
            return f"NOT ({clause})", params

        raise TypeError(f"Unsupported filter node: {type(node)}")

    def _compile_condition_sql(self, cond: Any, param_prefix: str) -> tuple[str, dict[str, Any]]:
        field = cond.field
        if field not in self._table.c:
            raise ValueError(f"Unknown column '{field}' in filter")

        op = cond.op
        raw_op = op.value if hasattr(op, "value") else str(op)
        op_value = raw_op.upper() if isinstance(raw_op, str) and raw_op.isalpha() else raw_op
        value = cond.value
        column_sql = self._quote_identifier(field)

        if value is None:
            if op_value in ("=", "==") or op == Op.EQ:
                return f"{column_sql} IS NULL", {}
            if op_value in ("!=", "<>") or op == Op.NE:
                return f"{column_sql} IS NOT NULL", {}
            raise ValueError(f"Operator {op} is invalid with NULL (field: {field})")

        if op_value == "IN" or op == Op.IN:
            if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
                raise TypeError("IN expects a non-string iterable value")
            values = list(value)
            if not values:
                return "1=0", {}
            placeholders: list[str] = []
            params: dict[str, Any] = {}
            for index, item in enumerate(values):
                name = f"{param_prefix}_{index}"
                placeholders.append(f":{name}")
                params[name] = item
            return f"{column_sql} IN ({', '.join(placeholders)})", params

        if op_value == "LIKE" or op == Op.LIKE:
            name = f"{param_prefix}_value"
            return f"{column_sql} LIKE :{name}", {name: value}

        op_map = {
            "=": "=",
            "==": "=",
            "!=": "!=",
            "<>": "!=",
            ">": ">",
            ">=": ">=",
            "<": "<",
            "<=": "<=",
        }
        if op_value not in op_map:
            raise ValueError(f"Unsupported operator: {op}")

        name = f"{param_prefix}_value"
        return f"{column_sql} {op_map[op_value]} :{name}", {name: value}

    @staticmethod
    def _quote_identifier(name: str) -> str:
        escaped = name.replace('"', '""')
        return f'"{escaped}"'

    def _build_select(
        self,
        columns: Optional[Sequence[str]],
        where: Optional[FilterExpr],
        limit: Optional[int] = None,
    ):
        if columns:
            cols = [self._table.c[col] for col in columns]
            stmt = select(*cols)
        else:
            stmt = select(self._table)
        where_clause = self._compile_where(where)
        if where_clause is not None:
            stmt = stmt.where(where_clause)
        if limit is not None:
            stmt = stmt.limit(limit)
        return stmt

    def _execute(self, stmt: ClauseElement, params: Optional[Any] = None) -> None:
        try:
            with self._engine.begin() as conn:
                if params is None:
                    conn.execute(stmt)
                else:
                    conn.execute(stmt, params)
        except Exception as exc:
            raise DatusException(ErrorCode.STORAGE_SAVE_FAILED, message_args={"error_message": str(exc)}) from exc

    def _execute_select(self, stmt: ClauseElement, params: Optional[dict] = None) -> list[dict]:
        try:
            with self._engine.begin() as conn:
                result = conn.execute(stmt, params or {})
                return [dict(row) for row in result.mappings().all()]
        except Exception as exc:
            raise DatusException(ErrorCode.DB_EXECUTION_ERROR, message_args={"error_message": str(exc)}) from exc

    def _execute_text(self, sql: str, params: dict) -> list[dict]:
        try:
            with self._engine.begin() as conn:
                result = conn.execute(text(sql), params)
                return [dict(row) for row in result.mappings().all()]
        except Exception as exc:
            raise DatusException(ErrorCode.DB_EXECUTION_ERROR, message_args={"error_message": str(exc)}) from exc

    def _execute_scalar(self, stmt: ClauseElement) -> int:
        try:
            with self._engine.begin() as conn:
                result = conn.execute(stmt).scalar()
                return int(result or 0)
        except Exception as exc:
            raise DatusException(ErrorCode.DB_EXECUTION_ERROR, message_args={"error_message": str(exc)}) from exc

    def _qualified_table_name(self) -> str:
        if self._table.schema:
            return f"{self._table.schema}.{self._table.name}"
        return self._table.name


class PgVectorBackend(VectorBackend):
    name = "pgvector"

    def __init__(
        self,
        db_path: str,
        connection_string: str,
        namespace: str,
        schema: str = "public",
        distance: str = "cosine",
        fts_enabled: bool = True,
        ensure_vector_extension: bool = True,
    ):
        self._connection_string = connection_string
        self._schema = schema
        self._namespace = namespace
        self._metadata = MetaData()
        self._engine = self._create_engine(connection_string)
        self._distance_op = "<=>" if distance == "cosine" else "<->"
        self.caps = BackendCapabilities(
            vector_search=True,
            hybrid_search=fts_enabled,
            fts=fts_enabled,
            scalar_index=False,
            native_embedding=False,
        )

        if register_vector is not None:
            try:
                with self._engine.connect() as conn:
                    raw_conn = getattr(conn, "connection", None)
                    dbapi_conn = getattr(raw_conn, "connection", raw_conn)
                    register_vector(dbapi_conn)
            except Exception as exc:
                logger.warning(f"pgvector register_vector failed: {exc}")

        if ensure_vector_extension:
            self._ensure_vector_extension()

    def ensure_table(self, spec) -> PgVectorTable:
        if not self.table_exists(spec.name):
            raise DatusException(ErrorCode.DB_TABLE_NOT_EXISTS, message_args={"table_name": spec.name})
        table = Table(spec.name, self._metadata, schema=self._schema, autoload_with=self._engine)
        self._validate_columns(table, spec)
        if "namespace" not in table.c:
            raise DatusException(
                ErrorCode.DB_EXECUTION_ERROR,
                message_args={"error_message": f"Table {spec.name} missing required column: namespace"},
            )
        return PgVectorTable(
            engine=self._engine,
            table=table,
            spec=spec,
            namespace=self._namespace,
            distance_op=self._distance_op,
            fts_enabled=self.caps.fts,
        )

    def drop_table(self, name: str) -> None:
        if not self.table_exists(name):
            return
        try:
            table = Table(name, self._metadata, schema=self._schema, autoload_with=self._engine)
            if "namespace" in table.c:
                stmt = table.delete().where(table.c.namespace == self._namespace)
            else:
                stmt = table.delete()
            with self._engine.begin() as conn:
                conn.execute(stmt)
            logger.info(f"pgvector backend: cleared rows for table {name}")
        except Exception as exc:
            logger.warning(f"pgvector backend: failed to clear table {name}: {exc}")

    def table_exists(self, name: str) -> bool:
        inspector = inspect(self._engine)
        try:
            return inspector.has_table(name, schema=self._schema)
        except SQLAlchemyError:
            return False

    def _create_engine(self, connection_string: str) -> Engine:
        from sqlalchemy import create_engine

        return create_engine(connection_string, pool_pre_ping=True, future=True)

    def _ensure_vector_extension(self) -> None:
        """Fail fast with actionable guidance when pgvector extension is missing."""
        try:
            with self._engine.connect() as conn:
                stmt = text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
                extension_enabled = bool(conn.execute(stmt).scalar())
        except Exception as exc:
            raise DatusException(
                ErrorCode.DB_EXECUTION_ERROR,
                message_args={"error_message": f"Failed to verify pgvector extension availability: {exc}"},
            ) from exc

        if not extension_enabled:
            raise DatusException(
                ErrorCode.DB_EXECUTION_ERROR,
                message_args={
                    "error_message": (
                        "PostgreSQL extension 'vector' is not enabled for the target database. "
                        "Run: CREATE EXTENSION IF NOT EXISTS vector;"
                    )
                },
            )

    def _validate_columns(self, table: Table, spec) -> None:
        if spec.schema is None:
            return
        required = []
        if hasattr(spec.schema, "names"):
            required = list(spec.schema.names)
        elif hasattr(spec.schema, "schema"):
            required = list(spec.schema.schema().names)  # type: ignore[attr-defined]
        missing = [name for name in required if name not in table.c]
        if missing:
            raise DatusException(
                ErrorCode.DB_EXECUTION_ERROR,
                message_args={"error_message": f"Table {spec.name} missing columns: {', '.join(missing)}"},
            )
