# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""DDL generators for Datus storage tables on PostgreSQL."""

from __future__ import annotations

from typing import Iterable, List, Tuple


def _column_sql(name: str, col_type: str, nullable: bool = True, default: str | None = None) -> str:
    sql = f"{name} {col_type}"
    if not nullable:
        sql += " NOT NULL"
    if default is not None:
        sql += f" DEFAULT {default}"
    return sql


def _create_table(name: str, columns: List[str], constraints: List[str]) -> str:
    cols = ",\n  ".join(columns + constraints)
    return f"CREATE TABLE IF NOT EXISTS {name} (\n  {cols}\n);"


def _fts_index_sql(table: str, columns: Iterable[str], index_name: str) -> str:
    parts = " || ' ' || ".join([f"COALESCE({col}, '')" for col in columns])
    return (
        f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} "
        f"USING GIN (to_tsvector('simple', {parts}));"
    )


def render_relational_ddl(schema: str = "public") -> str:
    prefix = f"{schema}."

    subject_nodes = _create_table(
        f"{prefix}subject_nodes",
        [
            _column_sql("namespace", "TEXT", nullable=False),
            _column_sql("node_id", "BIGSERIAL", nullable=False),
            _column_sql("parent_id", "BIGINT"),
            _column_sql("name", "TEXT", nullable=False),
            _column_sql("description", "TEXT", default="''"),
            _column_sql("created_at", "TEXT", nullable=False),
            _column_sql("updated_at", "TEXT", nullable=False),
        ],
        [
            "PRIMARY KEY (namespace, node_id)",
            "UNIQUE (namespace, parent_id, name)",
        ],
    )

    tasks = _create_table(
        f"{prefix}tasks",
        [
            _column_sql("namespace", "TEXT", nullable=False),
            _column_sql("task_id", "TEXT", nullable=False),
            _column_sql("task_query", "TEXT", nullable=False),
            _column_sql("sql_query", "TEXT"),
            _column_sql("sql_result", "TEXT"),
            _column_sql("status", "TEXT"),
            _column_sql("user_feedback", "TEXT"),
            _column_sql("created_at", "TEXT", nullable=False),
            _column_sql("updated_at", "TEXT", nullable=False),
        ],
        ["PRIMARY KEY (namespace, task_id)"],
    )

    feedback = _create_table(
        f"{prefix}feedback",
        [
            _column_sql("namespace", "TEXT", nullable=False),
            _column_sql("task_id", "TEXT", nullable=False),
            _column_sql("status", "TEXT", nullable=False),
            _column_sql("created_at", "TEXT", nullable=False),
        ],
        ["PRIMARY KEY (namespace, task_id)"],
    )

    indexes = [
        f"CREATE INDEX IF NOT EXISTS idx_subject_parent_id ON {prefix}subject_nodes(namespace, parent_id);",
        f"CREATE INDEX IF NOT EXISTS idx_subject_parent_name ON {prefix}subject_nodes(namespace, parent_id, name);",
    ]

    return "\n\n".join([subject_nodes, tasks, feedback] + indexes)


def render_vector_ddl(vector_dim: int, schema: str = "public") -> str:
    prefix = f"{schema}."
    vector_type = f"vector({vector_dim})"

    tables: List[Tuple[str, List[str], List[str], List[str]]] = [
        (
            "schema_metadata",
            [
                _column_sql("namespace", "TEXT", nullable=False),
                _column_sql("identifier", "TEXT"),
                _column_sql("catalog_name", "TEXT"),
                _column_sql("database_name", "TEXT"),
                _column_sql("schema_name", "TEXT"),
                _column_sql("table_name", "TEXT"),
                _column_sql("table_type", "TEXT"),
                _column_sql("definition", "TEXT"),
                _column_sql("vector", vector_type),
            ],
            ["UNIQUE (namespace, identifier)"],
            ["database_name", "schema_name", "table_name", "definition"],
        ),
        (
            "schema_value",
            [
                _column_sql("namespace", "TEXT", nullable=False),
                _column_sql("identifier", "TEXT"),
                _column_sql("catalog_name", "TEXT"),
                _column_sql("database_name", "TEXT"),
                _column_sql("schema_name", "TEXT"),
                _column_sql("table_name", "TEXT"),
                _column_sql("table_type", "TEXT"),
                _column_sql("sample_rows", "TEXT"),
                _column_sql("vector", vector_type),
            ],
            ["UNIQUE (namespace, identifier)"],
            ["database_name", "schema_name", "table_name", "sample_rows"],
        ),
        (
            "semantic_model",
            [
                _column_sql("namespace", "TEXT", nullable=False),
                _column_sql("id", "TEXT"),
                _column_sql("kind", "TEXT"),
                _column_sql("name", "TEXT"),
                _column_sql("fq_name", "TEXT"),
                _column_sql("semantic_model_name", "TEXT"),
                _column_sql("catalog_name", "TEXT"),
                _column_sql("database_name", "TEXT"),
                _column_sql("schema_name", "TEXT"),
                _column_sql("table_name", "TEXT"),
                _column_sql("description", "TEXT"),
                _column_sql("vector", vector_type),
                _column_sql("is_dimension", "BOOLEAN"),
                _column_sql("is_measure", "BOOLEAN"),
                _column_sql("is_entity_key", "BOOLEAN"),
                _column_sql("is_deprecated", "BOOLEAN"),
                _column_sql("expr", "TEXT"),
                _column_sql("column_type", "TEXT"),
                _column_sql("agg", "TEXT"),
                _column_sql("create_metric", "BOOLEAN"),
                _column_sql("agg_time_dimension", "TEXT"),
                _column_sql("is_partition", "BOOLEAN"),
                _column_sql("time_granularity", "TEXT"),
                _column_sql("entity", "TEXT"),
                _column_sql("yaml_path", "TEXT"),
                _column_sql("updated_at", "TIMESTAMP"),
            ],
            ["UNIQUE (namespace, id)"],
            ["description", "name", "fq_name"],
        ),
        (
            "metrics",
            [
                _column_sql("namespace", "TEXT", nullable=False),
                _column_sql("name", "TEXT"),
                _column_sql("subject_node_id", "BIGINT"),
                _column_sql("created_at", "TEXT"),
                _column_sql("id", "TEXT"),
                _column_sql("semantic_model_name", "TEXT"),
                _column_sql("description", "TEXT"),
                _column_sql("vector", vector_type),
                _column_sql("metric_type", "TEXT"),
                _column_sql("measure_expr", "TEXT"),
                _column_sql("base_measures", "TEXT[]"),
                _column_sql("dimensions", "TEXT[]"),
                _column_sql("entities", "TEXT[]"),
                _column_sql("catalog_name", "TEXT"),
                _column_sql("database_name", "TEXT"),
                _column_sql("schema_name", "TEXT"),
                _column_sql("sql", "TEXT"),
                _column_sql("yaml_path", "TEXT"),
                _column_sql("updated_at", "TIMESTAMP"),
            ],
            ["UNIQUE (namespace, id)"],
            ["description", "name"],
        ),
        (
            "reference_sql",
            [
                _column_sql("namespace", "TEXT", nullable=False),
                _column_sql("name", "TEXT"),
                _column_sql("subject_node_id", "BIGINT"),
                _column_sql("created_at", "TEXT"),
                _column_sql("id", "TEXT"),
                _column_sql("sql", "TEXT"),
                _column_sql("comment", "TEXT"),
                _column_sql("summary", "TEXT"),
                _column_sql("search_text", "TEXT"),
                _column_sql("filepath", "TEXT"),
                _column_sql("tags", "TEXT"),
                _column_sql("vector", vector_type),
            ],
            ["UNIQUE (namespace, id)"],
            ["sql", "name", "summary", "tags", "search_text"],
        ),
        (
            "ext_knowledge",
            [
                _column_sql("namespace", "TEXT", nullable=False),
                _column_sql("name", "TEXT"),
                _column_sql("subject_node_id", "BIGINT"),
                _column_sql("created_at", "TEXT"),
                _column_sql("id", "TEXT"),
                _column_sql("search_text", "TEXT"),
                _column_sql("explanation", "TEXT"),
                _column_sql("vector", vector_type),
            ],
            ["UNIQUE (namespace, id)", "UNIQUE (namespace, subject_node_id, name)"],
            ["search_text", "explanation"],
        ),
        (
            "document",
            [
                _column_sql("namespace", "TEXT", nullable=False),
                _column_sql("title", "TEXT"),
                _column_sql("hierarchy", "TEXT"),
                _column_sql("keywords", "TEXT[]"),
                _column_sql("language", "TEXT"),
                _column_sql("chunk_text", "TEXT"),
                _column_sql("vector", vector_type),
            ],
            [],
            ["chunk_text"],
        ),
    ]

    ddl_statements: List[str] = []
    for table_name, columns, constraints, fts_cols in tables:
        ddl_statements.append(_create_table(f"{prefix}{table_name}", columns, constraints))
        if fts_cols:
            ddl_statements.append(_fts_index_sql(f"{prefix}{table_name}", fts_cols, f"idx_{table_name}_fts"))

    return "\n\n".join(ddl_statements)


def render_all_ddl(vector_dim: int, schema: str = "public") -> str:
    return "\n\n".join([render_relational_ddl(schema), render_vector_ddl(vector_dim, schema)])
