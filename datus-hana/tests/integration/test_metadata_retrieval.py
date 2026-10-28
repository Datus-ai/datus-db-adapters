import pytest

from datus_hana.handlers import parse_hana_identifier

pytestmark = pytest.mark.integration


def test_metadata_and_samples(connector, config, metadata_objects_setup):
    objects = metadata_objects_setup
    listed_tables = {
        parse_hana_identifier(identifier)["table_name"]: identifier
        for identifier in connector.get_tables(schema_name=config.schema_name)
    }
    listed_views = {
        parse_hana_identifier(identifier)["table_name"]: identifier
        for identifier in connector.get_views(schema_name=config.schema_name)
    }
    table_ref = listed_tables[objects["table"]]
    view_ref = listed_views[objects["view"]]
    assert table_ref == objects["table_ref"]
    assert view_ref == connector.identifier(schema_name=config.schema_name, table_name=objects["view"])
    assert config.schema_name in connector.get_schemas()
    for identifier in (table_ref, view_ref):
        parts = parse_hana_identifier(identifier)
        assert [col["name"] for col in connector.get_schema(**parts)] == ["Mixed Column", "text"]
    samples = connector.get_sample_rows(tables=[objects["table_ref"]], top_n=1)
    assert len(samples) == 1
    assert samples[0]["identifier"] == objects["table_ref"]
    assert samples[0]["sample_rows"] == "Mixed Column,text\n1,中文\n"
    ddl = connector.get_tables_with_ddl(tables=[table_ref])
    assert len(ddl) == 1
    assert ddl[0]["identifier"] == table_ref
    assert ddl[0]["table_name"] == objects["table"]
    assert '"Mixed Column" INTEGER' in ddl[0]["definition"]
    view_ddls = connector.get_views_with_ddl()
    assert view_ref in {row["identifier"] for row in view_ddls}
