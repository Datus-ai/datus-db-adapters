# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Reconciliation check generation for cross-database migration.

Generates pairs of SQL queries (source vs target) for data reconciliation
after a migration transfer. Dialect-agnostic: uses standard SQL only.
"""

import re
from typing import Callable, List, Optional

from datus_db_core.logging import get_logger

logger = get_logger(__name__)

_NUMERIC_TYPE_PATTERN = re.compile(
    r"^(INTEGER|INT[248]?|BIGINT|SMALLINT|TINYINT|FLOAT[48]?|DOUBLE|REAL|DECIMAL|NUMERIC|HUGEINT|LARGEINT)\b",
    re.IGNORECASE,
)

_DATE_TYPE_PATTERN = re.compile(
    r"^(DATE|TIMESTAMP|DATETIME|TIME)\b",
    re.IGNORECASE,
)


def _is_numeric_type(col_type: str) -> bool:
    col_type = col_type.strip()
    return not col_type.endswith("]") and bool(_NUMERIC_TYPE_PATTERN.match(col_type))


def _is_date_type(col_type: str) -> bool:
    col_type = col_type.strip()
    return not col_type.endswith("]") and bool(_DATE_TYPE_PATTERN.match(col_type))


def _is_minmax_type(col_type: str) -> bool:
    return _is_numeric_type(col_type) or _is_date_type(col_type)


def _quote_identifier(name: str) -> str:
    """Quote a SQL identifier with double quotes to handle reserved words and special characters."""
    escaped = name.replace('"', '""')
    return f'"{escaped}"'


def build_reconciliation_checks(
    source_table: str,
    target_table: str,
    columns: List[dict],
    key_columns: Optional[List[str]] = None,
    quote_identifier: Optional[Callable[[str], str]] = None,
) -> List[dict]:
    """Build reconciliation check SQL pairs for source vs target comparison.

    Args:
        source_table: Fully qualified source table name.
        target_table: Fully qualified target table name.
        columns: List of column defs with name, type, nullable.
        key_columns: Optional list of key/PK column names. If None or empty,
                     key-dependent checks (duplicate_key, sample_diff) are skipped.
        quote_identifier: Optional callable to quote identifiers per dialect.
                          Defaults to double-quote quoting (ANSI SQL). Pass a
                          backtick-quoting callable for MySQL/StarRocks/ClickHouse,
                          or the connector's own quote_identifier when available.

    Returns:
        List of check dicts, each with: name, source_query, target_query.
    """
    quote = quote_identifier or _quote_identifier
    checks = []
    has_keys = bool(key_columns)

    # 1. Row count
    checks.append(
        {
            "name": "row_count",
            "source_query": f"SELECT COUNT(*) AS row_count FROM {source_table}",
            "target_query": f"SELECT COUNT(*) AS row_count FROM {target_table}",
        }
    )

    # 2. Null ratio — for all nullable columns
    nullable_cols = [c for c in columns if c.get("nullable", True)]
    if nullable_cols:
        src_parts = []
        tgt_parts = []
        for col in nullable_cols:
            qname = quote(col["name"])
            alias = col["name"].replace('"', "")
            src_parts.append(f"COUNT(*) - COUNT({qname}) AS {quote(alias + '_null_count')}")
            tgt_parts.append(f"COUNT(*) - COUNT({qname}) AS {quote(alias + '_null_count')}")

        src_parts.append("COUNT(*) AS total")
        tgt_parts.append("COUNT(*) AS total")

        checks.append(
            {
                "name": "null_ratio",
                "source_query": f"SELECT {', '.join(src_parts)} FROM {source_table}",
                "target_query": f"SELECT {', '.join(tgt_parts)} FROM {target_table}",
            }
        )

    # 3. Min/max — for numeric and date columns
    minmax_cols = [c for c in columns if _is_minmax_type(c["type"])]
    if minmax_cols:
        src_parts = []
        tgt_parts = []
        for col in minmax_cols:
            qname = quote(col["name"])
            alias = col["name"].replace('"', "")
            min_alias = quote(alias + "_min")
            max_alias = quote(alias + "_max")
            src_parts.append(f"MIN({qname}) AS {min_alias}, MAX({qname}) AS {max_alias}")
            tgt_parts.append(f"MIN({qname}) AS {min_alias}, MAX({qname}) AS {max_alias}")

        checks.append(
            {
                "name": "min_max",
                "source_query": f"SELECT {', '.join(src_parts)} FROM {source_table}",
                "target_query": f"SELECT {', '.join(tgt_parts)} FROM {target_table}",
            }
        )

    # 4. Distinct count — for key columns or all columns
    distinct_cols = key_columns if has_keys else [c["name"] for c in columns]
    if distinct_cols:
        if has_keys and len(distinct_cols) > 1:
            key_expr = ", ".join(quote(c) for c in distinct_cols)
            checks.append(
                {
                    "name": "distinct_count",
                    "source_query": f"SELECT COUNT(*) AS distinct_key_count FROM (SELECT DISTINCT {key_expr} FROM {source_table}) t",
                    "target_query": f"SELECT COUNT(*) AS distinct_key_count FROM (SELECT DISTINCT {key_expr} FROM {target_table}) t",
                }
            )
        else:
            src_parts = [f"COUNT(DISTINCT {quote(c)}) AS {quote(c + '_distinct')}" for c in distinct_cols]
            tgt_parts = [f"COUNT(DISTINCT {quote(c)}) AS {quote(c + '_distinct')}" for c in distinct_cols]
            checks.append(
                {
                    "name": "distinct_count",
                    "source_query": f"SELECT {', '.join(src_parts)} FROM {source_table}",
                    "target_query": f"SELECT {', '.join(tgt_parts)} FROM {target_table}",
                }
            )

    # 5. Duplicate key — only if key columns provided
    if has_keys:
        quoted_keys = [quote(k) for k in key_columns]
        key_str = ", ".join(quoted_keys)
        if len(key_columns) > 1:
            all_keys = ", ".join(quoted_keys)
            checks.append(
                {
                    "name": "duplicate_key",
                    "source_query": (
                        f"SELECT {all_keys}, COUNT(*) AS cnt FROM {source_table} "
                        f"GROUP BY {key_str} HAVING COUNT(*) > 1 ORDER BY {key_str} LIMIT 5"
                    ),
                    "target_query": (
                        f"SELECT {all_keys}, COUNT(*) AS cnt FROM {target_table} "
                        f"GROUP BY {key_str} HAVING COUNT(*) > 1 ORDER BY {key_str} LIMIT 5"
                    ),
                }
            )
        else:
            checks.append(
                {
                    "name": "duplicate_key",
                    "source_query": (
                        f"SELECT {key_str}, COUNT(*) AS cnt FROM {source_table} "
                        f"GROUP BY {key_str} HAVING COUNT(*) > 1 ORDER BY {key_str} LIMIT 5"
                    ),
                    "target_query": (
                        f"SELECT {key_str}, COUNT(*) AS cnt FROM {target_table} "
                        f"GROUP BY {key_str} HAVING COUNT(*) > 1 ORDER BY {key_str} LIMIT 5"
                    ),
                }
            )

    # 6. Sample diff — key-based sample, only if key columns provided
    if has_keys:
        quoted_keys = [quote(k) for k in key_columns]
        key_order = ", ".join(quoted_keys)
        all_cols = ", ".join(quote(c["name"]) for c in columns)
        checks.append(
            {
                "name": "sample_diff",
                "source_query": (f"SELECT {all_cols} FROM {source_table} ORDER BY {key_order} LIMIT 10"),
                "target_query": (f"SELECT {all_cols} FROM {target_table} ORDER BY {key_order} LIMIT 10"),
            }
        )

    # 7. Numeric aggregate — SUM/AVG for numeric columns
    numeric_cols = [c for c in columns if _is_numeric_type(c["type"])]
    if numeric_cols:
        src_parts = []
        tgt_parts = []
        for col in numeric_cols:
            qname = quote(col["name"])
            alias = col["name"].replace('"', "")
            sum_alias = quote(alias + "_sum")
            avg_alias = quote(alias + "_avg")
            src_parts.append(f"SUM({qname}) AS {sum_alias}, AVG({qname}) AS {avg_alias}")
            tgt_parts.append(f"SUM({qname}) AS {sum_alias}, AVG({qname}) AS {avg_alias}")

        checks.append(
            {
                "name": "numeric_aggregate",
                "source_query": f"SELECT {', '.join(src_parts)} FROM {source_table}",
                "target_query": f"SELECT {', '.join(tgt_parts)} FROM {target_table}",
            }
        )

    return checks
