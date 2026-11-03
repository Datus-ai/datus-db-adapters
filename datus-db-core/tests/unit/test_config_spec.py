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


@pytest.mark.parametrize(
    "values, valid",
    [
        ({"uri": "data.db"}, True),
        ({"db_path": "data.db"}, True),
        ({"uri": "x", "db_path": "x"}, False),
        ({}, False),
        ({"db_path": None}, False),
        ({"db_path": "x", "schema": "public"}, True),
        ({"uri": "x", "schema_name": "public"}, True),
        ({"uri": "x", "schema": "a", "schema_name": "b"}, False),
        ({"uri": "x", "unknown": True}, False),
    ],
)
def test_alias_schema_matches_config_for_nested_models(values, valid):
    from jsonschema import Draft202012Validator
    from pydantic import AliasChoices, ConfigDict

    class Paths(BaseModel):
        model_config = ConfigDict(extra="forbid", populate_by_name=True)
        uri: str = Field(validation_alias=AliasChoices("uri", "db_path"))
        schema_name: str | None = Field(default=None, alias="schema")

    class Envelope(BaseModel):
        connection: Paths

    meta = AdapterMetadata("nested", object, config_class=Envelope)
    schema = meta.get_config_schema()
    Draft202012Validator.check_schema(schema)
    payload = {"connection": values}
    assert Draft202012Validator(schema).is_valid(payload) is valid
    if valid:
        assert meta.validate_config(payload).connection.uri == values.get("uri", values.get("db_path"))
    else:
        with pytest.raises(ValidationError):
            meta.validate_config(payload)


@pytest.mark.parametrize("extra", ["ignore", "allow"])
@pytest.mark.parametrize(
    "values, valid",
    [
        ({}, True),
        ({"old": "v"}, True),
        ({"new": "v", "old": 42}, True),
        ({"new": 42, "old": "v"}, False),
        ({"old": 42}, False),
    ],
)
def test_schema_preserves_alias_precedence_for_allowed_extras(extra, values, valid):
    from jsonschema import Draft202012Validator
    from pydantic import AliasChoices, ConfigDict

    class Choices(BaseModel):
        model_config = ConfigDict(extra=extra)
        new: str = Field(default="default", validation_alias=AliasChoices("new", "old"))

    meta = AdapterMetadata("choices", object, config_class=Choices)
    schema = meta.get_config_schema()
    Draft202012Validator.check_schema(schema)
    assert Draft202012Validator(schema).is_valid(values) is valid
    if valid:
        assert meta.validate_config(values).new == values.get("new", values.get("old", "default"))
    else:
        with pytest.raises(ValidationError):
            meta.validate_config(values)
