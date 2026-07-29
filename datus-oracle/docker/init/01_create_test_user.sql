-- Runs once after database creation (mounted at /opt/oracle/scripts/setup).
-- Creates the integration-test app user in the FREEPDB1 pluggable database.
-- Deliberately a plain user without DBA_* access or SELECT_CATALOG_ROLE:
-- the adapter must work through ALL_* dictionary views only.
ALTER SESSION SET CONTAINER = FREEPDB1;

CREATE USER datus_test IDENTIFIED BY test_password
    DEFAULT TABLESPACE users
    QUOTA UNLIMITED ON users;

GRANT CREATE SESSION TO datus_test;
GRANT CREATE TABLE TO datus_test;
GRANT CREATE VIEW TO datus_test;
GRANT CREATE MATERIALIZED VIEW TO datus_test;

EXIT;
