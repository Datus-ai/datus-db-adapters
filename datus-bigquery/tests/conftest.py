# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.


def pytest_configure(config):
    """Configure custom markers."""
    config.addinivalue_line("markers", "acceptance: marks tests as acceptance tests (core functionality)")
    config.addinivalue_line("markers", "integration: marks tests as integration tests (requires running database)")
