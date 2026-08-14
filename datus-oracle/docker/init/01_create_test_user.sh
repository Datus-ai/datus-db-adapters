#!/usr/bin/env bash
set -euo pipefail

# Runs after every database start (mounted at /opt/oracle/scripts/startup), so
# the SQL must remain idempotent.
# Keep substitution into the SQL statement safe by accepting only unquoted
# Oracle-password characters.
: "${ORACLE_APP_PASSWORD:?Set ORACLE_APP_PASSWORD}"
if [[ ! "$ORACLE_APP_PASSWORD" =~ ^[A-Za-z][A-Za-z0-9_]{7,127}$ ]]; then
  echo "ORACLE_APP_PASSWORD must start with a letter and contain only letters, digits, or underscores (8-128 characters)." >&2
  exit 1
fi

ORACLE_PDB="${ORACLE_PDB:-ORCLPDB1}"
if [[ ! "$ORACLE_PDB" =~ ^[A-Za-z][A-Za-z0-9_]{0,127}$ ]]; then
  echo "ORACLE_PDB must be an unquoted Oracle identifier." >&2
  exit 1
fi

sqlplus -s / as sysdba <<SQL
WHENEVER SQLERROR EXIT SQL.SQLCODE

ALTER SESSION SET CONTAINER = ${ORACLE_PDB};

DECLARE
    user_count PLS_INTEGER;
BEGIN
    SELECT COUNT(*)
      INTO user_count
      FROM dba_users
     WHERE username = 'DATUS_TEST';

    IF user_count = 0 THEN
        EXECUTE IMMEDIATE
            'CREATE USER datus_test IDENTIFIED BY ${ORACLE_APP_PASSWORD} DEFAULT TABLESPACE users';
    ELSE
        EXECUTE IMMEDIATE
            'ALTER USER datus_test IDENTIFIED BY ${ORACLE_APP_PASSWORD} ACCOUNT UNLOCK';
    END IF;
END;
/

ALTER USER datus_test QUOTA UNLIMITED ON users;

GRANT CREATE SESSION TO datus_test;
GRANT CREATE TABLE TO datus_test;
GRANT CREATE VIEW TO datus_test;
GRANT CREATE MATERIALIZED VIEW TO datus_test;

EXIT;
SQL
