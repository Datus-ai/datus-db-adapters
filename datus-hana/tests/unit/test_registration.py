from importlib.metadata import entry_points

import pytest

from datus_db_core import connector_registry
from datus_hana import HanaConfig, HanaConnector, register
from datus_hana.analysis import analyze_hana_sql
from datus_hana.dialect_operations import HanaDialectOperations
from datus_hana.handlers import build_hana_uri, parse_hana_identifier, resolve_hana_context


def test_registration_contract():
    register()
    meta = connector_registry.get_metadata("hana")
    assert meta.config_class is HanaConfig
    assert meta.connector_class is HanaConnector
    assert connector_registry.get_capabilities("hana") == {"schema"}
    assert connector_registry.get_sql_analyzer("hana") is analyze_hana_sql
    assert connector_registry.get_identifier_parser("hana") is parse_hana_identifier
    assert connector_registry.get_context_resolver("hana") is resolve_hana_context
    assert connector_registry.get_uri_builder("hana") is build_hana_uri
    assert isinstance(connector_registry.get_dialect_operations("hana"), HanaDialectOperations)
    ep = next(ep for ep in entry_points(group="datus.adapters") if ep.name == "hana")
    assert ep.load() is register


def test_unregistered_adapters_have_no_new_dependency():
    assert connector_registry.get_sql_analyzer("unregistered-dialect") is None


def test_old_loaded_core_requires_process_restart(monkeypatch):
    monkeypatch.setattr(connector_registry, "get_sql_analyzer", None)
    with pytest.raises(RuntimeError, match="upgrade and restart"):
        register()
