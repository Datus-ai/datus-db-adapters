# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import re
from typing import Any, Dict, Optional

import sqlglot
from sqlglot import expressions
from sqlglot.expressions import Table

from datus_db_core.constants import SQLType
from datus_db_core.logging import get_logger

logger = get_logger(__name__)

# Built-in dialect constants (avoid importing DBType from agent)
_SQLITE = "sqlite"
_DUCKDB = "duckdb"


def _registered_parser_dialect(dialect: str) -> Optional[str]:
    # Imported lazily to avoid a registry -> connector -> sql_utils cycle.
    from datus_db_core.registry import connector_registry

    return connector_registry.get_parser_dialect(dialect)


def parse_read_dialect(dialect: str = "snowflake") -> str:
    db = (dialect or "").strip().lower()
    registered = _registered_parser_dialect(db)
    if registered:
        return registered
    if db in ("postgres", "postgresql", "redshift", "greenplum"):
        return "postgres"
    if db in ("spark", "databricks", "hive", "starrocks"):
        return "hive"
    if db in ("mssql", "sqlserver"):
        return "tsql"
    return db


def parse_dialect(dialect: str = "snowflake") -> str:
    db = (dialect or "").strip().lower()
    registered = _registered_parser_dialect(db)
    if registered:
        return registered
    if db in ("postgres", "postgresql"):
        return "postgres"
    if db in ("mssql", "sqlserver"):
        return "tsql"
    return db


def metadata_identifier(
    catalog_name: str = "",
    database_name: str = "",
    schema_name: str = "",
    table_name: str = "",
    dialect: str = "snowflake",
) -> str:
    from datus_db_core.registry import connector_registry

    if dialect == _SQLITE:
        return f"{database_name}.{table_name}" if database_name else table_name
    if dialect == _DUCKDB:
        return ".".join(part for part in (database_name, schema_name, table_name) if part)

    parts = []
    caps_known = connector_registry.has_capabilities(dialect)
    if caps_known:
        if connector_registry.support_catalog(dialect) and catalog_name:
            parts.append(catalog_name)
        if connector_registry.support_database(dialect) and database_name:
            parts.append(database_name)
        if connector_registry.support_schema(dialect) and schema_name:
            parts.append(schema_name)
    else:
        # Fallback: include all non-empty parts when dialect capabilities unknown
        if catalog_name:
            parts.append(catalog_name)
        if database_name:
            parts.append(database_name)
        if schema_name:
            parts.append(schema_name)
    if table_name:
        parts.append(table_name)
    return ".".join(parts)


def strip_sql_comments(sql: str) -> str:
    result = []
    i = 0
    length = len(sql)
    quote_char: Optional[str] = None

    while i < length:
        ch = sql[i]

        if quote_char is not None:
            result.append(ch)
            if ch == quote_char and not _is_escaped(sql, i):
                if i + 1 < length and sql[i + 1] == quote_char:
                    result.append(sql[i + 1])
                    i += 2
                    continue
                quote_char = None
            i += 1
            continue

        if ch in ("'", '"', "`"):
            quote_char = ch
            result.append(ch)
            i += 1
            continue

        if ch == "-" and i + 1 < length and sql[i + 1] == "-":
            end = sql.find("\n", i)
            if end == -1:
                result.append(" ")
                break
            result.append(" ")
            i = end
            continue

        if ch == "/" and i + 1 < length and sql[i + 1] == "*":
            depth = 1
            j = i + 2
            while j < length - 1 and depth > 0:
                if sql[j] == "/" and sql[j + 1] == "*":
                    depth += 1
                    j += 2
                elif sql[j] == "*" and sql[j + 1] == "/":
                    depth -= 1
                    j += 2
                else:
                    j += 1
            result.append(" ")
            if depth > 0:
                break
            i = j
            continue

        result.append(ch)
        i += 1

    return "".join(result)


def mask_sql_quoted_regions(sql: str) -> str:
    """Replace SQL string literals and quoted identifiers with whitespace."""
    result = []
    i = 0
    length = len(sql)
    quote_char: Optional[str] = None

    while i < length:
        ch = sql[i]

        if quote_char is None:
            if ch in ("'", '"', "`"):
                quote_char = ch
                result.append(" ")
            else:
                result.append(ch)
            i += 1
            continue

        result.append("\n" if ch == "\n" else " ")
        if ch == quote_char and not _is_escaped(sql, i):
            if i + 1 < length and sql[i + 1] == quote_char:
                result.append(" ")
                i += 2
                continue
            quote_char = None
        i += 1

    return "".join(result)


def _is_escaped(text: str, index: int) -> bool:
    backslash_count = 0
    position = index - 1
    while position >= 0 and text[position] == "\\":
        backslash_count += 1
        position -= 1
    return backslash_count % 2 == 1


_DOLLAR_QUOTE_RE = re.compile(r"\$[A-Za-z_0-9]*\$")


def _match_dollar_tag(text: str, index: int) -> Optional[str]:
    match = _DOLLAR_QUOTE_RE.match(text, index)
    if not match:
        return None
    return match.group(0)


def _first_statement(sql: str) -> str:
    s = strip_sql_comments(sql).strip()
    if not s:
        return ""

    in_single_quote = False
    in_double_quote = False
    in_backtick = False
    in_bracket = False
    dollar_tag: Optional[str] = None

    i = 0
    length = len(s)
    while i < length:
        ch = s[i]

        if dollar_tag:
            if s.startswith(dollar_tag, i):
                i += len(dollar_tag)
                dollar_tag = None
                continue
            i += 1
            continue

        if in_single_quote:
            if ch == "'":
                if i + 1 < length and s[i + 1] == "'":
                    i += 2
                    continue
                if not _is_escaped(s, i):
                    in_single_quote = False
            i += 1
            continue

        if in_double_quote:
            if ch == '"':
                if i + 1 < length and s[i + 1] == '"':
                    i += 2
                    continue
                if not _is_escaped(s, i):
                    in_double_quote = False
            i += 1
            continue

        if in_backtick:
            if ch == "`":
                if i + 1 < length and s[i + 1] == "`":
                    i += 2
                    continue
                in_backtick = False
            i += 1
            continue

        if in_bracket:
            if ch == "]":
                in_bracket = False
            i += 1
            continue

        if ch == "'":
            in_single_quote = True
            i += 1
            continue
        if ch == '"':
            in_double_quote = True
            i += 1
            continue
        if ch == "`":
            in_backtick = True
            i += 1
            continue
        if ch == "[":
            in_bracket = True
            i += 1
            continue
        if ch == "$":
            tag = _match_dollar_tag(s, i)
            if tag:
                dollar_tag = tag
                i += len(tag)
                continue

        if ch == ";":
            return s[:i].strip()

        i += 1

    return s.strip()


_METADATA_RE: re.Pattern = re.compile(
    r"""(?ix)^\s*
    (?:
        show\b(?:\s+create\s+table|\s+catalogs|\s+databases|\s+tables|\s+functions|\s+views|\s+columns|\s+partitions)?
        |set\s+catalog\b
        |describe\b
        |pragma\b
    )
""",
)


def _metadata_pattern() -> re.Pattern:
    return _METADATA_RE


_KEYWORD_SQL_TYPE_MAP: Dict[str, SQLType] = {
    "SELECT": SQLType.SELECT,
    "VALUES": SQLType.SELECT,
    "WITH": SQLType.SELECT,
    "INSERT": SQLType.INSERT,
    "REPLACE": SQLType.INSERT,
    "UPDATE": SQLType.UPDATE,
    "DELETE": SQLType.DELETE,
    "MERGE": SQLType.MERGE,
    "CREATE": SQLType.DDL,
    "ALTER": SQLType.DDL,
    "DROP": SQLType.DDL,
    "TRUNCATE": SQLType.DDL,
    "RENAME": SQLType.DDL,
    "COMMENT": SQLType.DDL,
    "GRANT": SQLType.DDL,
    "REVOKE": SQLType.DDL,
    "ANALYZE": SQLType.DDL,
    "VACUUM": SQLType.DDL,
    "OPTIMIZE": SQLType.DDL,
    "COPY": SQLType.DDL,
    "REFRESH": SQLType.DDL,
    "SHOW": SQLType.METADATA_SHOW,
    "DESCRIBE": SQLType.METADATA_SHOW,
    "DESC": SQLType.METADATA_SHOW,
    "PRAGMA": SQLType.METADATA_SHOW,
    "EXPLAIN": SQLType.EXPLAIN,
    "USE": SQLType.CONTENT_SET,
    "SWITCH": SQLType.CONTENT_SET,
    "SET": SQLType.CONTENT_SET,
    "CALL": SQLType.CONTENT_SET,
    "EXEC": SQLType.CONTENT_SET,
    "EXECUTE": SQLType.CONTENT_SET,
    "BEGIN": SQLType.CONTENT_SET,
    "START": SQLType.CONTENT_SET,
    "COMMIT": SQLType.CONTENT_SET,
    "ROLLBACK": SQLType.CONTENT_SET,
}

_OPTIONAL_DDL_EXPRESSIONS: tuple[type[expressions.Expression], ...] = tuple(
    getattr(expressions, name) for name in ("Copy", "Refresh") if hasattr(expressions, name)
)


def _normalize_expression(
    expr: Optional[expressions.Expression],
) -> Optional[expressions.Expression]:
    while expr is not None and isinstance(expr, (expressions.Alias, expressions.Subquery, expressions.Paren)):
        expr = expr.this
    return expr


def _fallback_sql_type(statement: str) -> SQLType | None:
    if not statement:
        return None
    upper_stmt = statement.upper()
    match = re.match(r"\s*([A-Z_]+)", upper_stmt)
    keyword = match.group(1) if match else ""

    if keyword == "WITH":
        match_cte_target = re.search(r"\)\s*(SELECT|INSERT|UPDATE|DELETE|MERGE)\b", upper_stmt)
        if match_cte_target:
            keyword = match_cte_target.group(1)
        else:
            keyword = "SELECT"

    if not keyword:
        return None
    return _KEYWORD_SQL_TYPE_MAP.get(keyword)


def parse_sql_type(sql: str, dialect: str) -> SQLType:
    if not sql or not isinstance(sql, str):
        return SQLType.UNKNOWN

    stripped_sql = sql.strip()
    if not stripped_sql:
        return SQLType.UNKNOWN

    first_statement = _first_statement(stripped_sql)
    dialect_name = parse_dialect(dialect)
    try:
        parsed_expression = sqlglot.parse_one(
            first_statement, dialect=dialect_name, error_level=sqlglot.ErrorLevel.IGNORE
        )
        if parsed_expression is None:
            if dialect_name == "starrocks" and _metadata_pattern().match(first_statement):
                return SQLType.METADATA_SHOW
            inferred = _fallback_sql_type(first_statement)
            return inferred if inferred else SQLType.UNKNOWN
    except Exception:
        inferred = _fallback_sql_type(first_statement)
        return inferred if inferred else SQLType.UNKNOWN

    normalized_expression = _normalize_expression(parsed_expression)
    if isinstance(normalized_expression, expressions.Query):
        return SQLType.SELECT
    if isinstance(normalized_expression, expressions.Values):
        return SQLType.SELECT
    if isinstance(normalized_expression, expressions.Insert):
        return SQLType.INSERT
    if isinstance(normalized_expression, expressions.Merge):
        return SQLType.MERGE
    if isinstance(normalized_expression, expressions.Update):
        return SQLType.UPDATE
    if isinstance(normalized_expression, expressions.Delete):
        return SQLType.DELETE
    if isinstance(
        normalized_expression,
        (
            expressions.Create,
            expressions.Alter,
            expressions.Drop,
            expressions.TruncateTable,
            expressions.RenameColumn,
            expressions.Analyze,
            expressions.Comment,
            expressions.Grant,
        ),
    ):
        return SQLType.DDL
    if isinstance(
        normalized_expression,
        (expressions.Describe, expressions.Show, expressions.Pragma),
    ):
        return SQLType.METADATA_SHOW
    if isinstance(normalized_expression, expressions.Command):
        command_name = str(normalized_expression.args.get("this") or "").upper()
        if command_name in {"SHOW", "DESC", "DESCRIBE"}:
            return SQLType.METADATA_SHOW
        if command_name == "EXPLAIN":
            return SQLType.EXPLAIN
        if command_name == "REPLACE":
            return SQLType.INSERT
        if command_name in {"CALL", "EXEC", "EXECUTE"}:
            return SQLType.CONTENT_SET
        mapped = _KEYWORD_SQL_TYPE_MAP.get(command_name)
        return mapped if mapped else SQLType.CONTENT_SET
    if isinstance(
        normalized_expression,
        (
            expressions.Use,
            expressions.Transaction,
            expressions.Commit,
            expressions.Rollback,
            expressions.Set,
        ),
    ):
        return SQLType.CONTENT_SET
    if _OPTIONAL_DDL_EXPRESSIONS and isinstance(normalized_expression, _OPTIONAL_DDL_EXPRESSIONS):
        return SQLType.DDL

    inferred = _fallback_sql_type(first_statement)
    return inferred if inferred else SQLType.UNKNOWN


_CONTEXT_CMD_RE = re.compile(r"^\s*(use|set|switch)\b", flags=re.IGNORECASE)


def _identifier_name(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, expressions.Identifier):
        return value.name
    if isinstance(value, expressions.Literal):
        literal = value.this
        return literal if isinstance(literal, str) else str(literal)
    if isinstance(value, expressions.Table):
        return _identifier_name(value.this)
    if isinstance(value, expressions.Expression):
        return value.sql()
    if isinstance(value, str):
        return value.strip('"`[]')
    return str(value)


def _table_parts(table_expr: Optional[Table]) -> Dict[str, str]:
    if not isinstance(table_expr, Table):
        return {"catalog": "", "database": "", "identifier": ""}
    args = table_expr.args
    return {
        "catalog": _identifier_name(args.get("catalog")),
        "database": _identifier_name(args.get("db")),
        "identifier": _identifier_name(args.get("this")),
    }


def _parse_identifier_sequence(value: str, dialect: str) -> Dict[str, str]:
    parsed = sqlglot.parse_one(f"USE {value}", dialect=dialect, error_level=sqlglot.ErrorLevel.IGNORE)
    table_expr = parsed.this if isinstance(parsed, expressions.Use) else None
    return _table_parts(table_expr)


def parse_context_switch(sql: str, dialect: str) -> Optional[Dict[str, Any]]:
    if not sql or not isinstance(sql, str):
        return None

    statement = _first_statement(sql)
    if not statement:
        return None

    cmd_match = _CONTEXT_CMD_RE.match(statement)
    if not cmd_match:
        return None

    command = cmd_match.group(1).upper()
    normalized_dialect = parse_dialect(dialect)

    result: Dict[str, Any] = {
        "command": command,
        "target": "",
        "catalog_name": "",
        "database_name": "",
        "schema_name": "",
        "fuzzy": False,
        "raw": statement,
    }

    if command == "SWITCH":
        switch_match = re.match(r"^\s*SWITCH\s+(.+)$", statement, flags=re.IGNORECASE)
        if not switch_match:
            return None
        remainder = switch_match.group(1).rstrip(";").strip()
        if not remainder:
            return None
        parts = _parse_identifier_sequence(remainder, normalized_dialect)
        result["catalog_name"] = parts["identifier"] or parts["database"] or parts["catalog"]
        result["target"] = "catalog"
        return result

    if command == "USE":
        expression = sqlglot.parse_one(statement, dialect=normalized_dialect, error_level=sqlglot.ErrorLevel.IGNORE)
        if not isinstance(expression, expressions.Use):
            return None
        parts = _table_parts(expression.this)
        kind_expr = expression.args.get("kind")
        kind = kind_expr.name.upper() if isinstance(kind_expr, expressions.Var) else ""

        catalog = parts["catalog"]
        database = parts["database"]
        identifier = parts["identifier"]

        if not identifier and not database and not catalog:
            return None

        if kind == "CATALOG":
            result["catalog_name"] = identifier or database or catalog
            result["target"] = "catalog"
            return result

        if kind == "DATABASE":
            result["database_name"] = identifier or database
            result["target"] = "database"
            return result

        if kind == "SCHEMA":
            result["schema_name"] = identifier
            if catalog:
                result["catalog_name"] = catalog
            if database:
                result["database_name"] = database
            result["target"] = "schema"
            return result

        if normalized_dialect == "duckdb":
            if database:
                result["database_name"] = database
                result["schema_name"] = identifier
                result["target"] = "schema"
            else:
                result["schema_name"] = identifier
                result["target"] = "schema"
                result["fuzzy"] = True
            return result

        if normalized_dialect == "mysql":
            result["database_name"] = identifier
            result["target"] = "database"
            return result

        if normalized_dialect in ("starrocks", "doris"):
            if catalog or (database and not catalog):
                result["catalog_name"] = catalog or database
                result["database_name"] = identifier
            else:
                result["database_name"] = identifier
            result["target"] = "database"
            return result

        if normalized_dialect == "snowflake":
            if catalog:
                result["catalog_name"] = catalog
                result["database_name"] = database
                result["schema_name"] = identifier
                result["target"] = "schema"
            elif database:
                result["database_name"] = database
                result["schema_name"] = identifier
                result["target"] = "schema"
            else:
                result["database_name"] = identifier
                result["target"] = "database"
            return result

        if catalog:
            result["catalog_name"] = catalog
        if database:
            result["database_name"] = database
        result["schema_name"] = identifier
        result["target"] = "schema" if database or catalog else "database"
        return result

    if command == "SET":
        set_match = re.match(
            r"^\s*SET\s+(?:SESSION\s+)?(CATALOG|DATABASE|SCHEMA)\s+(.*)$",
            statement,
            flags=re.IGNORECASE,
        )
        if not set_match:
            return None

        target = set_match.group(1).upper()
        remainder = set_match.group(2).strip()
        remainder = remainder.rstrip(";").strip()
        if remainder.startswith("="):
            remainder = remainder[1:].strip()
        elif remainder.upper().startswith("TO "):
            remainder = remainder[3:].strip()

        if not remainder:
            return None

        parts = _parse_identifier_sequence(remainder, normalized_dialect)
        catalog = parts["catalog"]
        database = parts["database"]
        identifier = parts["identifier"]

        if target == "CATALOG":
            result["target"] = "catalog"
            result["catalog_name"] = identifier or database or catalog
            return result

        if target == "DATABASE":
            result["target"] = "database"
            result["catalog_name"] = catalog
            result["database_name"] = identifier or database
            return result

        if target == "SCHEMA":
            result["target"] = "schema"
            result["catalog_name"] = catalog
            result["database_name"] = database
            result["schema_name"] = identifier
            if normalized_dialect == "duckdb" and not database:
                result["fuzzy"] = False
            return result

    return None
