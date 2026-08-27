# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from .config import TiDBConfig
from .connector import TiDBConnector
from .skills import get_tidb_sql_generation_notes

__version__ = "0.1.0"
__all__ = ["TiDBConnector", "TiDBConfig", "register"]


def register():
    """Register TiDB connector with Datus registry."""
    from datus_db_core import connector_registry

    connector_registry.register(
        "tidb",
        TiDBConnector,
        config_class=TiDBConfig,
        display_name="TiDB",
        capabilities={"database"},
        # sqlglot has no TiDB dialect; TiDB's SQL surface parses as MySQL.
        parser_dialect="mysql",
        sql_generation_notes=get_tidb_sql_generation_notes,
    )
