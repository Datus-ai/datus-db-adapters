import pytest

pytestmark = pytest.mark.integration


def test_metadata_and_samples(connector, config, metadata_objects_setup):
    objects = metadata_objects_setup
    assert objects["table"] in connector.get_tables(schema_name=config.schema_name)
    assert objects["view"] in connector.get_views(schema_name=config.schema_name)
    assert config.schema_name in connector.get_schemas()
    assert [col["name"] for col in connector.get_schema(table_name=objects["table"])] == ["Mixed Column", "text"]
    assert [col["name"] for col in connector.get_schema(table_name=objects["view"])] == ["Mixed Column", "text"]
    samples = connector.get_sample_rows(tables=[objects["table_ref"]], top_n=1)
    assert len(samples) == 1
    assert samples[0]["identifier"] == objects["table_ref"]
    assert samples[0]["sample_rows"] == "Mixed Column,text\n1,中文\n"
    ddl = connector.get_tables_with_ddl(tables=[objects["table"]])
    assert len(ddl) == 1
    assert ddl[0]["table_name"] == objects["table"]
    assert '"Mixed Column" INTEGER' in ddl[0]["definition"]
