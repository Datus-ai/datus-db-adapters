# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import re

from datus_postgresql.storage_ddl import render_vector_ddl


def test_ext_knowledge_table_contains_id_column_and_unique_id_constraint():
    ddl = render_vector_ddl(vector_dim=384, schema="public")
    match = re.search(r"CREATE TABLE IF NOT EXISTS public\.ext_knowledge \(\n(?P<body>.*?)\n\);", ddl, re.DOTALL)

    assert match is not None, "ext_knowledge table DDL not found"

    ext_knowledge_body = match.group("body")
    assert "id TEXT" in ext_knowledge_body
    assert "UNIQUE (namespace, id)" in ext_knowledge_body
    assert "UNIQUE (namespace, subject_node_id, name)" in ext_knowledge_body


def test_ext_knowledge_fts_index_is_present():
    ddl = render_vector_ddl(vector_dim=384, schema="public")
    assert "CREATE INDEX IF NOT EXISTS idx_ext_knowledge_fts ON public.ext_knowledge" in ddl
