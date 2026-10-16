# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

"""Verified common-query analysis only; the SDK executes the original SQL."""

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from datus_db_core.constants import SQLType
from datus_db_core.sql_analysis import SQLAnalysis

from .handlers import quote_identifier


def analyze_hana_sql(sql: str) -> SQLAnalysis:
    try:
        statements = sqlglot.parse(sql, read="postgres", error_level=sqlglot.ErrorLevel.RAISE)
    except sqlglot.errors.SqlglotError:
        return SQLAnalysis("error", diagnostic="SQL is outside the verified HANA common-query subset")
    if len(statements) != 1 or statements[0] is None:
        return SQLAnalysis("unsupported", diagnostic="Exactly one SQL statement is required")
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        return SQLAnalysis("unsupported", diagnostic="Local analysis supports common SELECT queries only")
    if any(isinstance(node, (exp.DML, exp.DDL, exp.Command, exp.Into, exp.Lock)) for node in tree.walk()):
        return SQLAnalysis("unsupported", diagnostic="Query contains a write, lock, or opaque statement")
    if any(isinstance(node, (exp.Anonymous, exp.UDTF)) for node in tree.walk()):
        return SQLAnalysis("unsupported", diagnostic="Unverified scalar/table functions have unknown object access")
    # Normalize only the analysis tree, never the SQL sent to HANA.
    for identifier in tree.find_all(exp.Identifier):
        if not identifier.args.get("quoted"):
            identifier.set("this", identifier.name.upper())
    for table in tree.find_all(exp.Table):
        if len(table.parts) > 2 or any(not isinstance(part, exp.Identifier) for part in table.parts):
            return SQLAnalysis("unsupported", diagnostic="Table functions and three-part object names are unsupported")
    tables = []
    for scope in traverse_scope(tree):
        for source in scope.sources.values():
            if isinstance(source, exp.Table):
                name = ".".join(quote_identifier(part.name) for part in source.parts)
                if name not in tables:
                    tables.append(name)
    return SQLAnalysis("complete", SQLType.SELECT, tuple(tables))
