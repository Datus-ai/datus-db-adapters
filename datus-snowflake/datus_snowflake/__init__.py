"""Snowflake adapter for Datus Agent."""

from .config import SnowflakeConfig
from .connector import SnowflakeConnector
from .skills import get_snowflake_sql_generation_notes

__version__ = "0.1.0"
__all__ = ["SnowflakeConnector", "SnowflakeConfig", "register"]


def register():
    """Register Snowflake connector with Datus registry."""
    from datus_db_core import connector_registry

    connector_registry.register(
        "snowflake",
        SnowflakeConnector,
        config_class=SnowflakeConfig,
        capabilities={"database", "schema"},
        sql_generation_notes=get_snowflake_sql_generation_notes,
    )
