"""Load shared TPC-H rows into an explicitly configured, dedicated HANA schema."""

import argparse

from datus_hana import HanaConnector
from datus_hana.testing import config_from_env
from datus_hana.tpch_data import TPCH_DATA, TPCH_DDL, TPCH_TABLES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--database")
    parser.add_argument("--drop", action="store_true", help="Drop existing TPC-H tables in the dedicated target schema")
    args = parser.parse_args()
    config = config_from_env()
    changes = {key: value for key, value in vars(args).items() if value is not None and key not in {"drop", "schema"}}
    if args.schema:
        changes["schema_name"] = args.schema
    config = type(config)(**(config.model_dump() | changes))
    if not config.schema_name:
        raise ValueError("An explicit dedicated --schema / HANA_SCHEMA is required")
    with HanaConnector(config) as connector:
        existing = set(connector.get_tables(schema_name=config.schema_name))
        for table in reversed(TPCH_TABLES):
            if table in existing:
                if not args.drop:
                    raise ValueError("TPC-H tables already exist; use --drop only in the dedicated test schema")
                result = connector.execute_ddl(f'DROP TABLE "{table}"')
                if not result.success:
                    raise RuntimeError(result.error)
        for ddl in TPCH_DDL:
            result = connector.execute_ddl(ddl)
            if not result.success:
                raise RuntimeError(result.error)
        connector.execute_queries(TPCH_DATA)


if __name__ == "__main__":
    main()
