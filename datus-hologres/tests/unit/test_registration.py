# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from unittest.mock import Mock

import datus_db_core
from datus_hologres import HologresConfig, HologresConnector, register
from datus_hologres.handlers import build_hologres_uri, resolve_hologres_context


def test_register_registers_hologres_adapter(monkeypatch):
    mock_registry = Mock()
    monkeypatch.setattr(datus_db_core, "connector_registry", mock_registry)

    register()

    mock_registry.register.assert_called_once_with(
        "hologres",
        HologresConnector,
        config_class=HologresConfig,
        capabilities={"database", "schema"},
        uri_builder=build_hologres_uri,
        context_resolver=resolve_hologres_context,
    )
