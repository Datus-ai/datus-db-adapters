#!/usr/bin/env bash
set -euo pipefail

# Runs once after database creation (mounted at /opt/oracle/scripts/setup).
# Keep substitution into the SQL statement safe by accepting only unquoted
# Oracle-password characters.
: "${ORACLE_APP_PASSWORD:?Set ORACLE_APP_PASSWORD}"
if [[ ! "$ORACLE_APP_PASSWORD" =~ ^[A-Za-z][A-Za-z0-9_]{7,127}$ ]]; then
  echo "ORACLE_APP_PASSWORD must start with a letter and contain only letters, digits, or underscores (8-128 characters)." >&2
  exit 1
fi

sqlplus -s / as sysdba <<SQL
WHENEVER SQLERROR EXIT SQL.SQLCODE

ALTER SESSION SET CONTAINER = FREEPDB1;

CREATE USER datus_test IDENTIFIED BY ${ORACLE_APP_PASSWORD}
    DEFAULT TABLESPACE users
    QUOTA UNLIMITED ON users;

GRANT CREATE SESSION TO datus_test;
GRANT CREATE TABLE TO datus_test;
GRANT CREATE VIEW TO datus_test;
GRANT CREATE MATERIALIZED VIEW TO datus_test;

EXIT;
SQL
