# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from .config import ClickZettaConfig
from .connector import ClickZettaConnector

__version__ = "0.1.0"
__all__ = ["ClickZettaConnector", "ClickZettaConfig", "register"]


def register():
    """Register ClickZetta connector with Datus registry."""
    from datus_db_core import connector_registry

    def clickzetta_factory(config):
        """Factory function to create ClickZetta connector from config dict."""
        if isinstance(config, dict):
            return ClickZettaConnector(
                service=config.get("service", ""),
                username=config.get("username", ""),
                password=config.get("password", ""),
                instance=config.get("instance", ""),
                workspace=config.get("workspace", ""),
                schema=config.get("schema", "PUBLIC"),
                vcluster=config.get("vcluster", "DEFAULT_AP"),
                secure=config.get("secure", None),
                hints=config.get("hints", None),
                extra=config.get("extra", None),
                timeout_seconds=config.get("timeout_seconds", 30),
                query_timeout_seconds=config.get("query_timeout_seconds"),
            )
        else:
            # Handle other config types (like Pydantic models)
            return ClickZettaConnector(
                service=getattr(config, "service", ""),
                username=getattr(config, "username", ""),
                password=getattr(config, "password", ""),
                instance=getattr(config, "instance", ""),
                workspace=getattr(config, "workspace", ""),
                schema=getattr(config, "schema", "PUBLIC"),
                vcluster=getattr(config, "vcluster", "DEFAULT_AP"),
                secure=getattr(config, "secure", None),
                hints=getattr(config, "hints", None),
                extra=getattr(config, "extra", None),
                timeout_seconds=getattr(config, "timeout_seconds", 30),
                query_timeout_seconds=getattr(config, "query_timeout_seconds", None),
            )

    # Register directly with factory - the ClickZetta connector is self-contained
    connector_registry.register(
        "clickzetta",
        ClickZettaConnector,
        clickzetta_factory,
        config_class=ClickZettaConfig,
        capabilities={"database", "schema"},
    )
