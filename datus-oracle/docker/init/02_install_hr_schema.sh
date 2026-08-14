#!/usr/bin/env bash
set -euo pipefail

# Installs Oracle's official HR sample schema (see
# ../sample-schemas/human_resources/README.md) so integration tests can run
# against foreign keys, a self-referencing hierarchy, a view and PL/SQL
# objects — structures the TPC-H fixtures do not cover.
#
# Runs after every database start (mounted at /opt/oracle/scripts/startup), so
# it exits early once HR is present. Set ORACLE_SKIP_SAMPLE_SCHEMAS=1 to skip
# the installation entirely.

if [[ "${ORACLE_SKIP_SAMPLE_SCHEMAS:-0}" == "1" ]]; then
  echo "HR sample schema installation skipped (ORACLE_SKIP_SAMPLE_SCHEMAS=1)."
  exit 0
fi

SCHEMA_DIR="${ORACLE_SAMPLE_SCHEMA_DIR:-/opt/oracle/sample-schemas}/human_resources"
if [[ ! -f "$SCHEMA_DIR/hr_create.sql" ]]; then
  echo "HR sample schema sources not found under $SCHEMA_DIR; skipping installation." >&2
  exit 0
fi

# Keep substitution into the SQL statement safe by accepting only unquoted
# Oracle-password characters, matching 01_create_test_user.sh.
: "${ORACLE_APP_PASSWORD:?Set ORACLE_APP_PASSWORD}"
HR_PASSWORD="${ORACLE_HR_PASSWORD:-$ORACLE_APP_PASSWORD}"
if [[ ! "$HR_PASSWORD" =~ ^[A-Za-z][A-Za-z0-9_]{7,127}$ ]]; then
  echo "HR password must start with a letter and contain only letters, digits, or underscores (8-128 characters)." >&2
  exit 1
fi

ORACLE_PDB="${ORACLE_PDB:-ORCLPDB1}"
if [[ ! "$ORACLE_PDB" =~ ^[A-Za-z][A-Za-z0-9_]{0,127}$ ]]; then
  echo "ORACLE_PDB must be an unquoted Oracle identifier." >&2
  exit 1
fi

# Key the probe on an installed object, not on the HR user: an install that
# aborted midway leaves the user behind with no tables, and a user-only check
# would treat that empty schema as complete forever.
already_installed="$(sqlplus -s / as sysdba <<SQL
WHENEVER SQLERROR EXIT SQL.SQLCODE
SET HEADING OFF FEEDBACK OFF PAGESIZE 0
ALTER SESSION SET CONTAINER = ${ORACLE_PDB};
SELECT COUNT(*) FROM dba_tables WHERE owner = 'HR' AND table_name = 'EMPLOYEES';
EXIT;
SQL
)"
already_installed="$(echo "$already_installed" | tr -d '[:space:]')"

# sqlplus still exits 0 on some failures, so anything but a number means the
# probe itself failed; treating that as "installed" would silently skip the
# installation and leave every HR test skipping too.
if [[ ! "$already_installed" =~ ^[0-9]+$ ]]; then
  echo "Could not determine whether the HR schema is installed: ${already_installed}" >&2
  exit 1
fi

if [[ "$already_installed" != "0" ]]; then
  echo "HR sample schema already installed; nothing to do."
  exit 0
fi

echo "Installing the HR sample schema from $SCHEMA_DIR ..."

# The upstream scripts are run with CURRENT_SCHEMA=HR, exactly as upstream's
# hr_install.sql does. The NLS settings are required: hr_populate.sql relies on
# American date and number formats.
sqlplus -s / as sysdba <<SQL
WHENEVER SQLERROR EXIT SQL.SQLCODE
WHENEVER OSERROR EXIT FAILURE
SET ECHO OFF FEEDBACK OFF VERIFY OFF

ALTER SESSION SET CONTAINER = ${ORACLE_PDB};

-- Reaching here means the schema is incomplete, so a leftover HR user is the
-- remains of a failed install: drop it and start from a clean slate.
DECLARE
    user_count PLS_INTEGER;
BEGIN
    SELECT COUNT(*) INTO user_count FROM dba_users WHERE username = 'HR';
    IF user_count > 0 THEN
        EXECUTE IMMEDIATE 'DROP USER hr CASCADE';
    END IF;
END;
/

CREATE USER hr IDENTIFIED BY ${HR_PASSWORD} DEFAULT TABLESPACE users QUOTA UNLIMITED ON users;

GRANT CREATE MATERIALIZED VIEW,
      CREATE PROCEDURE,
      CREATE SEQUENCE,
      CREATE SESSION,
      CREATE SYNONYM,
      CREATE TABLE,
      CREATE TRIGGER,
      CREATE TYPE,
      CREATE VIEW
  TO hr;

-- The test user reads HR through the adapter, so it needs its own grants.
GRANT SELECT ANY TABLE TO datus_test;
GRANT SELECT_CATALOG_ROLE TO datus_test;

ALTER SESSION SET CURRENT_SCHEMA = HR;
ALTER SESSION SET NLS_LANGUAGE = American;
ALTER SESSION SET NLS_TERRITORY = America;

@${SCHEMA_DIR}/hr_create.sql
@${SCHEMA_DIR}/hr_populate.sql
@${SCHEMA_DIR}/hr_code.sql

EXIT;
SQL

echo "HR sample schema installed."
