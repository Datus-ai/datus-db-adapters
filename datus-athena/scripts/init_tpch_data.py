#!/usr/bin/env python3
"""Create tiny Athena TPC-H fixtures, or drop only their table definitions."""

import argparse
import os

from datus_athena import AthenaConfig, AthenaConnector
from datus_athena.tpch_data import build_tpch_ctas
from datus_db_core.testing.tpch import TPCH_TABLES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drop", action="store_true", help="Drop the five fixture definitions; leave S3 data intact")
    args = parser.parse_args()
    config = AthenaConfig(
        region=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"),
        workgroup=os.environ["ATHENA_WORKGROUP"],
        database=os.environ["ATHENA_DATABASE"],
        catalog=os.environ.get("ATHENA_CATALOG", "AwsDataCatalog"),
        output_location=os.environ.get("ATHENA_OUTPUT_LOCATION"),
    )
    if args.drop:
        database = "`" + config.database.replace("`", "``") + "`"
        view = AthenaConnector.quote_identifier(config.database) + '."tpch_region_view"'
        statements = [f"DROP VIEW IF EXISTS {view}"]
        statements.extend(f"DROP TABLE IF EXISTS {database}.`{name}`" for name in TPCH_TABLES)
    else:
        statements = build_tpch_ctas(os.environ["ATHENA_DATA_LOCATION"], config.database)
    with AthenaConnector(config) as connector:
        connector.test_connection()
        if not args.drop:
            if connector._workgroup_configuration().get("EnforceWorkGroupConfiguration"):
                raise RuntimeError(
                    "Prepare CTAS fixtures with a Workgroup that permits external_location; "
                    "then run acceptance against enforced Workgroups using the same fixtures"
                )
            q = connector.quote_identifier
            statements.append(
                f"CREATE VIEW {q(config.database)}.{q('tpch_region_view')} AS "
                f"SELECT r_regionkey, r_name FROM {connector.full_name(table_name='tpch_region')}"
            )
        for sql in statements:
            result = connector.execute_ddl(sql)
            if not result.success:
                raise RuntimeError(result.error)


if __name__ == "__main__":
    main()
