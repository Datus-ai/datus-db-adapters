import pytest
from botocore.exceptions import ClientError

from .conftest import results, start, status

PARAMS = dict(CatalogName="AwsDataCatalog", DatabaseName="analytics", WorkGroup="datus-test")
TABLE = dict(
    Name="orders",
    TableType="EXTERNAL_TABLE",
    Columns=[{"Name": "id", "Type": "bigint"}],
    PartitionKeys=[{"Name": "day", "Type": "date", "Comment": "partition"}],
)
VIEW = dict(Name="orders_view", TableType="VIRTUAL_VIEW")
MISSING_ENTITY = "Entity Not Found (Service: AmazonDataCatalog; Error Code: EntityNotFoundException; Request ID: test)"


def object_page(stubber, objects, **kwargs):
    response = {"TableMetadataList": objects}
    if next_token := kwargs.pop("next_token", None):
        response["NextToken"] = next_token
    stubber.add_response("list_table_metadata", response, dict(PARAMS, MaxResults=50, **kwargs))


def metadata(stubber, table=TABLE):
    stubber.add_response("get_table_metadata", {"TableMetadata": table}, dict(PARAMS, TableName=table["Name"]))


def test_catalog_pagination_only_declares_glue(make_connector, sdk):
    _, stubber = sdk
    stubber.add_response(
        "list_data_catalogs",
        {
            "DataCatalogsSummary": [
                {"CatalogName": "AwsDataCatalog", "Type": "GLUE"},
                {"CatalogName": "remote", "Type": "LAMBDA"},
            ],
            "NextToken": "more",
        },
        {"WorkGroup": "datus-test", "MaxResults": 50},
    )
    stubber.add_response(
        "list_data_catalogs",
        {"DataCatalogsSummary": [{"CatalogName": "account2", "Type": "GLUE"}]},
        {"WorkGroup": "datus-test", "MaxResults": 50, "NextToken": "more"},
    )
    assert make_connector().get_catalogs() == ["AwsDataCatalog", "account2"]


@pytest.mark.parametrize(
    "include_sys,expected",
    [(False, ["analytics", "reporting"]), (True, ["analytics", "information_schema", "reporting"])],
)
def test_database_pagination(make_connector, sdk, include_sys, expected):
    _, stubber = sdk
    params = {"CatalogName": "other", "WorkGroup": "datus-test", "MaxResults": 50}
    stubber.add_response(
        "list_databases",
        {"DatabaseList": [{"Name": "analytics"}, {"Name": "information_schema"}], "NextToken": "more"},
        params,
    )
    stubber.add_response("list_databases", {"DatabaseList": [{"Name": "reporting"}]}, dict(params, NextToken="more"))
    assert make_connector().get_databases("other", include_sys) == expected


def test_table_and_view_pagination(make_connector, sdk):
    _, stubber = sdk
    conn = make_connector()
    object_page(stubber, [TABLE, VIEW], next_token="more")
    object_page(stubber, [dict(Name="items", TableType="MANAGED_TABLE")], NextToken="more")
    assert conn.get_tables() == ["orders", "items"]
    object_page(stubber, [TABLE, VIEW])
    assert conn.get_views() == ["orders_view"]


def test_columns_include_partitions_once(make_connector, sdk):
    _, stubber = sdk
    metadata(stubber, dict(TABLE, Columns=TABLE["Columns"] + TABLE["PartitionKeys"]))
    assert make_connector().get_schema(table_name="orders") == [
        dict(
            cid=0,
            name="id",
            type="bigint",
            comment=None,
            nullable=True,
            pk=False,
            default_value=None,
            is_partition=False,
        ),
        dict(
            cid=1,
            name="day",
            type="date",
            comment="partition",
            nullable=True,
            pk=False,
            default_value=None,
            is_partition=True,
        ),
    ]


@pytest.mark.parametrize(
    "kind,name,sql",
    [("table", "orders", "SHOW CREATE TABLE `orders`"), ("view", "orders_view", 'SHOW CREATE VIEW "orders_view"')],
)
def test_original_ddl_and_metadata_shape(make_connector, sdk, kind, name, sql):
    _, stubber = sdk
    conn = make_connector()
    if kind == "table":
        metadata(stubber)
    else:
        object_page(stubber, [TABLE, VIEW])
    start(stubber, sql)
    status(stubber, statement_type="UTILITY")
    results(
        stubber,
        [["CREATE EXTERNAL TABLE orders ("], ["id bigint)"]],
        [{"Name": "createtab_stmt", "Type": "varchar"}],
        header=False,
    )
    entries = (
        conn.get_tables_with_ddl(tables=["AwsDataCatalog.analytics.orders"])
        if kind == "table"
        else conn.get_views_with_ddl()
    )
    assert entries == [
        dict(
            identifier=f"AwsDataCatalog.analytics.{name}",
            catalog_name="AwsDataCatalog",
            database_name="analytics",
            schema_name="",
            table_name=name,
            table_type=kind,
            definition="CREATE EXTERNAL TABLE orders (\nid bigint)",
        )
    ]


def test_out_of_scope_ddl_filter_has_no_metadata_calls(make_connector):
    with pytest.raises(ValueError, match="outside"):
        make_connector().get_tables_with_ddl(tables=["other.db.orders"])


@pytest.mark.parametrize("code", ["MetadataException", "EntityNotFoundException"])
@pytest.mark.parametrize("include_existing", [False, True])
def test_targeted_ddl_omits_missing_tables(make_connector, sdk, code, include_existing):
    _, stubber = sdk
    stubber.add_client_error(
        "get_table_metadata", code, MISSING_ENTITY, expected_params=dict(PARAMS, TableName="absent")
    )
    stubber.add_response("get_database", {"Database": {"Name": "analytics"}}, PARAMS)
    if include_existing:
        metadata(stubber)
        start(stubber, "SHOW CREATE TABLE `orders`")
        status(stubber, statement_type="UTILITY")
        results(
            stubber,
            [["CREATE TABLE orders (id bigint)"]],
            [{"Name": "createtab_stmt", "Type": "varchar"}],
            header=False,
        )
    entries = make_connector().get_tables_with_ddl(tables=["absent", "orders"] if include_existing else ["absent"])
    assert [entry["table_name"] for entry in entries] == (["orders"] if include_existing else [])
    if include_existing:
        assert entries[0]["definition"] == "CREATE TABLE orders (id bigint)"


@pytest.mark.parametrize("scope_error", ["MetadataException", "AccessDeniedException"])
def test_targeted_ddl_does_not_hide_missing_or_inaccessible_database(make_connector, sdk, scope_error):
    _, stubber = sdk
    stubber.add_client_error(
        "get_table_metadata", "MetadataException", MISSING_ENTITY, expected_params=dict(PARAMS, TableName="absent")
    )
    stubber.add_client_error("get_database", scope_error, "scope unavailable", expected_params=PARAMS)
    with pytest.raises(ClientError, match="scope unavailable"):
        make_connector().get_tables_with_ddl(tables=["absent"])


@pytest.mark.parametrize(
    "code, message",
    [
        ("AccessDeniedException", "denied"),
        ("MetadataException", "Error Code: AccessDeniedException;"),
        ("MetadataException", "custom metadata failure"),
        ("InternalServerException", "unavailable"),
        ("InvalidRequestException", "invalid request"),
    ],
)
def test_targeted_ddl_preserves_other_metadata_errors(make_connector, sdk, code, message):
    _, stubber = sdk
    stubber.add_client_error("get_table_metadata", code, message, expected_params=dict(PARAMS, TableName="orders"))
    with pytest.raises(ClientError) as exc:
        make_connector().get_tables_with_ddl(tables=["orders"])
    assert exc.value.response["Error"] == {"Code": code, "Message": message}


def test_schema_lookup_still_reports_missing_table(make_connector, sdk):
    _, stubber = sdk
    stubber.add_client_error(
        "get_table_metadata", "MetadataException", MISSING_ENTITY, expected_params=dict(PARAMS, TableName="absent")
    )
    with pytest.raises(ClientError, match="EntityNotFoundException"):
        make_connector().get_schema(table_name="absent")


def test_ddl_error_is_not_silent(make_connector, sdk):
    _, stubber = sdk
    object_page(stubber, [TABLE])
    start(stubber, "SHOW CREATE TABLE `orders`")
    status(stubber, Status={"State": "FAILED", "StateChangeReason": "not authorized"})
    with pytest.raises(RuntimeError, match="not authorized"):
        make_connector().get_tables_with_ddl()


def test_samples_preserve_context_and_chinese(make_connector, sdk):
    _, stubber = sdk
    metadata(stubber)
    sql = 'SELECT * FROM "AwsDataCatalog"."analytics"."orders" LIMIT 1'
    start(stubber, sql)
    status(stubber)
    results(stubber, [["数据"]], [{"Name": "text", "Type": "varchar"}])
    assert make_connector().get_sample_rows(tables=["orders"], top_n=1) == [
        dict(
            identifier="AwsDataCatalog.analytics.orders",
            catalog_name="AwsDataCatalog",
            database_name="analytics",
            schema_name="",
            table_name="orders",
            table_type="table",
            sample_rows="text\n数据\n",
        )
    ]


def test_sample_lists_objects_and_filters_kind(make_connector, sdk):
    _, stubber = sdk
    object_page(stubber, [TABLE, VIEW])
    sql = 'SELECT * FROM "AwsDataCatalog"."analytics"."orders_view" LIMIT 5'
    start(stubber, sql)
    status(stubber)
    results(stubber, [[1]])
    rows = make_connector().get_sample_rows(table_type="view")
    assert len(rows) == 1 and rows[0]["table_name"] == "orders_view"
    assert rows[0]["sample_rows"] == "value\n1\n"


@pytest.mark.parametrize("name", ["123table", "sales-data", "literal.dot", 'quoted"table'])
@pytest.mark.parametrize("explicit", [False, True])
def test_samples_quote_raw_catalog_names(make_connector, sdk, name, explicit):
    _, stubber = sdk
    table = dict(TABLE, Name=name)
    if explicit:
        metadata(stubber, table)
    else:
        object_page(stubber, [table])
    quoted = '"' + name.replace('"', '""') + '"'
    start(stubber, f'SELECT * FROM "AwsDataCatalog"."analytics".{quoted} LIMIT 1')
    status(stubber)
    results(stubber, [[1]])
    entries = make_connector().get_sample_rows(tables=[quoted] if explicit else None, top_n=1)
    assert entries == [
        dict(
            identifier=f"AwsDataCatalog.analytics.{name}",
            catalog_name="AwsDataCatalog",
            database_name="analytics",
            schema_name="",
            table_name=name,
            table_type="table",
            sample_rows="value\n1\n",
        )
    ]


@pytest.mark.parametrize("kwargs", [{"top_n": 0}, {"table_type": "bad"}])
def test_sample_validation(make_connector, kwargs):
    with pytest.raises(ValueError):
        make_connector().get_sample_rows(**kwargs)


def test_sample_failure_does_not_look_like_empty_metadata(make_connector, sdk):
    _, stubber = sdk
    object_page(stubber, [TABLE])
    start(stubber, 'SELECT * FROM "AwsDataCatalog"."analytics"."orders" LIMIT 5')
    status(stubber, Status={"State": "FAILED", "StateChangeReason": "data access denied"})
    with pytest.raises(RuntimeError, match="data access denied"):
        make_connector().get_sample_rows()


def test_missing_database(make_connector):
    conn = make_connector(database=None)
    with pytest.raises(ValueError, match="database is required"):
        conn.get_tables()
    with pytest.raises(ValueError, match="database is required"):
        conn.get_schema(table_name="orders")
    with pytest.raises(ValueError, match="database is required"):
        conn.full_name(table_name="orders")


def test_metadata_error_propagates(make_connector, sdk):
    _, stubber = sdk
    stubber.add_client_error(
        "list_table_metadata", service_error_code="AccessDeniedException", service_message="denied"
    )
    with pytest.raises(Exception, match="denied"):
        make_connector().get_tables()


def test_metadata_repeated_token(make_connector, sdk):
    _, stubber = sdk
    object_page(stubber, [TABLE], next_token="repeat")
    object_page(stubber, [TABLE], NextToken="repeat", next_token="repeat")
    with pytest.raises(RuntimeError, match="pagination token"):
        make_connector().get_tables()


def test_connection_uses_catalog_discovery_and_checks_metadata_and_actual_query(make_connector, sdk):
    _, stubber = sdk
    stubber.add_response(
        "list_data_catalogs",
        {"DataCatalogsSummary": [{"CatalogName": "AwsDataCatalog", "Type": "GLUE"}]},
        {"MaxResults": 50, "WorkGroup": "datus-test"},
    )
    stubber.add_response("get_database", {"Database": {"Name": "analytics"}}, PARAMS)
    start(stubber, "SELECT 1 AS datus_connection_test")
    status(stubber)
    results(stubber, [[1]], [{"Name": "datus_connection_test", "Type": "integer"}])
    assert make_connector().test_connection() is True


@pytest.mark.parametrize("catalogs", [[], [{"CatalogName": "AwsDataCatalog", "Type": "LAMBDA"}]])
def test_missing_or_unsupported_catalog_is_explicit(make_connector, sdk, catalogs):
    _, stubber = sdk
    stubber.add_response(
        "list_data_catalogs",
        {"DataCatalogsSummary": catalogs},
        {"MaxResults": 50, "WorkGroup": "datus-test"},
    )
    with pytest.raises(ValueError, match="Glue"):
        make_connector().test_connection()


def test_empty_ddl_is_an_error(make_connector, sdk):
    _, stubber = sdk
    object_page(stubber, [TABLE])
    start(stubber, "SHOW CREATE TABLE `orders`")
    status(stubber, statement_type="UTILITY")
    results(stubber, [], [{"Name": "createtab_stmt", "Type": "varchar"}], header=False)
    with pytest.raises(RuntimeError, match="complete DDL"):
        make_connector().get_tables_with_ddl()


def test_connection_without_database_still_checks_metadata_and_query(make_connector, sdk):
    _, stubber = sdk
    stubber.add_response(
        "list_data_catalogs",
        {"DataCatalogsSummary": [{"CatalogName": "AwsDataCatalog", "Type": "GLUE"}]},
        {"MaxResults": 50, "WorkGroup": "datus-test"},
    )
    stubber.add_response(
        "list_databases",
        {"DatabaseList": []},
        {"CatalogName": "AwsDataCatalog", "WorkGroup": "datus-test", "MaxResults": 50},
    )
    start(stubber, "SELECT 1 AS datus_connection_test", context={"Catalog": "AwsDataCatalog"})
    status(stubber)
    results(stubber, [[1]], [{"Name": "datus_connection_test", "Type": "integer"}])
    assert make_connector(database=None).test_connection() is True
