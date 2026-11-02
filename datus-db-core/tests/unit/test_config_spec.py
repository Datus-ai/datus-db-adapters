import pytest
from pydantic import BaseModel, Field, ValidationError, model_validator

from datus_db_core.registry import AdapterMetadata, ConnectorRegistry


class Config(BaseModel):
    port: int = Field(default=9030, gt=0)
    password: str | None = None
    secret_ref: str | None = None

    @model_validator(mode="after")
    def exclusive_credentials(self):
        if self.password is not None and self.secret_ref is not None:
            raise ValueError("choose one credential source")
        return self


def test_export_does_not_claim_to_translate_python_validators():
    meta = AdapterMetadata("sample", object, config_class=Config)
    schema = meta.get_config_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["port"]["default"] == 9030
    assert schema["properties"]["port"]["exclusiveMinimum"] == 0
    assert meta.validate_config({"port": "9040"}).model_dump() == {
        "port": 9040,
        "password": None,
        "secret_ref": None,
    }
    with pytest.raises(ValidationError, match="choose one credential source"):
        meta.validate_config({"password": "example", "secret_ref": "ref"})


def test_missing_config_is_an_explicit_gap():
    with pytest.raises(ValueError, match="no Pydantic Config"):
        AdapterMetadata("sample", object).get_config_schema()


def test_registry_resolves_real_config_without_constructing_connector():
    class Registry(ConnectorRegistry):
        _connectors = {}
        _metadata = {}
        _factories = {}
        _capabilities = {}
        _uri_builders = {}
        _context_resolvers = {}

    class Connector:
        def __init__(self, config):
            raise AssertionError("spec lookup must not connect")

    Registry.register("postgresql", Connector, config_class=Config)
    assert Registry.validate_config("postgres", {}).port == 9030
    assert Registry.get_config_schema("postgres") == Registry.get_config_schema("postgresql")
