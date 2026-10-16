from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from datus_db_core import DatusDbException
from datus_hana import HanaConfig, HanaConnector


@pytest.fixture
def sdk():
    with patch("datus_hana.connector.dbapi.connect") as connect:
        connection = connect.return_value
        cursor = connection.cursor.return_value
        cursor.description = [(name,) for name in ("id", "amount", "text", "day", "stamp", "optional")]
        cursor.fetchmany.side_effect = [
            [(1, Decimal("1.20"), "中文", date(2024, 1, 2), datetime(2024, 1, 2, 3, 4), None)],
            [],
        ]
        yield connect, connection, cursor


@pytest.fixture
def connector():
    return HanaConnector(HanaConfig(host="hana", username="USER", password="secret", schema="CaseSensitive"))


@pytest.mark.parametrize("fmt", ["list", "csv", "pandas", "arrow"])
def test_sdk_roundtrip_formats_and_cleanup(connector, sdk, fmt):
    connect, connection, cursor = sdk
    sql = "SELECT * FROM t WHERE x = ?"
    result = connector.execute_query(sql, fmt, parameters=("';DELETE FROM t",))
    assert result.success, result.error
    assert result.row_count == 1
    cursor.execute.assert_called_once_with(sql, ("';DELETE FROM t",))
    connection.setautocommit.assert_called_once_with(False)
    connection.commit.assert_called_once()
    cursor.close.assert_called_once()
    connection.close.assert_called_once()
    assert connect.call_args.kwargs == {
        "address": "hana",
        "port": 39041,
        "user": "USER",
        "password": "secret",
        "currentSchema": "CaseSensitive",
        "connectTimeout": 30000,
        "communicationTimeout": 60000,
        "encrypt": True,
        "sslValidateCertificate": True,
    }
    if fmt == "csv":
        assert "中文" in result.sql_return
        assert "1.20" in result.sql_return
    else:
        rows = (
            result.sql_return
            if fmt == "list"
            else result.sql_return.to_pylist()
            if fmt == "arrow"
            else result.sql_return.to_dict("records")
        )
        assert rows[0] == {
            "id": 1,
            "amount": Decimal("1.20"),
            "text": "中文",
            "day": date(2024, 1, 2),
            "stamp": datetime(2024, 1, 2, 3, 4),
            "optional": None,
        }


def test_userkey_tls_and_tenant_properties(sdk):
    config = HanaConfig(
        userkey="KEY", database="HXE", ssl_trust_store="ca.pem", ssl_host_name_in_certificate="hana.internal"
    )
    connector = HanaConnector(config)
    assert connector.execute_query("SELECT 1 FROM DUMMY").success
    args = sdk[0].call_args.kwargs
    assert args["userkey"] == "KEY"
    assert args["databaseName"] == "HXE"
    assert args["sslTrustStore"] == "ca.pem"
    assert args["sslHostNameInCertificate"] == "hana.internal"
    assert not {"address", "port", "password", "user"} & args.keys()


def test_error_rolls_back_and_redacts_credentials(connector, sdk):
    error = RuntimeError("secret: authentication failed")
    error.errorcode = 10
    sdk[2].execute.side_effect = error
    result = connector.execute_query("SELECT 1 FROM DUMMY")
    assert not result.success
    assert "secret" not in result.error
    assert "code=10" in result.error
    sdk[1].rollback.assert_called_once()
    sdk[1].commit.assert_not_called()
    sdk[2].close.assert_called_once()
    sdk[1].close.assert_called_once()


def test_connection_error_is_sanitized(connector, sdk):
    sdk[0].side_effect = RuntimeError("failed: secret")
    with pytest.raises(DatusDbException, match="<redacted>"):
        connector.test_connection()


@pytest.mark.parametrize(
    "rows,byte_limit,row_limit,message",
    [
        ([(1,), (2,)], 100, 1, "row budget"),
        ([("中文",)], 2, 10, "byte budget"),
    ],
)
def test_result_budget_is_an_error_not_truncation(sdk, rows, byte_limit, row_limit, message):
    connector = HanaConnector(HanaConfig(userkey="KEY", max_result_rows=row_limit, max_result_bytes=byte_limit))
    sdk[2].description = [("x",)]
    sdk[2].fetchmany.side_effect = [rows, []]
    result = connector.execute_query("SELECT x FROM t", "list")
    assert not result.success
    assert message in result.error
    sdk[1].rollback.assert_called_once()


def test_no_persistent_session_and_context_isolation(connector, sdk):
    assert connector.full_name(table_name='Mixed"Name') == '"CaseSensitive"."Mixed""Name"'
    assert connector.identifier(table_name="T") == '"CaseSensitive"."T"'
    result = connector.execute_query("SELECT * FROM t", schema_name="Other")
    assert result.success
    assert sdk[0].call_args.kwargs["currentSchema"] == "Other"
    assert connector.schema_name == "CaseSensitive"
    connector.close()
    connector.close()
    assert "secret" not in repr(connector.to_dict())
    assert connector.get_type() == "hana"


def test_native_execution_does_not_rewrite_or_require_local_ast(connector, sdk):
    sql = 'SELECT TOP 1 * FROM "CaseSensitive"."T"'
    assert connector.execute({"sql_query": sql}, "list").success
    sdk[2].execute.assert_called_once_with(sql)


def test_batch_failure_rolls_back_entire_transaction(connector, sdk):
    sdk[2].description = None
    sdk[2].rowcount = 1
    sdk[2].execute.side_effect = [None, RuntimeError("insert failed")]
    with pytest.raises(DatusDbException):
        connector.execute_queries(["INSERT INTO t VALUES (1)", "INSERT INTO t VALUES (2)"])
    sdk[1].commit.assert_not_called()
    sdk[1].rollback.assert_called_once()
    sdk[1].close.assert_called_once()


def test_write_rowcount_captured_before_cursor_close(connector, sdk):
    sdk[2].description = None
    sdk[2].rowcount = 3
    result = connector.execute_insert("INSERT INTO t VALUES (1)")
    assert result.success and result.row_count == 3


def test_metadata_binds_names_and_propagates_failures(connector, sdk):
    sdk[2].description = [
        (key,)
        for key in (
            "COLUMN_NAME",
            "DATA_TYPE_NAME",
            "LENGTH",
            "SCALE",
            "IS_NULLABLE",
            "POSITION",
            "COMMENTS",
            "DEFAULT_VALUE",
        )
    ]
    sdk[2].fetchmany.side_effect = [[("Mixed", "DECIMAL", 10, 2, "TRUE", 1, "说明", None)], []]
    assert connector.get_schema(table_name="x';DROP TABLE t") == [
        {"cid": 0, "name": "Mixed", "type": "DECIMAL(10,2)", "nullable": True, "comment": "说明", "default_value": None}
    ]
    sql, params = sdk[2].execute.call_args.args
    assert "DROP TABLE" not in sql
    assert params == ("CaseSensitive", "x';DROP TABLE t", "CaseSensitive", "x';DROP TABLE t")
    sdk[2].execute.side_effect = RuntimeError("permission denied")
    with pytest.raises(DatusDbException):
        connector.get_tables()


def test_failed_schema_switch_keeps_existing_context(connector, sdk):
    sdk[0].side_effect = RuntimeError("schema unavailable")
    with pytest.raises(DatusDbException):
        connector.switch_context(schema_name="Missing")
    assert connector.schema_name == "CaseSensitive"


def test_cleanup_errors_do_not_hide_execution_error(connector, sdk):
    sdk[2].execute.side_effect = RuntimeError("original failure")
    sdk[2].close.side_effect = RuntimeError("close cursor")
    sdk[1].close.side_effect = RuntimeError("close connection")
    result = connector.execute_query("invalid SQL")
    assert not result.success
    assert "original failure" in result.error


def test_cross_tenant_context_is_rejected_before_connect(connector, sdk):
    result = connector.execute_query("SELECT 1 FROM DUMMY", database_name="other")
    assert not result.success
    sdk[0].assert_not_called()


def test_bad_config_type():
    with pytest.raises(TypeError):
        HanaConnector(object())


def test_sdk_setup_failure_still_closes_connection(connector, sdk):
    sdk[1].setautocommit.side_effect = RuntimeError("setup failed")
    result = connector.execute_query("SELECT 1 FROM DUMMY")
    assert not result.success
    sdk[1].close.assert_called_once()


def test_context_manager_authenticates_and_closes(sdk):
    sdk[2].description = [("one",)]
    sdk[2].fetchmany.side_effect = [[(1,)], []]
    with HanaConnector({"userkey": "KEY"}) as connector:
        assert connector.get_type() == "hana"
    sdk[1].close.assert_called_once()


def test_lob_read_and_duplicate_columns(connector, sdk):
    lob = MagicMock()
    lob.read.return_value = "中文"
    sdk[2].description = [("text",)]
    sdk[2].fetchmany.side_effect = [[(lob,)], []]
    assert connector.execute_query("SELECT text FROM t", "list").sql_return == [{"text": "中文"}]
    lob.read.assert_called_once_with(connector.config.max_result_bytes + 1)
    sdk[2].description = [("id",), ("id",)]
    result = connector.execute_query("SELECT a.id, b.id FROM a,b", "list")
    assert not result.success and "Duplicate result" in result.error


def test_metadata_objects_and_samples_use_actual_quoted_names(connector, sdk):
    cursor = sdk[2]
    executed = []

    def execute(sql, parameters=None):
        executed.append((sql, parameters))
        if "SELECT DEFINITION" in sql:
            columns, rows = ["DEFINITION"], [("SELECT 1 FROM DUMMY",)]
        elif "FROM SYS.TABLE_COLUMNS" in sql:
            columns, rows = (
                [
                    "COLUMN_NAME",
                    "DATA_TYPE_NAME",
                    "LENGTH",
                    "SCALE",
                    "IS_NULLABLE",
                    "POSITION",
                    "COMMENTS",
                    "DEFAULT_VALUE",
                ],
                [("text", "NVARCHAR", 50, None, "FALSE", 1, None, None)],
            )
        elif "FROM SYS.TABLES" in sql or "FROM SYS.VIEWS" in sql:
            columns, rows = ["SCHEMA_NAME", "OBJECT_NAME"], [("CaseSensitive", "Mixed.Table")]
        elif "FROM SYS.SCHEMAS" in sql:
            columns, rows = ["SCHEMA_NAME"], [("SYS",), ("CaseSensitive",)]
        else:
            columns, rows = ["text"], [("中文",)]
        cursor.description = [(name,) for name in columns]
        cursor.fetchmany.side_effect = [rows, []]

    cursor.execute.side_effect = execute
    assert connector.get_tables() == ["Mixed.Table"]
    assert connector.get_views() == ["Mixed.Table"]
    assert connector.get_schemas() == ["CaseSensitive"]
    assert connector.get_databases() == []
    metadata = {
        "identifier": '"CaseSensitive"."Mixed.Table"',
        "catalog_name": "",
        "database_name": "",
        "schema_name": "CaseSensitive",
        "table_name": "Mixed.Table",
    }
    assert connector.get_tables_with_ddl() == [
        metadata
        | {
            "table_type": "table",
            "definition": 'CREATE TABLE "CaseSensitive"."Mixed.Table" ("text" NVARCHAR(50) NOT NULL)',
        }
    ]
    assert connector.get_views_with_ddl() == [metadata | {"table_type": "view", "definition": "SELECT 1 FROM DUMMY"}]
    samples = connector.get_sample_rows(tables=['"CaseSensitive"."Mixed.Table"'], top_n=1)
    assert samples[0]["identifier"] == '"CaseSensitive"."Mixed.Table"'
    assert samples[0]["sample_rows"] == "text\n中文\n"
    assert ('SELECT * FROM "CaseSensitive"."Mixed.Table" LIMIT 1', None) in executed
    assert len(connector.get_sample_rows(table_type="full")) == 2


def test_userstore_without_schema_resolves_sdk_current_schema(sdk):
    connector = HanaConnector({"userkey": "KEY"})
    cursor = sdk[2]
    descriptions = [[("SCHEMA_NAME",)], [("SCHEMA_NAME",), ("OBJECT_NAME",)]]
    batches = [[[("Resolved",)], []], [[("Resolved", "T")], []]]

    def execute(sql, parameters=None):
        cursor.description = descriptions.pop(0)
        cursor.fetchmany.side_effect = batches.pop(0)

    cursor.execute.side_effect = execute
    assert connector.get_tables() == ["T"]
    assert cursor.execute.call_args.args[1] == ("Resolved",)


def test_environment_config_is_strict_tls_by_default(monkeypatch):
    from datus_hana.testing import config_from_env

    for name in (
        "HANA_USERKEY",
        "HANA_DATABASE",
        "HANA_SCHEMA",
        "HANA_SSL_TRUST_STORE",
        "HANA_SSL_HOST_NAME_IN_CERTIFICATE",
        "HANA_ENCRYPT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HANA_HOST", "h")
    monkeypatch.setenv("HANA_USER", "U")
    monkeypatch.setenv("HANA_PASSWORD", "runtime")
    config = config_from_env()
    assert config.encrypt and config.ssl_validate_certificate


@pytest.mark.parametrize("include_sys", [False, True])
def test_schema_discovery_filters_reserved_component_namespace(connector, sdk, include_sys):
    schemas = ["SYS", "SYSTEM", "PUBLIC", "_SYS_BIC", "_SYS_AFL", "_SYS_AUDIT", "_SYS_RT", "_SYS_SQL_ANALYZER", "APP"]
    cursor = sdk[2]
    cursor.description = [("SCHEMA_NAME",)]
    cursor.fetchmany.side_effect = [[(name,) for name in schemas], []]

    assert connector.get_schemas(include_sys=include_sys) == (schemas if include_sys else ["APP"])
    cursor.execute.assert_called_once_with("SELECT SCHEMA_NAME FROM SYS.SCHEMAS ORDER BY SCHEMA_NAME")
