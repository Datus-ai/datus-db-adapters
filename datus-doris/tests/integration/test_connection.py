# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest

from datus_doris import DorisConfig, DorisConnector


@pytest.mark.integration
@pytest.mark.acceptance
def test_connection_with_config(config: DorisConfig):
    conn = DorisConnector(config)
    try:
        assert conn.test_connection()
    finally:
        conn.close()


@pytest.mark.integration
def test_connection_with_dict_and_context_manager(config: DorisConfig):
    with DorisConnector(config.model_dump()) as conn:
        assert conn.test_connection()
