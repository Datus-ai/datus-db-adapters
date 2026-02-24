-- Generated from datus_postgresql.storage_ddl
-- Review/approve before executing.
-- Vector dimension: 384 (update if your embedding dim differs)
-- Schema: public

CREATE TABLE IF NOT EXISTS public.subject_nodes (
  namespace TEXT NOT NULL,
  node_id BIGSERIAL NOT NULL,
  parent_id BIGINT,
  name TEXT NOT NULL,
  description TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (namespace, node_id),
  UNIQUE (namespace, parent_id, name)
);

CREATE TABLE IF NOT EXISTS public.tasks (
  namespace TEXT NOT NULL,
  task_id TEXT NOT NULL,
  task_query TEXT NOT NULL,
  sql_query TEXT,
  sql_result TEXT,
  status TEXT,
  user_feedback TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (namespace, task_id)
);

CREATE TABLE IF NOT EXISTS public.feedback (
  namespace TEXT NOT NULL,
  task_id TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (namespace, task_id)
);

CREATE INDEX IF NOT EXISTS idx_subject_parent_id ON public.subject_nodes(namespace, parent_id);

CREATE INDEX IF NOT EXISTS idx_subject_parent_name ON public.subject_nodes(namespace, parent_id, name);

CREATE TABLE IF NOT EXISTS public.schema_metadata (
  namespace TEXT NOT NULL,
  identifier TEXT,
  catalog_name TEXT,
  database_name TEXT,
  schema_name TEXT,
  table_name TEXT,
  table_type TEXT,
  definition TEXT,
  vector vector(384),
  UNIQUE (namespace, identifier)
);

CREATE INDEX IF NOT EXISTS idx_schema_metadata_fts ON public.schema_metadata USING GIN (to_tsvector('simple', COALESCE(database_name, '') || ' ' || COALESCE(schema_name, '') || ' ' || COALESCE(table_name, '') || ' ' || COALESCE(definition, '')));

CREATE TABLE IF NOT EXISTS public.schema_value (
  namespace TEXT NOT NULL,
  identifier TEXT,
  catalog_name TEXT,
  database_name TEXT,
  schema_name TEXT,
  table_name TEXT,
  table_type TEXT,
  sample_rows TEXT,
  vector vector(384),
  UNIQUE (namespace, identifier)
);

CREATE INDEX IF NOT EXISTS idx_schema_value_fts ON public.schema_value USING GIN (to_tsvector('simple', COALESCE(database_name, '') || ' ' || COALESCE(schema_name, '') || ' ' || COALESCE(table_name, '') || ' ' || COALESCE(sample_rows, '')));

CREATE TABLE IF NOT EXISTS public.semantic_model (
  namespace TEXT NOT NULL,
  id TEXT,
  kind TEXT,
  name TEXT,
  fq_name TEXT,
  semantic_model_name TEXT,
  catalog_name TEXT,
  database_name TEXT,
  schema_name TEXT,
  table_name TEXT,
  description TEXT,
  vector vector(384),
  is_dimension BOOLEAN,
  is_measure BOOLEAN,
  is_entity_key BOOLEAN,
  is_deprecated BOOLEAN,
  expr TEXT,
  column_type TEXT,
  agg TEXT,
  create_metric BOOLEAN,
  agg_time_dimension TEXT,
  is_partition BOOLEAN,
  time_granularity TEXT,
  entity TEXT,
  yaml_path TEXT,
  updated_at TIMESTAMP,
  UNIQUE (namespace, id)
);

CREATE INDEX IF NOT EXISTS idx_semantic_model_fts ON public.semantic_model USING GIN (to_tsvector('simple', COALESCE(description, '') || ' ' || COALESCE(name, '') || ' ' || COALESCE(fq_name, '')));

CREATE TABLE IF NOT EXISTS public.metrics (
  namespace TEXT NOT NULL,
  name TEXT,
  subject_node_id BIGINT,
  created_at TEXT,
  id TEXT,
  semantic_model_name TEXT,
  description TEXT,
  vector vector(384),
  metric_type TEXT,
  measure_expr TEXT,
  base_measures TEXT[],
  dimensions TEXT[],
  entities TEXT[],
  catalog_name TEXT,
  database_name TEXT,
  schema_name TEXT,
  sql TEXT,
  yaml_path TEXT,
  updated_at TIMESTAMP,
  UNIQUE (namespace, id)
);

CREATE INDEX IF NOT EXISTS idx_metrics_fts ON public.metrics USING GIN (to_tsvector('simple', COALESCE(description, '') || ' ' || COALESCE(name, '')));

CREATE TABLE IF NOT EXISTS public.reference_sql (
  namespace TEXT NOT NULL,
  name TEXT,
  subject_node_id BIGINT,
  created_at TEXT,
  id TEXT,
  sql TEXT,
  comment TEXT,
  summary TEXT,
  search_text TEXT,
  filepath TEXT,
  tags TEXT,
  vector vector(384),
  UNIQUE (namespace, id)
);

CREATE INDEX IF NOT EXISTS idx_reference_sql_fts ON public.reference_sql USING GIN (to_tsvector('simple', COALESCE(sql, '') || ' ' || COALESCE(name, '') || ' ' || COALESCE(summary, '') || ' ' || COALESCE(tags, '') || ' ' || COALESCE(search_text, '')));

CREATE TABLE IF NOT EXISTS public.ext_knowledge (
  namespace TEXT NOT NULL,
  name TEXT,
  subject_node_id BIGINT,
  created_at TEXT,
  id TEXT,
  search_text TEXT,
  explanation TEXT,
  vector vector(384),
  UNIQUE (namespace, id),
  UNIQUE (namespace, subject_node_id, name)
);

CREATE INDEX IF NOT EXISTS idx_ext_knowledge_fts ON public.ext_knowledge USING GIN (to_tsvector('simple', COALESCE(search_text, '') || ' ' || COALESCE(explanation, '')));

CREATE TABLE IF NOT EXISTS public.document (
  namespace TEXT NOT NULL,
  title TEXT,
  hierarchy TEXT,
  keywords TEXT[],
  language TEXT,
  chunk_text TEXT,
  vector vector(384)
);

CREATE INDEX IF NOT EXISTS idx_document_fts ON public.document USING GIN (to_tsvector('simple', COALESCE(chunk_text, '')));
