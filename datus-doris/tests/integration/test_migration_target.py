# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Integration coverage for the migration-target interface.

``dry_run_ddl`` is the only validation path that executes against a live Doris
instance, so it cannot be covered by the unit suite.
"""

import pytest

from datus_doris import DorisConfig, DorisConnector

SCRATCH_PREFIX = "__datus_dry_run_"


def _table_names(connector: DorisConnector, config: DorisConfig) -> list[str]:
    return connector.get_tables(catalog_name=config.catalog, database_name=config.database)


def _assert_no_scratch_tables(connector: DorisConnector, config: DorisConfig) -> None:
    leftovers = [name for name in _table_names(connector, config) if SCRATCH_PREFIX in name]
    assert leftovers == [], f"dry_run_ddl left scratch tables behind: {leftovers}"


@pytest.mark.integration
@pytest.mark.acceptance
def test_dry_run_ddl_accepts_valid_ddl(connector: DorisConnector, config: DorisConfig):
    target = f"{config.database}.datus_dry_run_probe"
    ddl = f"""
        CREATE TABLE {target} (
            `id` BIGINT NOT NULL,
            `name` VARCHAR(64)
        ) ENGINE=OLAP
        DUPLICATE KEY (`id`)
        DISTRIBUTED BY HASH(`id`) BUCKETS 1
        PROPERTIES ("replication_num" = "1")
    """

    assert connector.dry_run_ddl(ddl, target) == []

    _assert_no_scratch_tables(connector, config)
    # The dry run must never create the table the DDL actually names.
    assert "datus_dry_run_probe" not in _table_names(connector, config)


@pytest.mark.integration
def test_dry_run_ddl_reports_ddl_doris_rejects(connector: DorisConnector, config: DorisConfig):
    """A FLOAT key column passes a naive text check but Doris refuses to create it."""
    target = f"{config.database}.datus_dry_run_bad_key"
    ddl = f"""
        CREATE TABLE {target} (
            `score` DOUBLE NOT NULL,
            `value` INT
        ) ENGINE=OLAP
        DUPLICATE KEY (`score`)
        DISTRIBUTED BY HASH(`score`) BUCKETS 1
        PROPERTIES ("replication_num" = "1")
    """

    errors = connector.dry_run_ddl(ddl, target)

    assert errors, "expected dry_run_ddl to report an error"
    _assert_no_scratch_tables(connector, config)


@pytest.mark.integration
def test_dry_run_ddl_reports_implicit_layout_even_though_doris_accepts_it(
    connector: DorisConnector,
    config: DorisConfig,
):
    """The migration policy fires on DDL the engine itself would happily create."""
    target = f"{config.database}.datus_dry_run_implicit"
    ddl = f'CREATE TABLE {target} (`id` BIGINT NOT NULL) PROPERTIES ("replication_num" = "1")'

    errors = connector.dry_run_ddl(ddl, target)

    assert any("must define one of" in error for error in errors)
    assert any("must include a DISTRIBUTED BY clause" in error for error in errors)
    _assert_no_scratch_tables(connector, config)


@pytest.mark.integration
def test_dry_run_ddl_reports_an_implicit_bucket_count(connector: DorisConnector, config: DorisConfig):
    """Doris creates this table with 10 buckets; the policy still refuses it.

    A DISTRIBUTED BY clause that stops short of BUCKETS is the same silent
    default as an omitted clause, and is equally unreviewable in a migration.
    """
    target = f"{config.database}.datus_dry_run_no_buckets"
    ddl = (
        f"CREATE TABLE {target} (`id` BIGINT NOT NULL) ENGINE=OLAP "
        "DUPLICATE KEY (`id`) DISTRIBUTED BY HASH(`id`) "
        'PROPERTIES ("replication_num" = "1")'
    )

    errors = connector.dry_run_ddl(ddl, target)

    assert any("must state a bucket count" in error for error in errors)
    _assert_no_scratch_tables(connector, config)
    assert "datus_dry_run_no_buckets" not in _table_names(connector, config)


@pytest.mark.integration
def test_dry_run_ddl_does_not_create_a_table_named_in_a_leading_comment(
    connector: DorisConnector,
    config: DorisConfig,
):
    """Retargeting must follow the CREATE target, not the first textual match.

    When a comment above the statement names the same table, a substring
    replacement consumes itself there and leaves CREATE TABLE pointing at the
    real name — which the dry run would then create for good, since cleanup
    only ever drops the scratch name.
    """
    target = f"{config.database}.datus_dry_run_commented"
    ddl = f"""
        /* rebuild of {target} */
        CREATE TABLE {target} (
            `id` BIGINT NOT NULL
        ) ENGINE=OLAP
        DUPLICATE KEY (`id`)
        DISTRIBUTED BY HASH(`id`) BUCKETS 1
        PROPERTIES ("replication_num" = "1")
    """

    assert connector.dry_run_ddl(ddl, target) == []

    _assert_no_scratch_tables(connector, config)
    assert "datus_dry_run_commented" not in _table_names(connector, config)


@pytest.mark.integration
def test_dry_run_ddl_leaves_an_existing_table_untouched(
    connector: DorisConnector,
    config: DorisConfig,
    unique_key_table: str,
):
    """Dry-running DDL against an existing table name must not drop or alter it."""
    target = f"{config.database}.{unique_key_table}"
    ddl = f"""
        CREATE TABLE {target} (
            `id` BIGINT NOT NULL
        ) ENGINE=OLAP
        DUPLICATE KEY (`id`)
        DISTRIBUTED BY HASH(`id`) BUCKETS 1
        PROPERTIES ("replication_num" = "1")
    """

    assert connector.dry_run_ddl(ddl, target) == []

    assert unique_key_table in _table_names(connector, config)
    schema = connector.get_schema(
        catalog_name=config.catalog,
        database_name=config.database,
        table_name=unique_key_table,
    )
    # Still the original UNIQUE KEY table, not the DUPLICATE KEY one from the DDL.
    assert {column["name"] for column in schema} == {"id", "name"}
    assert next(column for column in schema if column["name"] == "id")["key_type"] == "UNI"
    _assert_no_scratch_tables(connector, config)
