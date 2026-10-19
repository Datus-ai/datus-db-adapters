import pytest

from datus_db_core import connector_registry


@pytest.fixture(autouse=True)
def preserve_registry():
    attrs = ("_connectors", "_factories", "_metadata", "_capabilities", "_uri_builders", "_context_resolvers")
    saved = {name: dict(getattr(connector_registry, name)) for name in attrs}
    yield
    for name, values in saved.items():
        getattr(connector_registry, name).clear()
        getattr(connector_registry, name).update(values)
