# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

"""Verified common-query analysis only; the SDK executes the original SQL."""

import sqlglot
from sqlglot import exp
from sqlglot.dialects.postgres import Postgres
from sqlglot.optimizer.scope import traverse_scope
from sqlglot.tokens import TokenType

from datus_db_core.constants import SQLType
from datus_db_core.sql_analysis import SQLAnalysis

from .handlers import quote_identifier

# Verified native scalar functions whose object access is confined to their
# arguments. Some remain anonymous calls on supported parser versions.
# Qualified/quoted calls may resolve to user code and are not covered here.
_NATIVE_SCALARS = {
    "TO_VARCHAR",
    "TO_DECIMAL",
    "TO_BIGINT",
    "ADD_DAYS",
    "ADD_YEARS",
    "DAYS_BETWEEN",
    "SECONDS_BETWEEN",
    "WEEKDAY",
    "HOUR",
    "MINUTE",
}


def _verified_scalar(node: exp.Anonymous) -> bool:
    return isinstance(node.this, str) and node.name.upper() in _NATIVE_SCALARS and not isinstance(node.parent, exp.Dot)


class _HanaAnalysisDialect(Postgres):
    class Parser(Postgres.Parser):
        def _parse_function_call(self, functions=None, anonymous=False, optional_parens=True, any_token=False):
            # Preserve delimited function names as user calls. SQLGlot can
            # otherwise normalize them into built-in nodes and lose quoting.
            quoted = self._curr is not None and self._curr.token_type == TokenType.IDENTIFIER
            return super()._parse_function_call(
                functions=functions,
                anonymous=anonymous or quoted,
                optional_parens=optional_parens and not quoted,
                any_token=any_token,
            )


def analyze_hana_sql(sql: str) -> SQLAnalysis:
    try:
        statements = sqlglot.parse(sql, read=_HanaAnalysisDialect, error_level=sqlglot.ErrorLevel.RAISE)
    except sqlglot.errors.SqlglotError:
        return SQLAnalysis("error", diagnostic="SQL is outside the verified HANA common-query subset")
    if len(statements) != 1 or statements[0] is None:
        return SQLAnalysis("unsupported", diagnostic="Exactly one SQL statement is required")
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        return SQLAnalysis("unsupported", diagnostic="Local analysis supports common SELECT queries only")
    if any(isinstance(node, (exp.DML, exp.DDL, exp.Command, exp.Into, exp.Lock)) for node in tree.walk()):
        return SQLAnalysis("unsupported", diagnostic="Query contains a write, lock, or opaque statement")
    if any(
        isinstance(node, exp.UDTF) or isinstance(node, exp.Anonymous) and not _verified_scalar(node)
        for node in tree.walk()
    ):
        return SQLAnalysis("unsupported", diagnostic="Unverified scalar/table functions have unknown object access")
    # The compatible parser treats HANA sequence access as a qualified column.
    if any(column.table and column.name.upper() in {"NEXTVAL", "CURRVAL"} for column in tree.find_all(exp.Column)):
        return SQLAnalysis("unsupported", diagnostic="Sequence access is outside the verified read-only subset")
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
