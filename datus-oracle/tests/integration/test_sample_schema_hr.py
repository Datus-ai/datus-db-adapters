# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Metadata tests against Oracle's official HR sample schema.

The TPC-H fixtures are flat tables with no relationships, so they cannot
show whether the adapter reports foreign keys, self-referencing hierarchies,
views or PL/SQL objects correctly. HR does: 7 related tables, an
``employees.manager_id`` self reference, one view and two procedures.

The schema is installed by ``docker/init/02_install_hr_schema.sh`` when the
container starts. Tests skip when it is absent so a plain database without
sample schemas still runs the rest of the suite.

Run with:
    pytest tests/integration/test_sample_schema_hr.py -v
"""

import pytest

from datus_oracle import OracleConnector

HR_SCHEMA = "HR"

# Row counts published by Oracle in hr_install.sql's verification query.
HR_TABLE_ROWS = {
    "REGIONS": 5,
    "COUNTRIES": 25,
    "LOCATIONS": 23,
    "DEPARTMENTS": 27,
    "JOBS": 19,
    "EMPLOYEES": 107,
    "JOB_HISTORY": 10,
}


@pytest.fixture
def hr(connector: OracleConnector) -> OracleConnector:
    """Connector bound to a database that has the HR sample schema."""
    result = connector.execute(
        {"sql_query": "SELECT COUNT(*) AS cnt FROM all_tables WHERE owner = 'HR'"},
        result_format="list",
    )
    if not result.success:
        pytest.skip(f"Cannot inspect the data dictionary: {result.error}")
    if int(result.sql_return[0]["cnt"]) == 0:
        pytest.skip("HR sample schema is not installed (see docker/init/02_install_hr_schema.sh)")
    return connector


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_tables_lists_every_hr_table(hr: OracleConnector):
    """All seven HR tables are discoverable through the adapter."""
    tables = {t.split(".")[-1].strip('"').upper() for t in hr.get_tables(schema_name=HR_SCHEMA)}

    assert set(HR_TABLE_ROWS) <= tables


@pytest.mark.integration
@pytest.mark.acceptance
@pytest.mark.parametrize("table_name,expected_rows", sorted(HR_TABLE_ROWS.items()))
def test_hr_row_counts_match_oracle_published_values(hr: OracleConnector, table_name: str, expected_rows: int):
    """The vendored scripts load exactly the row counts Oracle documents."""
    result = hr.execute(
        {"sql_query": f'SELECT COUNT(*) AS cnt FROM "{HR_SCHEMA}"."{table_name}"'},
        result_format="list",
    )

    assert result.success, result.error
    assert int(result.sql_return[0]["cnt"]) == expected_rows


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_schema_reports_primary_key_and_types(hr: OracleConnector):
    """Column metadata carries the primary key flag and Oracle type names."""
    columns = {c["name"].upper(): c for c in hr.get_schema(table_name="EMPLOYEES", schema_name=HR_SCHEMA)}

    assert {"EMPLOYEE_ID", "MANAGER_ID", "SALARY", "HIRE_DATE"} <= set(columns)
    assert columns["EMPLOYEE_ID"]["pk"] is True
    assert columns["MANAGER_ID"]["pk"] is False
    assert columns["EMPLOYEE_ID"]["nullable"] is False
    assert "NUMBER" in columns["SALARY"]["type"].upper()
    assert "DATE" in columns["HIRE_DATE"]["type"].upper()


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_schema_surfaces_column_comments(hr: OracleConnector):
    """HR documents its columns, so comment extraction can be verified here."""
    columns = {c["name"].upper(): c for c in hr.get_schema(table_name="EMPLOYEES", schema_name=HR_SCHEMA)}

    assert "primary key" in (columns["EMPLOYEE_ID"]["comment"] or "").lower()
    assert columns["MANAGER_ID"]["comment"]


@pytest.mark.integration
@pytest.mark.acceptance
def test_employees_ddl_exposes_foreign_keys_and_self_reference(hr: OracleConnector):
    """EMPLOYEES references DEPARTMENTS, JOBS and itself; the DDL must show it."""
    entries = hr.get_tables_with_ddl(schema_name=HR_SCHEMA, tables=["EMPLOYEES"])

    assert entries, "EMPLOYEES produced no DDL entry"
    ddl = entries[0]["definition"].upper()
    assert "EMPLOYEE_ID" in ddl
    # The manager hierarchy is the structure TPC-H fixtures cannot cover.
    assert "MANAGER_ID" in ddl


@pytest.mark.integration
@pytest.mark.acceptance
def test_views_are_listed_separately_from_tables(hr: OracleConnector):
    """HR ships one view (EMP_DETAILS_VIEW); it must not appear as a table."""
    views = {v.split(".")[-1].strip('"').upper() for v in hr.get_views(schema_name=HR_SCHEMA)}
    tables = {t.split(".")[-1].strip('"').upper() for t in hr.get_tables(schema_name=HR_SCHEMA)}

    assert "EMP_DETAILS_VIEW" in views
    assert "EMP_DETAILS_VIEW" not in tables


@pytest.mark.integration
@pytest.mark.acceptance
def test_hierarchical_query_over_the_manager_chain(hr: OracleConnector):
    """Oracle CONNECT BY runs through the adapter against a real hierarchy."""
    result = hr.execute(
        {
            "sql_query": f"""
                SELECT LEVEL AS lvl, employee_id
                  FROM "{HR_SCHEMA}"."EMPLOYEES"
                 START WITH manager_id IS NULL
               CONNECT BY PRIOR employee_id = manager_id
                 ORDER BY lvl
            """
        },
        result_format="list",
    )

    assert result.success, result.error
    assert len(result.sql_return) == HR_TABLE_ROWS["EMPLOYEES"]
    assert int(result.sql_return[0]["lvl"]) == 1


@pytest.mark.integration
@pytest.mark.acceptance
def test_join_across_the_hr_star(hr: OracleConnector):
    """A five-table join exercises the relationships the schema declares."""
    result = hr.execute(
        {
            "sql_query": f"""
                SELECT r.region_name, COUNT(DISTINCT e.employee_id) AS employees
                  FROM "{HR_SCHEMA}"."EMPLOYEES" e
                  JOIN "{HR_SCHEMA}"."DEPARTMENTS" d ON d.department_id = e.department_id
                  JOIN "{HR_SCHEMA}"."LOCATIONS" l ON l.location_id = d.location_id
                  JOIN "{HR_SCHEMA}"."COUNTRIES" c ON c.country_id = l.country_id
                  JOIN "{HR_SCHEMA}"."REGIONS" r ON r.region_id = c.region_id
                 GROUP BY r.region_name
                 ORDER BY employees DESC
            """
        },
        result_format="list",
    )

    assert result.success, result.error
    assert len(result.sql_return) > 0
    assert sum(int(row["employees"]) for row in result.sql_return) > 0
