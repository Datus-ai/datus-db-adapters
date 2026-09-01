# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""
Shared TPC-H test data definitions for integration tests across all adapters.

Each adapter has dialect-specific DDL, but the table names, row counts, and
data values are identical. This module provides the shared constants and
helpers to generate INSERT statements with adapter-specific quoting:

- ``build_tpch_inserts`` — one multi-row INSERT per table (most dialects)
- ``build_tpch_row_inserts`` — one INSERT per row, for engines without
  multi-row VALUES support (e.g. Oracle 19c), with optional explicit
  ``DATE ''`` literals
"""

from datetime import date
from decimal import Decimal

TPCH_TABLES = [
    "tpch_region",
    "tpch_nation",
    "tpch_customer",
    "tpch_orders",
    "tpch_supplier",
]

ROW_COUNTS = [5, 25, 10, 15, 5]

# Row data for each table, in TPCH_TABLES order. Values are identical across
# all SQL dialects; DECIMAL columns use Decimal and DATE columns use date so
# the SQL literal formatting is exact and dialect helpers can special-case
# dates. This is the single copy of the data — never duplicate it.
_TPCH_ROWS = [
    # tpch_region: 5 rows (standard TPC-H)
    [
        (0, "AFRICA", "special Tiresias about the furiously even"),
        (1, "AMERICA", "hs use ironic, even requests"),
        (2, "ASIA", "ges. thinly even pinto beans ca"),
        (3, "EUROPE", "ly final courts cajole furiously final excuse"),
        (4, "MIDDLE EAST", "uickly special accounts cajole carefully"),
    ],
    # tpch_nation: 25 rows (standard TPC-H)
    [
        (0, "ALGERIA", 0, "haggle. carefully final deposits"),
        (1, "ARGENTINA", 1, "al foxes promise slyly"),
        (2, "BRAZIL", 1, "y alongside of the pending deposits"),
        (3, "CANADA", 1, "eas hang ironic, silent packages"),
        (4, "EGYPT", 4, "y above the carefully unusual theodolites"),
        (5, "ETHIOPIA", 0, "ven packages was slyly"),
        (6, "FRANCE", 3, "refully final requests"),
        (7, "GERMANY", 3, "l platelets. regular accounts"),
        (8, "INDIA", 2, "ss excuses cajole slyly"),
        (9, "INDONESIA", 2, "slyly express asymptotes"),
        (10, "IRAN", 4, "efully alongside of the slyly final"),
        (11, "IRAQ", 4, "nic deposits boost atop the quickly final"),
        (12, "JAPAN", 2, "ously. final, express gifts cajole"),
        (13, "JORDAN", 4, "ic deposits are blithely about the carefully"),
        (14, "KENYA", 0, "pending excuses haggle furiously deposits"),
        (15, "MOROCCO", 0, "rns. blithely bold courts among the closely"),
        (16, "MOZAMBIQUE", 0, "s. ironic, unusual asymptotes wake"),
        (17, "PERU", 1, "platelets. blithely pending dependencies"),
        (18, "CHINA", 2, "c dependencies. furiously express notornis"),
        (19, "ROMANIA", 3, "ular asymptotes are about the furious"),
        (20, "SAUDI ARABIA", 4, "ts. silent requests haggle"),
        (21, "VIETNAM", 2, "hely enticingly express accounts"),
        (22, "RUSSIA", 3, "requests against the platelets use"),
        (23, "UNITED KINGDOM", 3, "eans boost carefully special requests"),
        (24, "UNITED STATES", 1, "y final packages. slow foxes cajole"),
    ],
    # tpch_customer: 10 rows (simplified)
    [
        (1, "Customer#001", 0, Decimal("711.56"), "BUILDING"),
        (2, "Customer#002", 1, Decimal("121.65"), "AUTOMOBILE"),
        (3, "Customer#003", 2, Decimal("7498.12"), "AUTOMOBILE"),
        (4, "Customer#004", 3, Decimal("2866.83"), "MACHINERY"),
        (5, "Customer#005", 4, Decimal("794.47"), "HOUSEHOLD"),
        (6, "Customer#006", 5, Decimal("7638.57"), "AUTOMOBILE"),
        (7, "Customer#007", 18, Decimal("9561.95"), "AUTOMOBILE"),
        (8, "Customer#008", 8, Decimal("6819.74"), "BUILDING"),
        (9, "Customer#009", 12, Decimal("8324.07"), "FURNITURE"),
        (10, "Customer#010", 24, Decimal("2753.54"), "HOUSEHOLD"),
    ],
    # tpch_orders: 15 rows (simplified)
    [
        (1, 1, "O", Decimal("173665.47"), date(1996, 1, 2)),
        (2, 2, "O", Decimal("46929.18"), date(1996, 12, 1)),
        (3, 3, "F", Decimal("193846.25"), date(1993, 10, 14)),
        (4, 4, "O", Decimal("32151.78"), date(1995, 10, 11)),
        (5, 5, "F", Decimal("144659.20"), date(1994, 7, 30)),
        (6, 1, "F", Decimal("58749.59"), date(1992, 2, 21)),
        (7, 2, "O", Decimal("252004.18"), date(1996, 1, 10)),
        (8, 3, "O", Decimal("13309.60"), date(1995, 10, 11)),
        (9, 6, "F", Decimal("51135.56"), date(1993, 10, 14)),
        (10, 7, "F", Decimal("149149.20"), date(1993, 12, 18)),
        (11, 8, "O", Decimal("79258.24"), date(1996, 6, 20)),
        (12, 9, "F", Decimal("89911.07"), date(1993, 1, 29)),
        (13, 10, "O", Decimal("159364.60"), date(1995, 10, 21)),
        (14, 1, "O", Decimal("44694.46"), date(1995, 10, 22)),
        (15, 4, "F", Decimal("32632.18"), date(1993, 7, 16)),
    ],
    # tpch_supplier: 5 rows (simplified)
    [
        (1, "Supplier#001", 0, Decimal("5755.94")),
        (2, "Supplier#002", 1, Decimal("4032.68")),
        (3, "Supplier#003", 8, Decimal("4192.40")),
        (4, "Supplier#004", 18, Decimal("1276.49")),
        (5, "Supplier#005", 24, Decimal("3956.15")),
    ],
]


def _sql_literal(value, date_literal=False):
    """Render one Python value as a SQL literal.

    ``date_literal=True`` emits ``DATE 'YYYY-MM-DD'`` for date values, for
    dialects (e.g. Oracle) where a bare string would depend on session date
    format settings.
    """
    if value is None:
        return "NULL"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    if isinstance(value, date):
        return f"DATE '{value.isoformat()}'" if date_literal else f"'{value.isoformat()}'"
    return str(value)


def _check_tables_rows_aligned():
    if len(TPCH_TABLES) != len(_TPCH_ROWS):
        raise ValueError(
            f"TPCH_TABLES ({len(TPCH_TABLES)}) and _TPCH_ROWS ({len(_TPCH_ROWS)}) must have the same length"
        )


def build_tpch_inserts(quote_fn=None):
    """Build one multi-row INSERT statement per TPC-H table.

    Args:
        quote_fn: Callable that takes a bare table name and returns the
                  quoted/qualified form for the target dialect.
                  Examples:
                    lambda t: f"`{t}`"            # backtick-quoted
                    lambda t: f"`default`.`{t}`"  # database-qualified (Spark)
                    lambda t: t                    # unquoted (Hive)
                  If None, uses bare table names (no quoting).
    """
    if quote_fn is None:
        quote_fn = lambda t: t  # noqa: E731

    _check_tables_rows_aligned()

    inserts = []
    for table, rows in zip(TPCH_TABLES, _TPCH_ROWS):
        values = ",\n    ".join("(" + ", ".join(_sql_literal(v) for v in row) + ")" for row in rows)
        inserts.append(
            f"""
    INSERT INTO {quote_fn(table)} VALUES
    {values}
    """
        )
    return inserts


def build_tpch_row_inserts(quote_fn=None, date_literal=False):
    """Build single-row INSERT statements, grouped per TPC-H table.

    For engines without multi-row VALUES support (e.g. Oracle 19c). Returns a
    list parallel to TPCH_TABLES; each element is the list of INSERT
    statements for that table.

    Args:
        quote_fn: Same contract as in :func:`build_tpch_inserts`.
        date_literal: Emit explicit ``DATE 'YYYY-MM-DD'`` literals for DATE
                      values instead of plain quoted strings.
    """
    if quote_fn is None:
        quote_fn = lambda t: t  # noqa: E731

    _check_tables_rows_aligned()

    return [
        [
            f"INSERT INTO {quote_fn(table)} VALUES ({', '.join(_sql_literal(v, date_literal) for v in row)})"
            for row in rows
        ]
        for table, rows in zip(TPCH_TABLES, _TPCH_ROWS)
    ]
