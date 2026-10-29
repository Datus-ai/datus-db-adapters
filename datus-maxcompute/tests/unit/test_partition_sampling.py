# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

from unittest.mock import patch

import pytest
from odps.config import option_context
from odps.errors import ODPSError
from odps.models import TableSchema
from odps.rest import RestClient
from requests import Response

from datus_maxcompute import MaxComputeConfig, MaxComputeConnector


@pytest.fixture
def two_level_sql_options():
    """Restore dynamic PyODPS settings through the SDK context after each case."""
    with option_context() as local_options:
        local_options.sql.settings = {"odps.namespace.schema": "false"}
        yield


@pytest.mark.parametrize(
    ("partition_stats", "expected_partition"),
    [
        ([(0, 0), (1024, 1)], "001"),
        ([(0, 0), (0, 0)], "002"),
        ([(None, None), (None, None)], "002"),
        ([(-1, -1), (0, 2)], "001"),
        ([(None, None)] * 100, "100"),
    ],
)
def test_partition_sampling_uses_only_listing_metadata(two_level_sql_options, partition_stats, expected_partition):
    """Real PyODPS partitions must select samples without lazy metadata requests.

    Both ``physical_size`` and missing ``size``/``record_num`` attributes can load
    remote metadata. Keep the real SDK iterator and XML parser active so plain
    object stand-ins cannot hide those requests.
    """
    entries = []
    for index, (size, record_num) in enumerate(partition_stats):
        values = f'<Column Name="pt" Value="{len(partition_stats) - index:03}"/>'
        if size is not None:
            values += f"<PartitionSize>{size}</PartitionSize>"
        if record_num is not None:
            values += f"<PartitionRecordCount>{record_num}</PartitionRecordCount>"
        entries.append(f"<Partition>{values}</Partition>")
    response = Response()
    response.status_code = 200
    response._content = (
        "<Partitions><Marker></Marker><MaxItems>100</MaxItems>" + "".join(entries) + "</Partitions>"
    ).encode()

    def get(client, url, **kwargs):
        if kwargs.get("actions") == ["partitions", "reverse"]:
            return response
        raise ODPSError("Per-partition metadata must not be fetched during sampling")

    with patch.object(RestClient, "get", autospec=True, side_effect=get) as request:
        connector = MaxComputeConnector(
            MaxComputeConfig(
                project="partition_sampling_unit",
                endpoint="https://service.example/api",
                access_key_id="id",
                access_key_secret="secret",
                namespace_mode="two_level",
            )
        )
        table = connector._odps.get_table("orders")
        table.table_schema = TableSchema.from_lists(["id"], ["bigint"], ["pt"], ["string"])
        table._loaded = True
        request.reset_mock()  # Count sampling requests after SDK bootstrap discovery.

        assert connector._sample_partition_predicate(table) == f" WHERE `pt`='{expected_partition}'"

    request.assert_called_once()
    assert request.call_args.kwargs["actions"] == ["partitions", "reverse"]
