"""Provision the dedicated Express test user after tenant authentication works."""

import os
import re
import time

from hdbcli import dbapi

from datus_hana import HanaConnector
from datus_hana.handlers import quote_identifier
from datus_hana.testing import config_from_env


def main():
    config = config_from_env()
    if config.userkey:
        raise ValueError("Express bootstrap requires dedicated test username/password")
    # CREATE USER takes a Unicode user name, not a table's delimited identifier.
    # Keep the bootstrap identity to an explicit, injection-free CI contract.
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,126}", config.username):
        raise ValueError("Express bootstrap username must use uppercase ASCII letters, digits and underscores")
    deadline = time.monotonic() + int(os.getenv("HANA_READY_TIMEOUT", "1200"))
    while True:
        try:
            connection = dbapi.connect(
                address=config.host,
                port=config.port,
                user="SYSTEM",
                password=os.environ["DATUS_TEST_HANA_PASSWORD"],
                encrypt=False,
                connectTimeout=5000,
            )
            break
        except dbapi.Error:
            if time.monotonic() >= deadline:
                raise RuntimeError("HANA tenant did not become ready before timeout") from None
            time.sleep(5)
    try:
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT 1 FROM DUMMY")
            assert cursor.fetchone()[0] == 1
            # All values are runtime-only and no credential-bearing SQL is logged.
            user = config.username
            schema = quote_identifier(config.schema_name or config.username)
            cursor.execute("SELECT USER_NAME FROM SYS.USERS WHERE USER_NAME = ?", (config.username,))
            if cursor.fetchone() is None:
                cursor.execute(
                    f"CREATE USER {user} PASSWORD {quote_identifier(config.password)} NO FORCE_FIRST_PASSWORD_CHANGE"
                )
            cursor.execute(
                "SELECT SCHEMA_NAME FROM SYS.SCHEMAS WHERE SCHEMA_NAME = ?", (config.schema_name or config.username,)
            )
            if cursor.fetchone() is None:
                cursor.execute(f"CREATE SCHEMA {schema} OWNED BY {user}")
            connection.commit()
        finally:
            cursor.close()
    finally:
        connection.close()
    HanaConnector(config).test_connection()


if __name__ == "__main__":
    main()
