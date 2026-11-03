"""JSON Schema generation for the flat input aliases declared by Config models."""

from copy import deepcopy

from pydantic import AliasChoices
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaValue
from pydantic_core import core_schema


class ConnectionSchemaGenerator(GenerateJsonSchema):
    """Retain input aliases and their selection rules without duplicating fields.

    Pydantic normally exports only the first AliasChoices name. Expand flat
    aliases from model metadata, including nested models. Python validators and
    coercions still run through the real Config; they are not translated here.
    """

    def model_schema(self, schema: core_schema.ModelSchema) -> JsonSchemaValue:
        result = super().model_schema(schema)
        if self.mode != "validation" or not self.by_alias or "properties" not in result:
            return result

        model = schema["cls"]
        config = model.model_config
        properties = result["properties"]
        for name, field in model.model_fields.items():
            alias = field.validation_alias
            if isinstance(alias, str):
                aliases = [alias]
            elif isinstance(alias, AliasChoices) and all(isinstance(a, str) for a in alias.choices):
                aliases = list(dict.fromkeys(alias.choices))
            else:
                # Leave nested AliasPath inputs to Pydantic's own generator.
                continue
            source = aliases[0]
            if source not in properties:
                continue
            names = aliases if config.get("validate_by_alias", True) else []
            by_name = config.get("validate_by_name", config.get("populate_by_name", not names))
            if by_name and name not in names:
                names = [*names, name]
            if names == [source] or not names:
                continue

            field_schema = properties.pop(source)
            required = result.get("required", [])
            is_required = source in required
            if is_required:
                required.remove(source)
                if not required:
                    result.pop("required", None)
            forbid_extra = config.get("extra") == "forbid"
            alternatives = []
            for index, input_name in enumerate(names):
                # With ignored/allowed extras, only the first present alias is
                # validated by Pydantic. Later aliases must remain unrestricted.
                properties[input_name] = deepcopy(field_schema) if forbid_extra else {}
                branch = {"required": [input_name], "properties": {input_name: deepcopy(field_schema)}}
                excluded = [n for n in names if n != input_name] if forbid_extra else names[:index]
                if excluded:
                    branch["not"] = {"anyOf": [{"required": [n]} for n in excluded]}
                alternatives.append(branch)
            if not is_required:
                alternatives.append({"not": {"anyOf": [{"required": [n]} for n in names]}})
            result.setdefault("allOf", []).append({"oneOf": alternatives})
        return result
